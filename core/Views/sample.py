from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.forms.models import model_to_dict
import json
from datetime import timedelta
from datetime import datetime
import os
from pymongo import MongoClient
#models
from ..models import SampleStatus,Billing, TestValue
from ..models import BarcodeTestDetails
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv
from django.utils.timezone import make_aware
from core.utils import get_employee_name

load_dotenv()

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_samplepatients_by_date(request):
    """
    Get sample patients from BarcodeTestDetails who have tests that are either not in SampleStatus
    or have Pending status in SampleStatus. Enriches test details from core_testdetails MongoDB collection.
    """
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    single_date = request.GET.get('date')
    
    if not from_date and not single_date:
        return JsonResponse({'error': 'from_date or date parameter is required.'}, status=400)
    
    try:
        if single_date:
            parsed_date = datetime.fromisoformat(single_date)
            from_date_parsed = parsed_date
            to_date_parsed = parsed_date
        else:
            from_date_parsed = datetime.fromisoformat(from_date)
            to_date_parsed = datetime.fromisoformat(to_date) if to_date else from_date_parsed
        
        if to_date_parsed.time() == datetime.min.time():
            to_date_parsed = to_date_parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Get SampleStatus records for the date range
        sample_status_records = SampleStatus.objects.filter(
            date__gte=from_date_parsed, 
            date__lte=to_date_parsed + timedelta(days=1)
        ).values_list('patient_id', 'barcode', 'testdetails')
        
        # Track test status by patient_id, barcode, and test_id
        completed_samples = set()
        pending_samples = set()
        
        for patient_id, barcode, testdetails in sample_status_records:
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails)
                except json.JSONDecodeError:
                    continue
            
            for test in testdetails:
                # Use test_id instead of testname for more accurate tracking
                test_key = (patient_id, barcode, test.get('test_id', ''))
                if test.get('samplestatus') in ["Pending", "Rejected"]:
                    pending_samples.add(test_key)
                else:
                    # Any other status (Sample Collected, Received, etc.) is considered completed
                    completed_samples.add(test_key)
        
        # Get patients from BarcodeTestDetails
        patients = BarcodeTestDetails.objects.filter(
            date__gte=from_date_parsed, 
            date__lte=to_date_parsed + timedelta(days=1)
        ).order_by('-date', 'patient_id')
        
        # Connect to MongoDB to get test details from core_testdetails
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        test_details_collection = db.core_testdetails
        
        filtered_patients = []
        for patient in patients:
            patient_tests = patient.testdetails
            if isinstance(patient_tests, str):
                try:
                    patient_tests = json.loads(patient_tests)
                except json.JSONDecodeError:
                    continue
            
            if not isinstance(patient_tests, list):
                continue
            
            # Filter tests to include only pending or not-in-status tests
            enriched_testdetails = []
            
            for test in patient_tests:
                test_id = test.get('test_id')
                test_key = (patient.patient_id, patient.barcode, test_id)
                
                # Include test only if it's pending or not in any status
                if (test_key not in completed_samples and test_key not in pending_samples) or \
                   (test_key in pending_samples):
                    
                    if test_id:
                        # Query core_testdetails by test_id
                        test_detail = test_details_collection.find_one(
                            {"test_id": test_id},
                            {
                                "_id": 0,
                                "test_id": 1,
                                "test_name": 1,
                                "department": 1,
                                "collection_container": 1,
                                "parameters": 1,
                                "device_id": 1
                            }
                        )
                        
                        if test_detail:
                            enriched_test = {
                                **test,
                                'barcode': patient.barcode,
                                'testname': test_detail.get('test_name', 'N/A'),
                                'test_name': test_detail.get('test_name', 'N/A'),
                                'department': test_detail.get('department', 'N/A'),
                                'collection_container': test_detail.get('collection_container', 'N/A')
                            }
                            enriched_testdetails.append(enriched_test)
            
            # Only include patient if they have at least one pending/not-in-status test
            if enriched_testdetails:
                patient_dict = model_to_dict(patient)
                
                if patient_dict.get('date'):
                    patient_dict['date'] = patient_dict['date'].isoformat()
                
                # Replace testdetails with enriched version
                patient_dict['testdetails'] = enriched_testdetails
                
                # Get billing records with bill_no for this specific patient
                billing_record = Billing.objects.filter(
                    patient_id=patient.patient_id,
                    date__gte=from_date_parsed,
                    date__lte=to_date_parsed + timedelta(days=1)
                ).values('bill_no', 'sample_collector', 'patient_id', 'branch', 'B2B', 'payment_method').first()
                
                # Get sample collector
                sample_collector = ''
                if billing_record:
                    sample_collector = billing_record.get('sample_collector', '')
                
                patient_dict['sample_collector'] = get_employee_name(sample_collector) if sample_collector else ''
                
                # Get branch (processing location)
                branch = ''
                if billing_record:
                    branch = billing_record.get('branch', '')
                
                patient_dict['branch'] = branch if branch else ''
                
                # Get B2B
                B2B = ''
                if billing_record:
                    B2B = billing_record.get('B2B', '')
                
                patient_dict['B2B'] = B2B if B2B else ''
                
                # Get payment method
                payment_method = None
                if billing_record:
                    payment_method = billing_record.get('payment_method')
                
                # Parse payment_method if it's a string
                if payment_method and isinstance(payment_method, str):
                    try:
                        payment_method = json.loads(payment_method)
                    except json.JSONDecodeError:
                        payment_method = None
                
                patient_dict['payment_method'] = payment_method if payment_method else {}
                
                filtered_patients.append(patient_dict)
        
        return JsonResponse({
            'data': filtered_patients,
            'total_count': len(filtered_patients),
            'date_range': {
                'from_date': from_date_parsed.isoformat(),
                'to_date': to_date_parsed.isoformat()
            }
        }, safe=False)
        
    except ValueError as e:
        return JsonResponse({
            'error': f'Invalid date format. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS. Error: {str(e)}'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': f'An error occurred: {str(e)}'
        }, status=500)
