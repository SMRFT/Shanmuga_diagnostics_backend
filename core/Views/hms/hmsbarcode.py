from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from ...models import Hmsbarcode, HmspatientBilling
import json
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission


@api_view(["POST"])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def save_hms_barcodes(request):
    if request.method == "POST":
        try:
            data = request.data
            billnumber = data.get('billnumber')

            # Check if bill_no already exists
            if Hmsbarcode.objects.filter(billnumber=billnumber).exists():
                return JsonResponse({'error': 'Bill number already exists!'}, status=400)

            # Extract employee ID from request (same as your other function)
            employee_id = data.get('auth-user-id')

                    
            barcode = data.get('barcode')
            date = data.get('date')  # Date as a string
            testdetails = data.get('testdetails')

            # Convert string to date object if needed
            if date:
                try:
                    date = datetime.strptime(date, "%d/%m/%Y %H:%M").date()
                except ValueError:
                    date = datetime.strptime(date, "%d/%m/%Y").date()

            # Save patient details with created_by field
            Hmsbarcode.objects.create(
                
                date=date,                
                barcode=barcode,
                billnumber=billnumber,
                testdetails=testdetails,
                created_by=employee_id,  # Add the created_by field
            )
            return JsonResponse({'message': 'Barcodes saved successfully!'}, status=201)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
        
@api_view(["GET"])
@permission_classes([HasRoleAndDataPermission])
def get_hms_barcode_by_date(request):
    import pymongo
    from pymongo import MongoClient
    import os

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    
    # Maintain backward compatibility with single 'date' parameter
    single_date = request.GET.get('date')
    
    if single_date and not (from_date and to_date):
        # Handle legacy single date parameter
        from_date = single_date
        to_date = single_date
    elif not (from_date and to_date):
        return JsonResponse({'error': 'from_date and to_date parameters are required.'}, status=400)
    
    try:
        # Parse the provided dates
        parsed_from_date = datetime.strptime(from_date, '%Y-%m-%d')
        parsed_to_date = datetime.strptime(to_date, '%Y-%m-%d')
        
        # Validate date range
        if parsed_from_date > parsed_to_date:
            return JsonResponse({'error': 'from_date cannot be later than to_date.'}, status=400)
        
        # Create start and end datetime objects
        start_of_range = datetime.combine(parsed_from_date, datetime.min.time())  # from_date 00:00:00
        end_of_range = datetime.combine(parsed_to_date, datetime.max.time())    # to_date 23:59:59.999999
       
        # Query Billing records for the date range using date field
        billing_records = HmspatientBilling.objects.filter(
            date__gte=start_of_range, 
            date__lte=end_of_range
        ).order_by('-date')  # Order by most recent first
       
        processed_bills = set()
        patient_data = []

        # Process existing billing records
        for billing in billing_records:
            try:
                if billing.billnumber in processed_bills:
                    continue
                    
                processed_bills.add(billing.billnumber)
                
                tests = billing.testdetails
                if isinstance(tests, str):
                    try:
                        tests = json.loads(tests)
                    except json.JSONDecodeError:
                        continue
                
                if not isinstance(tests, list):
                    tests = []
                
                valid_tests = []
                for test in tests:
                    if not test.get('refund', False) and not test.get('cancellation', False):
                        valid_tests.append(test)
                
                if not valid_tests:
                    continue
                
                patient_dict = {
                    'patient_id': billing.patient_id,
                    'patientname': billing.patientname,
                    'age': billing.age,
                    'age_type': billing.age_type,
                    'gender': billing.gender,
                    'phone': billing.phone,
                    'bill_no': billing.billnumber,
                    'date': billing.date,
                    'location_id': billing.location_id,
                    'ref_doctor': billing.ref_doctor,
                    'testdetails': valid_tests,
                }
                
                patient_data.append(patient_dict)
                
            except Exception as e:
                print(f"Error processing billing record {billing.billnumber}: {str(e)}")
                continue

        # Now, try fetching from machine_hmsmi collection directly using pymongo
        try:
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            machine_collection = db.machine_hmsmi
            
            # Query Logic for machine data
            # machine_hmsmi uses 'BillDate' as date field usually
            
            machine_query = {
                "BillDate": {
                    "$gte": start_of_range,
                    "$lte": end_of_range
                }
            }
            
            machine_records = list(machine_collection.find(machine_query))
            
            # Group machine records by BillNumber
            machine_data_grouped = {}
            for rec in machine_records:
                bill_no = rec.get("BillNumber")
                if not bill_no:
                    continue
                
                if bill_no not in machine_data_grouped:
                    machine_data_grouped[bill_no] = []
                machine_data_grouped[bill_no].append(rec)
            
            # Create patient objects from machine data if not already processed
            for bill_no, records in machine_data_grouped.items():
                if bill_no in processed_bills:
                    continue # Skip if we already have it from HmspatientBilling
                
                processed_bills.add(bill_no)
                
                # Use the first record for patient info
                first_rec = records[0]
                
                # Construct testdetails list
                test_details = []
                for r in records:
                   test_details.append({
                       "testname": r.get("SubTestName") or r.get("TestName"),
                       "test_code": r.get("SubTestcode") or r.get("TestCode"),
                       "price": 0, # Placeholder
                       "status": "Pending", # Default
                       "sub_title": r.get("SubTestName", ""),
                   })
                
                patient_dict = {
                    'patient_id': first_rec.get('IPOPNumber', ''),
                    'patientname': first_rec.get('PatientName', ''),
                    'age': first_rec.get('PatientAge', ''),
                    'age_type': 'Y', # Default
                    'gender': first_rec.get('Gender', ''),
                    'phone': first_rec.get('mobilenumber', ''),
                    'bill_no': bill_no,
                    'date': first_rec.get('BillDate'),
                    'location_id': '',
                    'ref_doctor': first_rec.get('RefDoctor', ''),
                    'testdetails': test_details,
                    'source': 'machine_hmsmi'
                }
                
                patient_data.append(patient_dict)

        except Exception as e:
            print(f"Error fetching machine data: {str(e)}")
            # Don't fail the whole request, just log
        
        # Add summary information to the response
        response_data = {
            'data': patient_data,
            'summary': {
                'total_patients': len(patient_data),
                'date_range': {
                    'from': from_date,
                    'to': to_date
                },
                'total_records_processed': len(patient_data) # Approximate
            }
        }
        
        # Return the filtered patient data with summary
        return JsonResponse(response_data, safe=False)
        
    except ValueError as e:
        return JsonResponse({
            'error': f'Invalid date format. Use YYYY-MM-DD format. Details: {str(e)}'
        }, status=400)
    except Exception as e:
        # Handle any other unexpected errors
        return JsonResponse({
            'error': f'An unexpected error occurred: {str(e)}'
        }, status=500)
