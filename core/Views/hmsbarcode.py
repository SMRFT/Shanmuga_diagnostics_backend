from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from ..models import Hmsbarcode, HmspatientBilling
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
       
        # Process each billing record directly
        patient_data = []
        processed_bills = set()  # To avoid duplicate bills
        
        for billing in billing_records:
            try:
                # Skip if we've already processed this bill
                if billing.billnumber in processed_bills:
                    continue
                    
                processed_bills.add(billing.billnumber)
                
                # Handle testdetails - it's already a JSONField in the model
                tests = billing.testdetails
                
                # If tests is a string (legacy data), parse it as JSON
                if isinstance(tests, str):
                    try:
                        tests = json.loads(tests)
                    except json.JSONDecodeError:
                        # Skip billing records with invalid JSON in testdetails
                        continue
                
                # Ensure tests is a list
                if not isinstance(tests, list):
                    tests = []
                
                # Filter out tests that are refunded or cancelled
                valid_tests = []
                for test in tests:
                    # Check if refund or cancellation keys exist and are True
                    if not test.get('refund', False) and not test.get('cancellation', False):
                        valid_tests.append(test)
                
                # If no valid tests remain after filtering, skip this billing record entirely
                if not valid_tests:
                    continue
                
                # Create patient data directly from billing record
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
                # Log the error and continue with next record
                print(f"Error processing billing record {billing.billnumber}: {str(e)}")
                continue
        
        # Add summary information to the response
        response_data = {
            'data': patient_data,
            'summary': {
                'total_patients': len(patient_data),
                'date_range': {
                    'from': from_date,
                    'to': to_date
                },
                'total_records_processed': len(billing_records)
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