@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def sample_status(request):
    if request.method == 'POST':
        try:
            # Handle different data formats
            if hasattr(request, 'data'):
                data = request.data
            else:
                data = json.loads(request.body)
            
            print(f"Request Data: {data}")  # For debugging
            
            # Extract patient data
            employee_id = data.get('auth-user-id')
            patient_id = data.get('patient_id')           
            date = data.get('date')          
            barcode = data.get('barcode')           
            testdetails = data.get('testdetails', [])
            
            # Validate required fields
            required_fields = ['patient_id', 'barcode']
            missing_fields = []
            for field in required_fields:
                if not data.get(field):
                    missing_fields.append(field)
            
            if missing_fields:
                return JsonResponse({
                    'error': f'Missing required fields: {", ".join(missing_fields)}'
                }, status=400)
            
            # Check if an entry with the same patient_id, barcode, and date exists
            existing_entry = SampleStatus.objects.filter(
                patient_id=patient_id,
                barcode=barcode,
                date=date
            ).first()
            
            if existing_entry:
                return JsonResponse({
                    'error': 'Please change status as Sample Collected',
                    'message': 'Patient ID already exists. Please change status as Sample Collected'
                }, status=409)
            
            # Process and validate testdetails
            processed_testdetails = []
            for test in testdetails:
                # Handle datetime formatting if needed
                samplecollected_time = test.get('samplecollected_time')
                received_time = test.get('received_time')
                rejected_time = test.get('rejected_time')
                
                processed_test = {
                    'test_id': test.get('test_id'),
                    'samplecollector': test.get('samplecollector', 'N/A'),
                    'samplestatus': test.get('samplestatus', 'Pending'),
                    'samplecollected_time': samplecollected_time,
                    'received_time': received_time,
                    'rejected_time': rejected_time,
                    'collectd_by': test.get('collectd_by'),
                    'received_by': test.get('received_by'),
                    'rejected_by': test.get('rejected_by'),
                    'remarks': test.get('remarks'),
                }
                processed_testdetails.append(processed_test)
            
            # Save the new entry
            sample_status = SampleStatus(
                patient_id=patient_id,               
                date=date,               
                barcode=barcode,             
                created_by=employee_id,             
                testdetails=processed_testdetails
            )
            sample_status.save()
            
            return JsonResponse({
                'message': 'Data saved successfully',
                'saved_data': {
                    'patient_id': patient_id,                   
                    'barcode': barcode,
                    'testdetails_count': len(processed_testdetails),
                    'testdetails': processed_testdetails
                }
            }, status=201)
            
        except KeyError as e:
            return JsonResponse({'error': f'Missing key: {str(e)}'}, status=400)
        except Exception as e:
            print(f"Error saving sample status: {str(e)}")  # For debugging
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)


