from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
import logging
from ...models import Hmsbarcode, HmspatientBilling
from datetime import datetime, timedelta
from django.utils import timezone 
from core.mongo_client import get_client
import json
import os
from rest_framework.decorators import api_view, permission_classes
from django.views.decorators.csrf import csrf_exempt
from django.utils.dateparse import parse_datetime
from django.forms.models import model_to_dict
from pyauth.auth import HasRoleAndDataPermission



from ...models import Hmsbarcode,Hmssamplestatus

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def hms_get_samplepatients_by_date(request):
   
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
        
        # Get Hmssamplestatus records for the date range
        sample_status_records = Hmssamplestatus.objects.filter(
            date__gte=from_date_parsed, 
            date__lte=to_date_parsed + timedelta(days=1)
        ).values_list('barcode', 'testdetails')
        
        # Track test status by barcode and test_id
        completed_samples = set()
        pending_samples = set()
        
        for barcode, testdetails in sample_status_records:
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails)
                except json.JSONDecodeError:
                    continue
            
            for test in testdetails:
                # Use test_id instead of testname for more accurate tracking
                test_key = (barcode, test.get('test_id', ''))
                if test.get('samplestatus') in ["Pending", "Rejected"]:
                    pending_samples.add(test_key)
                else:
                    # Any other status (Sample Collected, Received, etc.) is considered completed
                    completed_samples.add(test_key)
        
        # Get patients from Hmsbarcode with all patient details
        patients = Hmsbarcode.objects.filter(
            date__gte=from_date_parsed, 
            date__lte=to_date_parsed + timedelta(days=1)
        ).order_by('-date', 'barcode')
        
        # Connect to MongoDB to get test details from core_testdetails
        client = get_client()
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
                test_key = (patient.barcode, test_id)
                
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
                                'test_name': test_detail.get('test_name', 'N/A'),
                                'department': test_detail.get('department', 'N/A'),
                                'collection_container': test_detail.get('collection_container', 'N/A')
                            }
                            enriched_testdetails.append(enriched_test)
            
            # Only include patient if they have at least one pending/not-in-status test
            if enriched_testdetails:
                # Create patient dictionary with all Hmsbarcode model fields
                patient_dict = {
                    'patient_id': patient.patient_id or '',
                    'ipnumber': patient.ipnumber or '',
                    'patientname': patient.patientname or '',
                    'age': patient.age or 0,
                    'age_type': patient.age_type or '',
                    'billnumber': patient.billnumber or '',
                    'gender': patient.gender or '',
                    'barcode': patient.barcode or '',
                    'opiptype': patient.IPOPType or '',
                    'ref_doctor': patient.ref_doctor or '',
                    'date': patient.date.isoformat() if patient.date else '',
                    'location_id': patient.location_id or 'hms',
                    'testdetails': enriched_testdetails
                }
                
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
def hms_sample_status(request):
    if request.method == 'POST':
        try:
            # Handle different data formats
            if hasattr(request, 'data'):
                data = request.data
            else:
                data = json.loads(request.body)
            logger.debug(f"Request Data: {data}")  # For debugging
            # Extract patient data
            employee_id = data.get('auth-user-id')
            date = data.get('date')
            barcode = data.get('barcode')
            testdetails = data.get('testdetails', [])
            # Validate required fields
            required_fields = ['barcode']
            missing_fields = []
            for field in required_fields:
                if not data.get(field):
                    missing_fields.append(field)
            if missing_fields:
                return JsonResponse({
                    'error': f'Missing required fields: {", ".join(missing_fields)}'
                }, status=400)
            # Parse and format date if provided
            if date:
                try:
                    # Handle both date and datetime formats
                    if ' ' in str(date):
                        # If datetime string is provided, extract only the date part
                        date = str(date).split(' ')[0]
                    # Validate date format YYYY-MM-DD
                    from datetime import datetime
                    datetime.strptime(date, '%Y-%m-%d')
                except ValueError:
                    return JsonResponse({
                        'error': 'Invalid date format. Expected YYYY-MM-DD'
                    }, status=400)
            # Check if an entry with the same barcode and date exists
            existing_entry = Hmssamplestatus.objects.filter(
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
                # Parse datetime strings to ensure proper format
                def parse_datetime(dt_string):
                    if dt_string and isinstance(dt_string, str):
                        try:
                            from datetime import datetime
                            # Try to parse the datetime string
                            dt = datetime.strptime(dt_string, '%Y-%m-%d %H:%M:%S')
                            return dt.strftime('%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            return dt_string
                    return dt_string
                processed_test = {
                    'test_id': test.get('test_id'),
                    'samplestatus': test.get('samplestatus', 'Pending'),
                    'samplecollected_time': parse_datetime(samplecollected_time),
                    'received_time': parse_datetime(received_time),
                    'rejected_time': parse_datetime(rejected_time),
                    'collectd_by': test.get('collectd_by'),
                    'received_by': test.get('received_by'),
                    'rejected_by': test.get('rejected_by'),
                    'remarks': test.get('remarks'),
                }
                processed_testdetails.append(processed_test)
            # Save the new entry
            sample_status = Hmssamplestatus(
                date=date,
                barcode=barcode,
                created_by=employee_id,
                testdetails=processed_testdetails
            )
            sample_status.save()
            return JsonResponse({
                'message': 'Data saved successfully',
                'saved_data': {
                    'barcode': barcode,
                    'testdetails_count': len(processed_testdetails),
                    'testdetails': processed_testdetails
                }
            }, status=201)
        except KeyError as e:
            return JsonResponse({'error': f'Missing key: {str(e)}'}, status=400)
        except Exception as e:
            logger.error(f"Error saving sample status: {str(e)}")  # For debugging
            import traceback
            traceback.print_exc()  # Print full stack trace for debugging
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid request method'}, status=405)


@api_view(['PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def hms_patch_sample_status(request, barcode):
    from django.utils import timezone
    
    client = get_client()
    db = client.Diagnostics
    collection = db.core_hmssamplestatus
    
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
            
            if not isinstance(updates, list) or len(updates) == 0:
                return JsonResponse({'error': 'testdetails must be a non-empty array'}, status=400)
            
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
            
            # ✅ Build lookup map of only the test_ids sent in the request
            updates_map = {u.get('test_id'): u for u in updates if u.get('test_id') is not None}
            
            updated_count = 0
            test_ids_updated = []
            
            for existing_test in testdetails:
                if not isinstance(existing_test, dict):
                    continue
                
                test_id = existing_test.get('test_id')
                
                # ✅ Skip tests NOT in the request — leave them completely untouched
                if test_id not in updates_map:
                    continue
                
                matching_update = updates_map[test_id]
                new_status = matching_update.get('samplestatus')
                current_status = existing_test.get('samplestatus')
                
                if new_status and new_status != current_status:
                    ist_time = timezone.now().astimezone(timezone.get_current_timezone())
                    formatted_time = ist_time.strftime('%Y-%m-%d %H:%M:%S')
                    
                    if new_status == 'Sample Collected':
                        existing_test['samplestatus'] = new_status
                        existing_test['samplecollected_time'] = matching_update.get('samplecollected_time', formatted_time)
                        existing_test['collectd_by'] = matching_update.get('collectd_by')
                        updated_count += 1
                        test_ids_updated.append(test_id)
                        
                    elif new_status == 'Pending':
                        existing_test['samplestatus'] = new_status
                        existing_test['samplecollected_time'] = None
                        existing_test['collectd_by'] = None
                        updated_count += 1
                        test_ids_updated.append(test_id)
            
            if updated_count == 0:
                return JsonResponse({'error': 'No matching test_id found or no changes were made'}, status=400)
            
            update_data = {
                "testdetails": json.dumps(testdetails),  # ✅ Save original array (mutated in-place)
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
                    'message': f'Successfully updated {updated_count} test(s) for barcode {barcode}',
                    'updated_count': updated_count,
                    'test_ids_updated': test_ids_updated
                }, status=200)
            else:
                return JsonResponse({'error': 'No changes were made to the database'}, status=400)
                
        except KeyError as e:
            return JsonResponse({'error': f'Missing required field: {str(e)}'}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)

from django.utils.timezone import make_aware
from core.pagination import paginate_queryset
from django.db.models import Q
@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def hms_get_sample_collected(request):
    """
    Get sample collected patients from Hmssamplestatus.
    Fetches patient details directly from Hmsbarcode model and test details from core_testdetails.
    """
    if request.method == "GET":
        try:
            # Get date parameters from query string
            from_date = request.GET.get('from_date')
            to_date = request.GET.get('to_date')
            search = request.GET.get('search', '').strip()

            # Start with all samples
            samples_query = Hmssamplestatus.objects.all()

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

            # Apply search filtering by resolving matching barcodes from Hmsbarcode
            # (Hmssamplestatus has no patientname/patient_id fields; those live on Hmsbarcode)
            if search:
                matching_barcodes = Hmsbarcode.objects.filter(
                    Q(patientname__icontains=search) | Q(patient_id__icontains=search) | Q(barcode__icontains=search)
                ).values_list('barcode', flat=True)
                samples_query = samples_query.filter(barcode__in=matching_barcodes)

            # Paginate the filtered samples before processing (page/limit read from request.GET)
            samples, page_meta = paginate_queryset(samples_query, request)

            # Connect to MongoDB to get test details from core_testdetails
            client = get_client()
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
                        
                        # If patient is not already in the dictionary, fetch from Hmsbarcode
                        if patient_key not in patient_data:
                            try:
                                # Find patient by barcode in Hmsbarcode model
                                hms_barcode = Hmsbarcode.objects.get(barcode=sample.barcode)
                                
                                patient_data[patient_key] = {
                                    "date": sample.date,
                                    "patient_id": hms_barcode.patient_id or '',
                                    "ipnumber": hms_barcode.ipnumber or '',
                                    "patientname": hms_barcode.patientname or '',
                                    "barcode": hms_barcode.barcode or '',
                                    "age": hms_barcode.age or 0,
                                    "age_type": hms_barcode.age_type or '',
                                    "gender": hms_barcode.gender or '',
                                    "billnumber": hms_barcode.billnumber or '',
                                    "opiptype": hms_barcode.IPOPType or '',
                                    "ref_doctor": hms_barcode.ref_doctor or '',
                                    "location_id": hms_barcode.location_id or '',
                                    "testdetails": []
                                }
                            except Hmsbarcode.DoesNotExist:
                                # Fallback if barcode not found in Hmsbarcode
                                patient_data[patient_key] = {
                                    "date": sample.date,
                                    "patient_id": "",
                                    "ipnumber": "",
                                    "patientname": "",
                                    "barcode": sample.barcode,
                                    "age": 0,
                                    "age_type": "",
                                    "gender": "",
                                    "billnumber": "",
                                    "opiptype": "",
                                    "ref_doctor": "",
                                    "location_id": "",
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
                                    "test_name": test_detail.get('test_name', "N/A"),
                                    "container": test_detail.get('collection_container', "N/A"),
                                    "department": test_detail.get('department', "N/A"),
                                    "collectd_by": detail.get("collectd_by", "N/A"),
                                    "samplestatus": detail.get("samplestatus", "N/A"),
                                    "samplecollected_time": detail.get("samplecollected_time", "N/A"),
                                }
                                
                           
                        # Append the enriched test details
                        patient_data[patient_key]["testdetails"].append(enriched_test)
            
            # Convert the dictionary to a list
            data = list(patient_data.values())
            
            # Return the filtered data as a response
            # NOTE: response shape now includes total_pages/current_page/total_count for pagination
            return JsonResponse({
                "data": data,
                "filters_applied": {
                    "from_date": from_date,
                    "to_date": to_date,
                    "total_records": len(data)
                },
                **page_meta
            }, safe=False)
            
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

@api_view(['PUT'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def hms_update_sample_collected(request, barcode):
    """
    Update sample status in MongoDB:
    - Find record by barcode
    - Find correct test within testdetails using test_id
    - Update only that test entry
    """
    try:
        # MongoDB connection
        client = get_client()
        db = client.Diagnostics
        collection = db.core_hmssamplestatus
        
        # Parse body
        body = request.data if hasattr(request, 'data') else json.loads(request.body)
        updates = body.get("updates", [])
        barcode = body.get("barcode")
        
        if not updates:
            return JsonResponse({"error": "Updates are required"}, status=400)
        if not barcode:
            return JsonResponse({"error": "barcode is required"}, status=400)
        
        # ===== Find the correct patient record =====
        record = collection.find_one({"barcode": barcode})
        if not record:
            return JsonResponse({"error": "Sample not found"}, status=404)
        
        record_id = record["_id"]
        testdetails = json.loads(record.get("testdetails", "[]"))
        
        # ===== Apply updates =====
        from django.utils import timezone
        import pytz
        ist_timezone = pytz.timezone('Asia/Kolkata')
        current_time = timezone.now().astimezone(ist_timezone)
        formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S')
        
        for update in updates:
            test_id = update.get("test_id")
            new_status = update.get("samplestatus")
            received_by = update.get("received_by")
            rejected_by = update.get("rejected_by")
            outsourced_by = update.get("outsourced_by")
            outsource_lab = update.get("outsource_lab")
            remarks = update.get("remarks")
            
            if test_id is None or new_status is None:
                return JsonResponse({"error": "test_id and samplestatus are required"}, status=400)
            
            # Find the test by test_id only
            test_entry = next(
                (t for t in testdetails if t.get("test_id") == test_id),
                None
            )
            
            if not test_entry:
                return JsonResponse({"error": f"Test with id {test_id} not found"}, status=404)
            
            # Update sample status and timestamps
            test_entry['samplestatus'] = new_status
            if new_status == "Received":
                test_entry['received_time'] = formatted_time
                test_entry['received_by'] = received_by
            elif new_status == "Rejected":
                test_entry['rejected_time'] = formatted_time
                test_entry['rejected_by'] = rejected_by
                test_entry['remarks'] = remarks
            elif new_status == "Outsource":
                test_entry['outsourced_time'] = formatted_time
                test_entry['outsourced_by'] = outsourced_by
                test_entry['outsource_lab'] = outsource_lab
        
        # ===== Save back to DB =====
        collection.update_one(
            {"_id": record_id},
            {"$set": {"testdetails": json.dumps(testdetails)}}
        )
        
        return JsonResponse({"message": "Sample status updated successfully"}, status=200)
        
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    
@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def hms_check_sample_status(request, barcode):
    if request.method == 'GET':
        try:
            # Check if an entry exists for this barcode
            existing_entry = Hmssamplestatus.objects.filter(barcode=barcode).first()
            
            if existing_entry:
                return JsonResponse({
                    'exists': True,
                    'message': 'Sample status data exists for this barcode'
                }, status=200)
            else:
                return JsonResponse({
                    'exists': False,
                    'message': 'No sample status data found for this barcode'
                }, status=200)
                
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)