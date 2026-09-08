from rest_framework.response import Response
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from django.views.decorators.csrf import csrf_exempt
import os
import re
import logging
import json
from datetime import datetime
from pymongo import MongoClient
from core.utils import get_employee_name
from ..models import BarcodeTestDetails, Patient, Billing
#auth
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_existing_barcode(request):
    patient_id = request.GET.get('patient_id')
    date = request.GET.get('date')
    bill_no = request.GET.get('bill_no')

    if not patient_id and not bill_no:
        return JsonResponse({'error': 'Either Patient ID or Bill No is required.'}, status=400)

    try:
        parsed_date = datetime.strptime(date, '%Y-%m-%d').date() if date else None
        query_filter = {}

        if patient_id:
            query_filter['patient_id'] = patient_id
        if bill_no:
            query_filter['bill_no'] = bill_no
        if parsed_date:
            query_filter['date'] = parsed_date

        barcode_record = BarcodeTestDetails.objects.filter(**query_filter).first()

        if barcode_record:
            return JsonResponse({
                'patient_id': barcode_record.patient_id,
                'patientname': barcode_record.patientname,
                'age': barcode_record.age,
                'gender': barcode_record.gender,
                'date': barcode_record.date,
                'bill_no': barcode_record.bill_no,
                'testdetails': barcode_record.testdetails,  # Ensure tests are serialized correctly
                'barcode': barcode_record.barcode
            }, status=200)

        return JsonResponse({'message': 'No barcode found for the given details.'}, status=404)

    except ValueError:
        return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)


logger = logging.getLogger(__name__)
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_max_barcode(request):
    try:
        max_barcode = 0  # Initialize the maximum barcode value
        # Retrieve all 'tests' fields from the database
        all_tests = BarcodeTestDetails.objects.values_list('testdetails', flat=True)
        for tests in all_tests:
            try:
                # Parse the tests JSON string if needed
                if isinstance(tests, str):  
                    tests = eval(tests)  # Convert string representation to a list of dicts (use json.loads if stored as JSON)

                if isinstance(tests, list):  # Ensure it's a list of test dictionaries
                    for test in tests:
                        barcode = test.get("barcode", "")
                        if barcode:
                            # Extract numeric part from the barcode
                            numeric_part = ''.join(filter(str.isdigit, barcode))
                            if numeric_part.isdigit():
                                numeric_value = int(numeric_part)
                                max_barcode = max(max_barcode, numeric_value)
            except Exception as inner_exception:
                logger.warning(f"Error processing tests: {inner_exception}")
                continue

        # Increment the max barcode value by 1
        next_barcode = max_barcode + 1

        # Format as a zero-padded 6-digit string
        formatted_next_barcode = f"{next_barcode:06d}"
        logger.debug(f"Next barcode generated: {formatted_next_barcode}")
        return JsonResponse({'next_barcode': formatted_next_barcode}, status=200)

    except Exception as e:
        logger.error(f"Error in get_max_barcode: {e}")
        return JsonResponse({'error': 'Failed to generate barcode'}, status=500)
    

@api_view(["POST"])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def save_barcodes(request):
    if request.method == "POST":
        try:
            data = request.data
            bill_no = data.get('bill_no')

            # Check if bill_no already exists
            if BarcodeTestDetails.objects.filter(bill_no=bill_no).exists():
                return JsonResponse({'error': 'Bill number already exists!'}, status=400)

            # Extract employee ID from request (same as your other function)
            employee_id = data.get('auth-user-id')

            patient_id = data.get('patient_id')
            patientname = data.get('patientname')
            segment = data.get('segment')
            age = data.get('age')
            gender = data.get('gender')            
            barcode = data.get('barcode')
            date = data.get('date')  # Date as a string
            testdetails = data.get('testdetails')
            is_emergency = data.get('is_emergency')
            patient_history = data.get('patient_history')
            sample_collector = data.get('sample_collector')

            # Convert string to date object if needed
            if date:
                try:
                    date = datetime.strptime(date, "%d/%m/%Y %H:%M").date()
                except ValueError:
                    date = datetime.strptime(date, "%d/%m/%Y").date()

            # Save patient details with created_by field
            BarcodeTestDetails.objects.create(
                patient_id=patient_id,
                patientname=patientname,
                age=age,
                segment=segment,
                gender=gender,
                date=date,                
                barcode=barcode,
                bill_no=bill_no,
                patient_history=patient_history,
                sample_collector=sample_collector,
                is_emergency=is_emergency,
                testdetails=testdetails,
                created_by=employee_id,
                  # Add the created_by field
            )
            return JsonResponse({'message': 'Barcodes saved successfully!'}, status=201)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
        