@api_view(['PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def patch_sample_status(request, barcode):
    from django.utils import timezone
    
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_samplestatus
    
    if request.method == 'PATCH':
        try:
            if hasattr(request, 'data'):
                data = request.data
            else:
                data = json.loads(request.body)
            
            employee_id = data.get('auth-user-id')
            
            if 'testdetails' not in data:
                return JsonResponse({'error': 'testdetails field is required'}, status=400)
            
            updates = data['testdetails']
            
            if not isinstance(updates, list):
                return JsonResponse({'error': 'testdetails must be an array'}, status=400)

            patient_doc = collection.find_one({"barcode": barcode})
            if not patient_doc:
                return JsonResponse({'error': 'No patient found with the given barcode'}, status=404)

            testdetails = patient_doc.get('testdetails', [])
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails)
                except json.JSONDecodeError:
                    return JsonResponse({'error': 'Invalid testdetails format'}, status=400)

            if not isinstance(testdetails, list):
                return JsonResponse({'error': 'testdetails is not in the correct format'}, status=400)

            # Build a lookup of updates by test_id
            updates_map = {u.get('test_id'): u for u in updates if u.get('test_id') is not None}

            updated_count = 0

            # ✅ Only touch tests whose test_id is in the request
            for existing_test in testdetails:
                if not isinstance(existing_test, dict):
                    continue

                test_id = existing_test.get('test_id')

                # Skip tests NOT in the request — leave them completely untouched
                if test_id not in updates_map:
                    continue

                update = updates_map[test_id]
                new_status = update.get('samplestatus')
                current_status = existing_test.get('samplestatus')

                if new_status == current_status:
                    continue

                ist_time = timezone.now().astimezone(timezone.get_current_timezone())
                formatted_time = ist_time.strftime('%Y-%m-%d %H:%M:%S')

                if new_status == 'Sample Collected':
                    existing_test['samplestatus'] = new_status
                    existing_test['samplecollected_time'] = formatted_time
                    existing_test['collectd_by'] = update.get('collectd_by')
                    updated_count += 1
                elif new_status == 'Pending':
                    existing_test['samplestatus'] = new_status
                    existing_test['samplecollected_time'] = None
                    existing_test['collectd_by'] = None
                    updated_count += 1
                else:
                    existing_test['samplestatus'] = new_status
                    updated_count += 1

            if updated_count == 0:
                return JsonResponse({'error': 'No changes were made'}, status=400)

            update_data = {
                "testdetails": json.dumps(testdetails),
                "lastmodified_date": timezone.now()
            }
            if employee_id:
                update_data["lastmodified_by"] = employee_id

            result = collection.update_one(
                {"_id": patient_doc['_id']},
                {"$set": update_data}
            )

            if result.modified_count > 0:
                return JsonResponse({
                    'message': f'Successfully updated {updated_count} tests for barcode {barcode}',
                    'updated_count': updated_count
                }, status=200)
            else:
                return JsonResponse({'error': 'No changes were made'}, status=400)

        except KeyError as e:
            return JsonResponse({'error': f'Missing required field: {str(e)}'}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_sample_collected(request):
    """
    Get sample collected patients from SampleStatus.
    Fetches patient details directly from BarcodeTestDetails and Billing models,
    and enriches test details from core_testdetails MongoDB collection.
    """
    if request.method == "GET":
        try:
            # Get date parameters from query string
            from_date = request.GET.get('from_date')
            to_date = request.GET.get('to_date')
            
            # Start with all samples
            samples_query = SampleStatus.objects.all()
            
            # Apply date filtering if parameters are provided
            if from_date:
                try:
                    from_date_obj = datetime.strptime(from_date, '%Y-%m-%d')
                    start_of_day = make_aware(datetime.combine(from_date_obj, datetime.min.time()))
                    samples_query = samples_query.filter(date__gte=start_of_day)
                except ValueError:
                    return JsonResponse({"error": "Invalid from_date format. Use YYYY-MM-DD"}, status=400)

            if to_date:
                try:
                    to_date_obj = datetime.strptime(to_date, '%Y-%m-%d')
                    end_of_day = make_aware(datetime.combine(to_date_obj, datetime.max.time()))
                    samples_query = samples_query.filter(date__lte=end_of_day)
                except ValueError:
                    return JsonResponse({"error": "Invalid to_date format. Use YYYY-MM-DD"}, status=400)
            
            # Fetch filtered samples
            samples = samples_query
            
            # Connect to MongoDB to get test details from core_testdetails
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            test_details_collection = db.core_testdetails
            
            patient_data = {}
            
            # Prepare the data grouped by patient
            for sample in samples:
                # Deserialize testdetails if it's a string
                if isinstance(sample.testdetails, str):
                    test_details = json.loads(sample.testdetails)
                else:
                    test_details = sample.testdetails
                
                # Filter test details based on samplestatus only
                for detail in test_details:
                    if detail.get("samplestatus") == "Sample Collected":
                        # Create unique key for patient
                        patient_key = sample.barcode
                        
                        # If patient is not already in the dictionary, fetch from BarcodeTestDetails and Billing
                        if patient_key not in patient_data:
                            try:
                                # Find patient by barcode in BarcodeTestDetails model
                                barcode_details = BarcodeTestDetails.objects.get(barcode=sample.barcode)
                                
                                # Get billing details if bill_no exists
                                billing_details = None
                                payment_method_data = {}
                                
                                if barcode_details.bill_no:
                                    try:
                                        billing_details = Billing.objects.get(bill_no=barcode_details.bill_no)
                                        
                                        # Parse payment_method if it's a string
                                        if billing_details.payment_method:
                                            if isinstance(billing_details.payment_method, str):
                                                payment_method_data = json.loads(billing_details.payment_method)
                                            else:
                                                payment_method_data = billing_details.payment_method
                                    except Billing.DoesNotExist:
                                        pass
                                
                                patient_data[patient_key] = {
                                    "date": sample.date,
                                    "patient_id": barcode_details.patient_id or '',
                                    "patientname": barcode_details.patientname or '',
                                    "barcode": barcode_details.barcode or '',
                                    "age": barcode_details.age or 0,
                                    "gender": barcode_details.gender or '',
                                    "segment": barcode_details.segment or '',
                                    "is_emergency": barcode_details.is_emergency if hasattr(barcode_details, 'is_emergency') else False,
                                    "bill_no": barcode_details.bill_no or "N/A",
                                    "B2B": billing_details.B2B if billing_details else "N/A",
                                    "branch": billing_details.branch if billing_details else "N/A",
                                    "payment_method": payment_method_data.get("paymentmethod", "N/A") if payment_method_data else "N/A",
                                    "testdetails": []
                                }
                            except BarcodeTestDetails.DoesNotExist:
                                # Fallback if barcode not found in BarcodeTestDetails
                                patient_data[patient_key] = {
                                    "date": sample.date,
                                    "patient_id": sample.patient_id or '',
                                    "patientname": "",
                                    "barcode": sample.barcode,
                                    "age": 0,
                                    "gender": "",
                                    "segment": "",
                                    "is_emergency": False,
                                    "bill_no": "N/A",
                                    "B2B": "N/A",
                                    "branch": "N/A",
                                    "payment_method": "N/A",
                                    "testdetails": []
                                }
                        
                        # Get enriched test details from MongoDB core_testdetails
                        test_id = detail.get("test_id")
                        enriched_test = None
                        
                        if test_id:
                            # Query core_testdetails by test_id
                            test_detail = test_details_collection.find_one(
                                {"test_id": test_id},
                                {
                                    "_id": 0,
                                    "test_id": 1,
                                    "test_name": 1,
                                    "department": 1,
                                    "collection_container": 1,
                                    "parameters": 1,
                                    "device_id": 1
                                }
                            )
                            
                            if test_detail:
                                enriched_test = {
                                    "test_id": test_id,
                                    "testname": test_detail.get('test_name', "N/A"),
                                    "test_name": test_detail.get('test_name', "N/A"),
                                    "container": test_detail.get('collection_container', "N/A"),
                                    "department": test_detail.get('department', "N/A"),
                                    "samplecollector": detail.get("samplecollector", "N/A"),
                                    "collectd_by": detail.get("collectd_by", "N/A"),
                                    "samplestatus": detail.get("samplestatus", "N/A"),
                                    "samplecollected_time": detail.get("samplecollected_time", "N/A"),
                                }
                        
                        # Only append if enriched test exists
                        if enriched_test:
                            patient_data[patient_key]["testdetails"].append(enriched_test)
            
            # Convert the dictionary to a list
            data = list(patient_data.values())
            
            # Return the filtered data as a response
            return JsonResponse({
                "data": data,
                "filters_applied": {
                    "from_date": from_date,
                    "to_date": to_date,
                    "total_records": len(data)
                }
            }, safe=False)
            
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
 
        
@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_outsource_labs(request):
    """Fetch all active outsource labs"""
    if request.method == "GET":
        try:
            # MongoDB connection setup
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            collection = db.outsource_lab
            
            # Fetch only active labs
            labs = list(collection.find(
                {"is_active": True},
                {"_id": 0, "labID": 1, "labName": 1}
            ))
            
            return JsonResponse({
                "success": True,
                "data": labs
            }, safe=False)
            
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@api_view(['PUT'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def update_sample_collected(request, barcode):
    # MongoDB connection setup
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_samplestatus
    
    if request.method == "PUT":
        try:
            if hasattr(request, 'data'):
                body = request.data
            else:
                body = json.loads(request.body)
            
            # Extract barcode and samplecollected_time for validation
            barcode = body.get("barcode")
            updates = body.get("updates", [])
            
            if not updates:
                return JsonResponse({"error": "Updates are required"}, status=400)
            
            # Find the patient sample record by patient_id and barcode
            patient_sample = collection.find_one({               
                "barcode": barcode
            })
            
            if not patient_sample:
                return JsonResponse({"error": "Sample not found"}, status=404)
            
            # Parse testdetails as a Python list
            testdetails = json.loads(patient_sample.get('testdetails', '[]'))
            
            # Import the proper Django timezone module
            from django.utils import timezone
            import pytz
            
            # Configure IST timezone
            ist_timezone = pytz.timezone('Asia/Kolkata')
            
            # Apply updates based on test_id
            for update in updates:
                test_id = update.get("test_id")
                new_status = update.get("samplestatus")
                received_by = update.get("received_by")
                rejected_by = update.get("rejected_by")
                outsourced_by = update.get("outsourced_by")
                outsource_lab = update.get("outsource_lab")  # New field
                remarks = update.get("remarks")
                
                if test_id is None or new_status is None:
                    return JsonResponse({
                        "error": "samplestatus and test_id are required"
                    }, status=400)
                
                # Find the test entry by test_id and samplecollected_time
                test_found = False
                for test_entry in testdetails:
                    if (test_entry.get('test_id') == test_id):
                        
                        # Update the sample status
                        test_entry['samplestatus'] = new_status
                        
                        # Get current time in IST timezone
                        current_time = timezone.now().astimezone(ist_timezone)
                        formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S')
                        
                        if new_status == "Received":
                            test_entry['received_time'] = formatted_time
                            test_entry['received_by'] = received_by
                            # Clear rejection/outsource fields if they exist
                            test_entry.pop('rejected_time', None)
                            test_entry.pop('rejected_by', None)
                            test_entry.pop('remarks', None)
                            test_entry.pop('outsourced_time', None)
                            test_entry.pop('outsourced_by', None)
                            test_entry.pop('outsource_lab', None)
                            
                        elif new_status == "Rejected":
                            test_entry['rejected_time'] = formatted_time
                            test_entry['rejected_by'] = rejected_by
                            test_entry['remarks'] = remarks
                            # Clear received/outsource fields if they exist
                            test_entry.pop('received_time', None)
                            test_entry.pop('received_by', None)
                            test_entry.pop('outsourced_time', None)
                            test_entry.pop('outsourced_by', None)
                            test_entry.pop('outsource_lab', None)
                            
                        elif new_status == "Outsource":
                            test_entry['outsourced_time'] = formatted_time
                            test_entry['outsourced_by'] = outsourced_by
                            test_entry['outsource_lab'] = outsource_lab  # Store lab name
                            # Clear received/rejection fields if they exist
                            test_entry.pop('received_time', None)
                            test_entry.pop('received_by', None)
                            test_entry.pop('rejected_time', None)
                            test_entry.pop('rejected_by', None)
                            test_entry.pop('remarks', None)
                        
                        test_found = True
                        break
                
                if not test_found:
                    return JsonResponse({
                        "error": f"Test with id {test_id} not found"
                    }, status=404)
            
            # Save changes back to the database
            collection.update_one(
                {"barcode": barcode},
                {"$set": {"testdetails": json.dumps(testdetails)}}
            )
            
            return JsonResponse({
                "message": "Sample status updated successfully",
                "updated_tests": len(updates)
            }, status=200)
            
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
        

@api_view(['GET'])       
@permission_classes([ HasRoleAndDataPermission])
def get_received_samples(request):
    # Get patient_id and date from the query parameters
    patient_id = request.GET.get('patient_id')
    date_str = request.GET.get('date')

    if not patient_id or not date_str:
        return JsonResponse({'error': 'Missing patient_id or date parameter'}, status=400)

    try:
        # Fetch SampleStatus entries for the given patient_id
        received_samples = SampleStatus.objects.filter(patient_id=patient_id)

        test_data = []
        for sample in received_samples:
            # Check if testdetails is a string or a list
            if isinstance(sample.testdetails, str):
                try:
                    test_list = json.loads(sample.testdetails)  # Parse JSON string
                except json.JSONDecodeError:
                    return JsonResponse({'error': 'Invalid testdetails format'}, status=400)
            elif isinstance(sample.testdetails, list):
                test_list = sample.testdetails  # Use as-is
            else:
                continue  # Skip invalid testdetails format

            for test_item in test_list:
                testname = test_item.get('testname')
                samplestatus = test_item.get('samplestatus')

                # Only include tests with 'Received' status and matching date
                if samplestatus == 'Received' and str(sample.date) == date_str:
                    test_info = {
                        "patient_id": sample.patient_id,
                        "patientname": sample.patientname,
                        "testname": testname,
                        "samplestatus": samplestatus,
                        "date": sample.date,
                        "segment": sample.segment
                    }
                    test_data.append(test_info)

        return JsonResponse({'data': test_data})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)       


@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_rejected_samples(request):
    """
    Get all test details with 'Rejected' status.
    Supports optional date filtering by 'from_date' and 'to_date' sent in the payload.
    """
    if request.method == "POST":
        try:
            # Handle parameters in POST body
            data = request.data
            from_date = data.get('from_date')
            to_date = data.get('to_date')

            samples_query = SampleStatus.objects.all()

            if from_date:
                try:
                    from_date_obj = datetime.strptime(from_date, '%Y-%m-%d')
                    start_of_day = make_aware(datetime.combine(from_date_obj, datetime.min.time()))
                    samples_query = samples_query.filter(date__gte=start_of_day)
                except ValueError:
                    return JsonResponse({"error": "Invalid from_date format. Use YYYY-MM-DD"}, status=400)

            if to_date:
                try:
                    to_date_obj = datetime.strptime(to_date, '%Y-%m-%d')
                    end_of_day = make_aware(datetime.combine(to_date_obj, datetime.max.time()))
                    samples_query = samples_query.filter(date__lte=end_of_day)
                except ValueError:
                    return JsonResponse({"error": "Invalid to_date format. Use YYYY-MM-DD"}, status=400)

            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            test_details_collection = db.core_testdetails
            test_id_cache = {}

            rejected_data = []

            for sample in samples_query:
                # Deserialize testdetails if needed
                if isinstance(sample.testdetails, str):
                    try:
                        test_details = json.loads(sample.testdetails)
                    except json.JSONDecodeError:
                        continue
                else:
                    test_details = sample.testdetails

                if not isinstance(test_details, list):
                    continue

                for detail in test_details:
                    if detail.get("samplestatus") == "Rejected":
                        test_id = detail.get("test_id")
                        testname = detail.get("testname", "N/A")
                        container = detail.get("container", "N/A")
                        
                        if test_id and (testname == "N/A" or container == "N/A"):
                            if test_id not in test_id_cache:
                                td_doc = test_details_collection.find_one({"test_id": test_id}, {"test_name": 1, "collection_container": 1})
                                if td_doc:
                                    test_id_cache[test_id] = {
                                        "name": td_doc.get("test_name", "N/A"),
                                        "container": td_doc.get("collection_container", "N/A")
                                    }
                                else:
                                    test_id_cache[test_id] = {"name": testname, "container": container}
                                    
                            cached = test_id_cache[test_id]
                            testname = cached["name"] if cached["name"] != "N/A" else testname
                            container = cached["container"] if cached["container"] != "N/A" else container

                        rejected_data.append({
                            "id": str(sample.id) if hasattr(sample, 'id') else str(sample.pk),
                            "date": sample.date.isoformat() if sample.date else None,
                            "patient_id": sample.patient_id,
                            "barcode": sample.barcode,
                            "testname": testname,
                            "container": container,
                            "samplecollector": detail.get("samplecollector", "N/A"),
                            "samplestatus": detail.get("samplestatus", "N/A"),
                            "rejected_time": detail.get("rejected_time", "N/A"),
                            "rejected_by": detail.get("rejected_by", "N/A"),
                            "remarks": detail.get("remarks", "N/A"),
                        })
            
            # Sort by rejected_time descending (newest first)
            rejected_data.sort(key=lambda x: x.get('rejected_time') or '', reverse=True)

            return JsonResponse({
                "data": rejected_data,
                "count": len(rejected_data)
            }, safe=False)

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)       


@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_outsourced_samples(request):
    """
    Get all test details with 'Outsourced' status and include TAT value from TestValue.
    TAT = Approve Time (TestValue) - Outsource Time (SampleStatus).
    Params are expected in the POST body.
    """
    if request.method == "POST":
        try:
            # Handle parameters in POST body
            data = request.data
            from_date = data.get('from_date')
            to_date = data.get('to_date')

            samples_query = SampleStatus.objects.all()

            if from_date:
                try:
                    from_date_obj = datetime.strptime(from_date, '%Y-%m-%d')
                    start_of_day = make_aware(datetime.combine(from_date_obj, datetime.min.time()))
                    samples_query = samples_query.filter(date__gte=start_of_day)
                except ValueError:
                    return JsonResponse({"error": "Invalid from_date format. Use YYYY-MM-DD"}, status=400)

            if to_date:
                try:
                    to_date_obj = datetime.strptime(to_date, '%Y-%m-%d')
                    end_of_day = make_aware(datetime.combine(to_date_obj, datetime.max.time()))
                    samples_query = samples_query.filter(date__lte=end_of_day)
                except ValueError:
                    return JsonResponse({"error": "Invalid to_date format. Use YYYY-MM-DD"}, status=400)

            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            test_details_collection = db.core_testdetails
            test_id_cache = {}

            # First pass: Collect outsourced candidates
            temp_outsourced = []
            barcodes = set()

            for sample in samples_query:
                # Deserialize testdetails if needed
                if isinstance(sample.testdetails, str):
                    try:
                        test_details = json.loads(sample.testdetails)
                    except json.JSONDecodeError:
                        continue
                else:
                    test_details = sample.testdetails

                if not isinstance(test_details, list):
                    continue

                for detail in test_details:
                    if detail.get("samplestatus") == "Outsource":
                        test_id = detail.get("test_id")
                        testname = detail.get("testname", "N/A")
                        container = detail.get("container", "N/A")
                        
                        if test_id and (testname == "N/A" or container == "N/A"):
                            if test_id not in test_id_cache:
                                td_doc = test_details_collection.find_one({"test_id": test_id}, {"test_name": 1, "collection_container": 1})
                                if td_doc:
                                    test_id_cache[test_id] = {
                                        "name": td_doc.get("test_name", "N/A"),
                                        "container": td_doc.get("collection_container", "N/A")
                                    }
                                else:
                                    test_id_cache[test_id] = {"name": testname, "container": container}
                                    
                            cached = test_id_cache[test_id]
                            testname = cached["name"] if cached["name"] != "N/A" else testname
                            container = cached["container"] if cached["container"] != "N/A" else container

                        item = {
                            "id": str(sample.id) if hasattr(sample, 'id') else str(sample.pk),
                            "date": sample.date.isoformat() if sample.date else None,
                            "patient_id": sample.patient_id,
                            "barcode": sample.barcode,
                            "testname": testname,
                            "container": container,
                            "samplecollector": detail.get("samplecollector", "N/A"),
                            "samplestatus": detail.get("samplestatus", "N/A"),
                            "outsourced_to": detail.get("outsource_lab", "N/A"), 
                            "outsourced_time": detail.get("outsourced_time", "N/A"),
                            "outsourced_by": detail.get("outsourced_by", "N/A"),
                            "remarks": detail.get("remarks", "N/A"),
                        }
                        temp_outsourced.append(item)
                        if sample.barcode:
                            barcodes.add(sample.barcode)
            
            # Fetch TestValue records for barcodes
            test_value_map = {}
            if barcodes:
                tv_records = TestValue.objects.filter(barcode__in=list(barcodes)).values('barcode', 'testdetails')
                for tv in tv_records:
                    bc = tv['barcode']
                    # Parse testdetails if string
                    td = tv['testdetails']
                    if isinstance(td, str):
                        try:
                            td = json.loads(td)
                        except:
                            td = []
                    
                    if not isinstance(td, list):
                        td = []

                    if bc in test_value_map:
                        test_value_map[bc].extend(td)
                    else:
                        test_value_map[bc] = td
            
            # Second pass: Match and Calculate TAT
            outsourced_data = []
            for item in temp_outsourced:
                res_time = "N/A"
                dispatch_status = "N/A"
                dispatch_time = "N/A"
                tat = "N/A"
                
                # Find matching test in TestValue details
                barcode = item['barcode']
                testname = item['testname']
                out_time_str = item['outsourced_time']
                
                if barcode in test_value_map:
                    tv_details = test_value_map[barcode]
                    # Find specific test result with robust matching
                    target_name = testname.strip().lower() if testname else ""
                    
                    matching_res = None
                    for t in tv_details:
                        current_name = t.get('testname', '').strip().lower()
                        if current_name == target_name:
                            matching_res = t
                            break
                    
                    if matching_res:
                         res_time = matching_res.get('approve_time', "N/A")
                         dispatch_status = matching_res.get('dispatch', False)
                         dispatch_time = matching_res.get('dispatch_time', "N/A")
                
                item['approve_time'] = res_time
                item['dispatch_status'] = dispatch_status
                item['dispatch_time'] = dispatch_time
                
                # Calculate TAT
                if out_time_str != "N/A" and res_time != "N/A" and out_time_str and res_time:
                    try:
                        # Standardize to datetime objects
                        # Try parsing common formats
                        formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"]
                        
                        dt_out = None
                        for f in formats:
                            try:
                                dt_out = datetime.strptime(str(out_time_str), f)
                                break
                            except: pass
                        
                        dt_res = None
                        for f in formats:
                            try:
                                dt_res = datetime.strptime(str(res_time), f)
                                break
                            except: pass
                            
                        if dt_out and dt_res:
                            diff = dt_res - dt_out
                            # Format TAT as H:M
                            total_seconds = int(diff.total_seconds())
                            hours = total_seconds // 3600
                            minutes = (total_seconds % 3600) // 60
                            tat = f"{hours}h {minutes}m"
                            
                            if total_seconds < 0:
                                tat = "N/A"
                    except:
                        pass
                
                item['tat'] = tat
                outsourced_data.append(item)

            # Sort by recently outsourced first
            outsourced_data.sort(key=lambda x: x.get('outsourced_time') or '', reverse=True)

            return JsonResponse({
                "data": outsourced_data,
                "count": len(outsourced_data)
            }, safe=False)

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)    

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def check_sample_status(request, barcode):
    if request.method == 'GET':
        try:
            # Check if an entry exists for this patient_id
            existing_entry = SampleStatus.objects.filter(barcode=barcode).first()
            
            if existing_entry:
                return JsonResponse({
                    'exists': True,
                    'message': 'Sample status data exists for this patient'
                }, status=200)
            else:
                return JsonResponse({
                    'exists': False,
                    'message': 'No sample status data found for this patient'
                }, status=200)
                
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)
   