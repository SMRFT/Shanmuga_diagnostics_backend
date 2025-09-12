from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from ...models import Hmsbarcode, HmspatientBilling
from datetime import datetime, timedelta
from django.utils import timezone 
from pymongo import MongoClient
import json
import os
from rest_framework.decorators import api_view, permission_classes
from django.views.decorators.csrf import csrf_exempt
from django.utils.dateparse import parse_datetime
from django.forms.models import model_to_dict
from pyauth.auth import HasRoleAndDataPermission



from ...models import Hmsbarcode,Hmssamplestatus
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def hms_get_samplepatients_by_date(request):
    """
    Get sample patients from Hmsbarcode who have tests that are either not in Hmssamplestatus
    or have Pending status in Hmssamplestatus. Supports both single date and date range queries.
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
        
        # Get Hmssamplestatus records for the date range
        sample_status_records = Hmssamplestatus.objects.filter(
            date__gte=from_date_parsed, 
            date__lte=to_date_parsed + timedelta(days=1)
        ).values_list('barcode', 'testdetails')
        
        completed_samples = set()
        pending_samples = set()
        
        for barcode, testdetails in sample_status_records:
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails)
                except json.JSONDecodeError:
                    continue
            
            for test in testdetails:
                test_key = (barcode, test.get('testname', ''))
                if test.get('samplestatus') == 'Pending':
                    pending_samples.add(test_key)
                else:
                    completed_samples.add(test_key)
        
        # Get patients from Hmsbarcode
        patients = Hmsbarcode.objects.filter(
            date__gte=from_date_parsed, 
            date__lte=to_date_parsed + timedelta(days=1)
        ).order_by('-date', 'barcode')
        
        # Connect to MongoDB to get patient details
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_hmspatientbilling
        
        filtered_patients = []
        for patient in patients:
            patient_tests = patient.testdetails
            if isinstance(patient_tests, str):
                try:
                    patient_tests = json.loads(patient_tests)
                except json.JSONDecodeError:
                    continue
            
            if isinstance(patient_tests, list):
                patient_test_names = {test.get('testname', '') for test in patient_tests}
            else:
                continue
            
            should_include = False
            for test_name in patient_test_names:
                test_key = (patient.barcode, test_name)
                if (test_key not in completed_samples and test_key not in pending_samples) or \
                   (test_key in pending_samples):
                    should_include = True
                    break
            
            if should_include:
                # Get patient details from MongoDB
                patient_details = collection.find_one(
                    {"billnumber": patient.billnumber},
                    {
                        "_id": 0,
                        "patient_id": 1,
                        "ipnumber": 1,
                        "patientname": 1,
                        "age": 1,
                        "age_type": 1,
                        "gender": 1,
                        "phone": 1,
                        "ref_doctor": 1,
                        "segment": 1
                    }
                )
                
                patient_dict = model_to_dict(patient)
                
                if patient_dict.get('date'):
                    patient_dict['date'] = patient_dict['date'].isoformat()
                
                if isinstance(patient_dict.get('testdetails'), str):
                    try:
                        patient_dict['testdetails'] = json.loads(patient_dict['testdetails'])
                    except json.JSONDecodeError:
                        patient_dict['testdetails'] = []
                
                if isinstance(patient_dict['testdetails'], list):
                    patient_dict['testdetails'] = [
                        {**test, 'barcode': patient.barcode}
                        for test in patient_dict['testdetails']
                    ]
                
                # Add patient details from MongoDB
                if patient_details:
                    patient_dict.update({
                        'patient_id': patient_details.get('patient_id', ''),
                        'patientname': patient_details.get('patientname', ''),
                        'age': patient_details.get('age', ''),
                        'age_type': patient_details.get('age_type', ''),
                        'gender': patient_details.get('gender', ''),
                        'phone': patient_details.get('phone', ''),
                        'ref_doctor': patient_details.get('ref_doctor', ''),
                        'segment': patient_details.get('segment', '')
                    })
                
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
            
            print(f"Request Data: {data}")  # For debugging
            
            # Extract patient data
            employee_id = data.get('auth-user-id')
            barcode = data.get('barcode')           
            date = data.get('date')          
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
            
            # Check if an entry with the same barcode and date exists
            existing_entry = Hmssamplestatus.objects.filter(
                barcode=barcode,
                date=date
            ).first()
            
            if existing_entry:
                return JsonResponse({
                    'error': 'Please change status as Sample Collected',
                    'message': 'Barcode already exists. Please change status as Sample Collected'
                }, status=409)
            
            # Process and validate testdetails
            processed_testdetails = []
            for test in testdetails:
                samplestatus = test.get('samplestatus', 'Pending')
                processed_test = {
                    'test_id': test.get('test_id'),
                    'testname': test.get('testname'),
                    'container': test.get('container', 'N/A'),
                    'department': test.get('department', 'N/A'),
                    'samplecollector': test.get('samplecollector', 'N/A'),
                    'samplestatus': samplestatus,
                    'samplecollected_time': (
                        test.get('samplecollected_time') 
                        if samplestatus != "Sample Collected" 
                        else timezone.now().isoformat()
                    ),
                    'received_time': test.get('received_time'),
                    'rejected_time': test.get('rejected_time'),
                    'oursourced_time': test.get('oursourced_time'),
                    'collectd_by': (
                        test.get('collectd_by') 
                        if samplestatus != "Sample Collected" 
                        else request.user.get_full_name() if hasattr(request.user, 'get_full_name') 
                        else data.get('employee_name')  # fallback if you send from frontend
                    ),
                    'received_by': test.get('received_by'),
                    'rejected_by': test.get('rejected_by'),
                    'oursourced_by': test.get('oursourced_by'),
                    'remarks': test.get('remarks'),
                }
                processed_testdetails.append(processed_test)
            
            # Save the new entry
            sample_status = Hmssamplestatus(
                barcode=barcode,             
                date=date,               
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
            print(f"Error saving sample status: {str(e)}")  # For debugging
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)

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

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def hms_get_sample_status_data(request, barcode):
    """
    Get sample status data for a specific barcode
    """
    if request.method == 'GET':
        try:
            # Find the sample status entry for this barcode
            sample_status = Hmssamplestatus.objects.filter(barcode=barcode).first()
            
            if not sample_status:
                return JsonResponse({
                    'error': 'No sample status data found for this barcode'
                }, status=404)
            
            # Convert model instance to dictionary
            sample_data = model_to_dict(sample_status)
            
            # Format the date for frontend
            if sample_data.get('date'):
                sample_data['date'] = sample_data['date'].isoformat()
            
            # Format created_date and lastmodified_date if they exist
            if sample_data.get('created_date'):
                sample_data['created_date'] = sample_data['created_date'].isoformat()
            if sample_data.get('lastmodified_date'):
                sample_data['lastmodified_date'] = sample_data['lastmodified_date'].isoformat()
            
            # Ensure testdetails is properly formatted (parse if it's a JSON string)
            if isinstance(sample_data.get('testdetails'), str):
                try:
                    sample_data['testdetails'] = json.loads(sample_data['testdetails'])
                except json.JSONDecodeError:
                    sample_data['testdetails'] = []
            
            return JsonResponse({
                'data': sample_data,
                'message': 'Sample status data retrieved successfully'
            }, status=200)
                
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)

@api_view(['PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def hms_patch_sample_status(request, barcode):
    # Import timezone at the top of the function
    from django.utils import timezone
    
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_hmssamplestatus
    
    if request.method == 'PATCH':
        try:
            # Handle different data formats
            if hasattr(request, 'data'):
                data = request.data
            else:
                data = json.loads(request.body)
            
            print(f"PATCH Request Data: {data}")
            
            # Extract employee_id for lastmodified_by
            employee_id = data.get('auth-user-id')
            
            # Extract testdetails from the request data
            if 'testdetails' not in data:
                return JsonResponse({'error': 'testdetails field is required'}, status=400)
            
            updates = data['testdetails']
            
            if not isinstance(updates, list):
                return JsonResponse({'error': 'testdetails must be an array'}, status=400)
            
            # Find the patient document
            patient_doc = collection.find_one({"barcode": barcode})
            if not patient_doc:
                return JsonResponse({'error': 'No patient found with the given barcode'}, status=404)
            
            # Get current testdetails
            testdetails = patient_doc.get('testdetails', [])
            
            # Parse testdetails if it's a string
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails)
                except json.JSONDecodeError:
                    return JsonResponse({'error': 'Invalid testdetails format'}, status=400)
            
            if not isinstance(testdetails, list):
                return JsonResponse({'error': 'testdetails is not in the correct format'}, status=400)
            
            # First, validate if any changes are needed
            changes_needed = False
            for update in updates:
                for existing_test in testdetails:
                    if not isinstance(existing_test, dict):
                        continue
                    
                    # Find matching test
                    if (existing_test.get('test_id') == update.get('test_id') or 
                        existing_test.get('testname') == update.get('testname')):
                        
                        new_status = update.get('samplestatus')
                        current_status = existing_test.get('samplestatus')
                        
                        # Check if status change is needed
                        if new_status != current_status:
                            # Special validation: if trying to change to Pending but already Pending
                            if new_status == 'Pending' and current_status == 'Pending':
                                continue  # No change needed
                            changes_needed = True
                            break
                
                if changes_needed:
                    break
            
            # If no changes are needed, return early
            if not changes_needed:
                return JsonResponse({'error': 'No changes were made'}, status=400)
            
            # Process updates
            updated_testdetails = []
            updated_count = 0
            
            for existing_test in testdetails:
                if not isinstance(existing_test, dict):
                    updated_testdetails.append(existing_test)
                    continue
                
                # Find matching update
                matching_update = None
                for update in updates:
                    if (existing_test.get('test_id') == update.get('test_id') or 
                        existing_test.get('testname') == update.get('testname')):
                        matching_update = update
                        break
                
                if matching_update:
                    # Update the test status and related fields
                    new_status = matching_update.get('samplestatus', existing_test.get('samplestatus'))
                    current_status = existing_test.get('samplestatus')
                    
                    # Only update if status actually changes
                    if new_status != current_status:
                        # Update fields based on new status
                        if new_status == 'Sample Collected':
                            # Set collected time in IST format
                            ist_time = timezone.now().astimezone(timezone.get_current_timezone())
                            formatted_time = ist_time.strftime('%Y-%m-%d %H:%M:%S')
                            
                            existing_test['samplestatus'] = new_status
                            existing_test['samplecollected_time'] = formatted_time
                            existing_test['collectd_by'] = matching_update.get('collectd_by')
                            updated_count += 1
                        elif new_status == 'Pending':
                            # Reset to pending status
                            existing_test['samplestatus'] = new_status
                            existing_test['samplecollected_time'] = None
                            existing_test['collectd_by'] = None
                            updated_count += 1
                        else:
                            existing_test['samplestatus'] = new_status
                            updated_count += 1
                
                updated_testdetails.append(existing_test)
            
            # Check if any updates were actually made after processing
            if updated_count == 0:
                return JsonResponse({'error': 'No changes were made'}, status=400)
            
            # Prepare update data
            update_data = {
                "testdetails": json.dumps(updated_testdetails),
                "lastmodified_date": timezone.now()
            }
            
            if employee_id:
                update_data["lastmodified_by"] = employee_id
            
            # Update the document in MongoDB
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
            print(f"KeyError: {str(e)}")
            return JsonResponse({'error': f'Missing required field: {str(e)}'}, status=400)
        except Exception as e:
            print(f"Unexpected error: {str(e)}")
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)

from django.utils.timezone import make_aware
@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def hms_get_sample_collected(request):
    if request.method == "GET":
        try:
            # Get date parameters from query string
            from_date = request.GET.get('from_date')
            to_date = request.GET.get('to_date')
            
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
            
            # Fetch filtered samples
            samples = samples_query
            
            # Connect to MongoDB to get patient details
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            collection = db.core_hmspatientbilling
            
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
                        # Fetch additional patient data from MongoDB using barcode
                        barcode_details = None
                        try:
                            # Find patient by barcode in Hmsbarcode
                            hms_barcode = Hmsbarcode.objects.get(barcode=sample.barcode)
                            # Get patient details from MongoDB using billnumber
                            barcode_details = collection.find_one(
                                {"billnumber": hms_barcode.billnumber},
                                {
                                    "_id": 0,
                                    "patient_id": 1,
                                    "patientname": 1,
                                    "age": 1,
                                    "age_type": 1,
                                    "gender": 1,
                                    "segment": 1
                                }
                            )
                        except Hmsbarcode.DoesNotExist:
                            pass
                        
                        # Create unique key for patient
                        patient_key = sample.barcode
                        
                        # If patient is not already in the dictionary, add them
                        if patient_key not in patient_data:
                            if barcode_details:
                                patient_data[patient_key] = {
                                    "date": sample.date,
                                    "patient_id": barcode_details.get("patient_id", ""),
                                    "patientname": barcode_details.get("patientname", ""),
                                    "barcode": sample.barcode,
                                    "age": barcode_details.get("age", ""),
                                    "gender": barcode_details.get("gender", "N/A"),
                                    "segment": barcode_details.get("segment", ""),
                                    "testdetails": []
                                }
                            else:
                                # Fallback to existing sample data
                                patient_data[patient_key] = {
                                    "date": sample.date,
                                    "patient_id": "",
                                    "patientname": "",
                                    "barcode": sample.barcode,
                                    "age": "",
                                    "gender": "N/A",
                                    "segment": "",
                                    "testdetails": []
                                }
                        
                        # Append the test details
                        patient_data[patient_key]["testdetails"].append({
                            "test_id": detail.get("test_id", "N/A"),
                            "testname": detail.get("testname", "N/A"),
                            "container": detail.get("container", "N/A"),
                            "department": detail.get("department", "N/A"),
                            "samplecollector": detail.get("samplecollector", "N/A"),
                            "samplestatus": detail.get("samplestatus", "N/A"),
                            "samplecollected_time": detail.get("samplecollected_time", "N/A"),
                        })
            
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

@api_view(['PUT'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def hms_update_sample_collected(request, barcode):
    """
    Update sample status in MongoDB:
    - Find record by barcode + samplecollected_time
    - Find correct test within testdetails using test_id
    - Update only that test entry
    """
    try:
        # MongoDB connection
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_hmssamplestatus
        
        # Parse body
        body = request.data if hasattr(request, 'data') else json.loads(request.body)
        updates = body.get("updates", [])
        barcode = body.get("barcode")
        samplecollected_time = body.get("samplecollected_time")  # string 'YYYY-MM-DD HH:MM:SS'
        
        if not updates:
            return JsonResponse({"error": "Updates are required"}, status=400)
        if not barcode or not samplecollected_time:
            return JsonResponse({"error": "barcode and samplecollected_time are required"}, status=400)
        
        # ===== Find the correct patient record =====
        candidate_records = list(collection.find({"barcode": barcode}))
        if not candidate_records:
            return JsonResponse({"error": "Sample not found"}, status=404)
        
        preferred_record = None
        for rec in candidate_records:
            testdetails = json.loads(rec.get("testdetails", "[]"))
            if any(t.get("samplecollected_time") == samplecollected_time for t in testdetails):
                preferred_record = rec
                break
        
        if not preferred_record:
            return JsonResponse({"error": "Sample with matching collected time not found"}, status=404)
        
        record_id = preferred_record["_id"]
        testdetails = json.loads(preferred_record.get("testdetails", "[]"))
        
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
            remarks = update.get("remarks")
            
            if test_id is None or new_status is None:
                return JsonResponse({"error": "test_id and samplestatus are required"}, status=400)
            
            # Find the test by test_id AND samplecollected_time
            test_entry = next(
                (t for t in testdetails if t.get("test_id") == test_id and t.get("samplecollected_time") == samplecollected_time),
                None
            )
            
            if not test_entry:
                return JsonResponse({"error": f"Test with id {test_id} not found for given collected time"}, status=404)
            
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
        
        # ===== Save back to DB =====
        collection.update_one(
            {"_id": record_id},
            {"$set": {"testdetails": json.dumps(testdetails)}}
        )
        
        return JsonResponse({"message": "Sample status updated successfully"}, status=200)
        
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)