@api_view(["GET"])
@permission_classes([HasRoleAndDataPermission])
def get_barcode_by_date(request):
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
       
        # Query Billing records for the date range using bill_date
        billing_records = Billing.objects.filter(
            bill_date__gte=start_of_range, 
            bill_date__lte=end_of_range
        ).order_by('-bill_date')  # Order by most recent first
       
        # Mongo Test Master Lookup for shortcut, collection_container, etc.
        test_master_by_id = {}
        test_master_by_name = {}
        try:
            client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
            db = client.Diagnostics
            for doc in db.core_testdetails.find({}, {"test_id": 1, "test_name": 1, "shortcut": 1, "collection_container": 1, "suffix": 1, "is_servicecharge": 1, "test_code": 1}):
                if doc.get("test_id") is not None:
                    test_master_by_id[str(doc.get("test_id"))] = doc
                if doc.get("test_name"):
                    test_master_by_name[str(doc.get("test_name")).strip().lower()] = doc
        except Exception as e:
            logger.error(f"Error fetching test master for shortcuts: {e}")

        # Existing Barcode status check
        existing_bill_numbers = set(
            BarcodeTestDetails.objects.filter(
                bill_no__isnull=False
            ).values_list("bill_no", flat=True)
        )

        def is_service_charge(test_item, master_info):
            if master_info.get("is_servicecharge") is True or str(master_info.get("is_servicecharge", "")).lower() == "true":
                return True
            if str(master_info.get("test_code", "")).strip().lower() == "home collection - service charge":
                return True
            m_name = str(master_info.get("test_name") or "").strip().lower()
            if "service charge" in m_name:
                return True
            if test_item.get("is_servicecharge") is True or str(test_item.get("is_servicecharge", "")).lower() == "true":
                return True
            t_name = str(test_item.get("test_name") or test_item.get("testname") or "").strip().lower()
            if "service charge" in t_name:
                return True
            return False

        # Process each billing record and get corresponding patient details
        patient_data = []
        processed_patients = set()  # To avoid duplicate patients with multiple bills
        
        for billing in billing_records:
            try:
                # Create a unique key for patient to avoid duplicates
                patient_key = (billing.patient_id, billing.bill_no)
                
                if patient_key in processed_patients:
                    continue
                    
                processed_patients.add(patient_key)
                
                # Get the patient details using patient_id from billing
                patient = Patient.objects.get(patient_id=billing.patient_id)
                
                # Handle testdetails from billing
                tests = billing.testdetails
                # print("test",tests)
                # If tests is a string, parse it as JSON
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
                        # Ensure dictionary object
                        test_item = dict(test) if isinstance(test, dict) else {'test_name': str(test)}
                        tid = str(test_item.get('test_id', ''))
                        tname = str(test_item.get('test_name') or test_item.get('testname') or '').strip().lower()
                        master_info = test_master_by_id.get(tid) or test_master_by_name.get(tname) or {}

                        if not test_item.get('shortcut'):
                            test_item['shortcut'] = master_info.get('shortcut', '')
                        if not test_item.get('collection_container'):
                            test_item['collection_container'] = master_info.get('collection_container', '')
                        if not test_item.get('suffix'):
                            test_item['suffix'] = master_info.get('suffix', '')
                        if master_info.get('is_servicecharge') is not None and 'is_servicecharge' not in test_item:
                            test_item['is_servicecharge'] = master_info.get('is_servicecharge')

                        valid_tests.append(test_item)
                
                # If no valid tests remain after filtering, skip this billing record entirely
                if not valid_tests:
                    continue

                # If only service charges are billed, don't display in barcode list
                has_clinical_tests = False
                for test_item in valid_tests:
                    tid = str(test_item.get('test_id', ''))
                    tname = str(test_item.get('test_name') or test_item.get('testname') or '').strip().lower()
                    master_info = test_master_by_id.get(tid) or test_master_by_name.get(tname) or {}
                    if not is_service_charge(test_item, master_info):
                        has_clinical_tests = True
                        break

                if not has_clinical_tests:
                    continue
                
                barcode_status = "Generated" if billing.bill_no in existing_bill_numbers else "Pending"

                # Create a combined data structure with patient and billing info
                patient_dict = {
                    'patient_id': patient.patient_id,
                    'patientname': patient.patientname,
                    'age': patient.age,
                    'age_type': patient.age_type,
                    'gender': patient.gender,
                    'phone': patient.phone,
                    'email': patient.email,
                    'address': patient.address,
                    'bill_no': billing.bill_no,
                    'date': billing.bill_date,  # Using bill_date for consistency
                    'lab_id': billing.lab_id,
                    'segment': billing.segment,
                    'B2B': billing.B2B,
                    'salesMapping': billing.salesMapping,
                    'sample_collector': get_employee_name(billing.sample_collector) if billing.sample_collector else billing.sample_collector,
                    'refby': billing.refby,
                    'branch': billing.branch,
                    'status': billing.status,
                    'barcode_status': barcode_status,
                    'totalAmount': billing.totalAmount,
                    'discount': billing.discount,
                    'payment_method': billing.payment_method,
                    'credit_amount': billing.credit_amount,
                    'testdetails': valid_tests,
                    'is_emergency': billing.is_emergency,
                    'patient_history': billing.patient_history,
                }
                
                # Recalculate total amount based on valid tests only
                total_amount = sum(float(test.get('amount', 0)) for test in valid_tests)
                patient_dict['totalAmount'] = str(total_amount)
                
                patient_data.append(patient_dict)
                
            except Patient.DoesNotExist:
                # Skip billing records where patient doesn't exist
                continue
            except Exception as e:
                # Log the error and continue with next record
                print(f"Error processing billing record {billing.bill_no}: {str(e)}")
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


@api_view(["GET"])
@permission_classes([HasRoleAndDataPermission])
def check_barcode(request):
    patient_id = request.GET.get('patient_id')
    date = request.GET.get('date')
    if BarcodeTestDetails.objects.filter(patient_id=patient_id, date=date).exists():
        return JsonResponse({"exists": True})
    return JsonResponse({"exists": False})