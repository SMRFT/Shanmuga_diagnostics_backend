from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from pymongo import MongoClient
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ..models import  TestValue
from bson import json_util
import json
load_dotenv()
logger = logging.getLogger(__name__)

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_corporate_sample(request, batch_number):
    """
    Get all sample details for a specific batch number with employee information
    """
    if request.method == "GET":
        client = None
        try:
            # Connect to MongoDB
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Corporatehealthcheckup  # Database name

            # Collections
            samples_collection = db["core_sample"]
            employee_collection = db["core_employeeregistration"]
            billing_collection = db["core_billing"]

            # Connect to Diagnostics database for test details
            diagnostics_db = client["Diagnostics"]
            test_details_collection = diagnostics_db["core_testdetails"]

            # Find all samples for this batch number
            pipeline = [
                {
                    "$match": {
                        "testdetails": {
                            "$regex": f'"batch_number":\\s*"{batch_number}"'
                        }
                    }
                }
            ]

            samples = list(samples_collection.aggregate(pipeline))

            # Get all data for lookups
            employees = list(employee_collection.find())
            billing = list(billing_collection.find())
            test_details_data = list(test_details_collection.find())

            # Create lookup dictionaries with better error handling and debugging
            # Step 1: barcode -> employee_id from core_billing
            billing_lookup = {}
            for bill in billing:
                barcode = bill.get('barcode')
                employee_id = bill.get('employee_id')
                if barcode and employee_id:
                    # Convert both to string for consistent lookup
                    barcode_key = str(barcode).strip()
                    employee_id_value = str(employee_id).strip()
                    billing_lookup[barcode_key] = employee_id_value
            
            # Debug: Print first few billing records
            logger.info(f"Sample billing lookup entries: {dict(list(billing_lookup.items())[:5])}")
            
            # Step 2: employee_id -> employee_data from core_employeeregistration
            employee_lookup = {}
            for emp in employees:
                employee_id = emp.get('employee_id')
                if employee_id:
                    # Convert to string for consistent lookup
                    emp_id_key = str(employee_id).strip()
                    employee_lookup[emp_id_key] = emp
            
            # Debug: Print first few employee records
            logger.info(f"Sample employee lookup entries: {list(employee_lookup.keys())[:5]}")
            
            # Test lookup
            test_lookup = {str(test.get('test_id')): test for test in test_details_data if test.get('test_id')}

            batch_samples = []

            def safe_get(obj, key, default=""):
                value = obj.get(key) if obj else None
                return value if value is not None else default

            for sample in samples:
                barcode = sample.get('barcode')
                test_details = sample.get('testdetails', '[]')

                # Parse test details
                if isinstance(test_details, str):
                    try:
                        test_details = json.loads(test_details)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse testdetails for sample {sample.get('_id')}")
                        test_details = []

                # Filter tests that belong to this batch and have status "Transferred"
                batch_tests = []
                for detail in test_details:
                    if (detail.get("batch_number") == batch_number and 
                        detail.get("samplestatus") == "Transferred"):
                        batch_tests.append(detail)

                # Skip if no transferred tests for this batch
                if not batch_tests:
                    continue

                # Get employee information with detailed debugging
                employee_id = None
                employee_data = {}
                
                if barcode:
                    # Convert barcode to string and strip whitespace for consistent lookup
                    barcode_str = str(barcode).strip()
                    logger.info(f"Processing barcode: '{barcode_str}'")
                    
                    # Step 1: Get employee_id from billing using barcode
                    employee_id = billing_lookup.get(barcode_str)
                    logger.info(f"Found employee_id: '{employee_id}' for barcode: '{barcode_str}'")
                    
                    if employee_id:
                        # Step 2: Get employee data using employee_id
                        employee_data = employee_lookup.get(employee_id, {})
                        logger.info(f"Found employee data: {bool(employee_data)} for employee_id: '{employee_id}'")
                        if employee_data:
                            logger.info(f"Employee name: {employee_data.get('employee_name', 'N/A')}")
                    else:
                        # Debug: Check if barcode exists with different formatting
                        logger.warning(f"Employee_id not found for barcode: '{barcode_str}'")
                        logger.info(f"Available barcodes (first 10): {list(billing_lookup.keys())[:10]}")
                        
                        # Try to find similar barcodes
                        similar_barcodes = [k for k in billing_lookup.keys() if barcode_str in k or k in barcode_str]
                        if similar_barcodes:
                            logger.info(f"Similar barcodes found: {similar_barcodes}")
                else:
                    logger.warning("No barcode found in sample")

                # Prepare enhanced test details
                enhanced_test_details = []
                for detail in batch_tests:
                    test_id = detail.get("test_id")
                    test_data = test_lookup.get(str(test_id), {}) if test_id else {}

                    enhanced_detail = {
                        "test_id": test_id if test_id else "N/A",
                        "testname": detail.get("testname", "N/A"),
                        "container": safe_get(test_data, "collection_container", "N/A"),
                        "department": safe_get(test_data, "department", "N/A"),
                        "samplecollector": detail.get("collected_by", "N/A"),
                        "samplestatus": detail.get("samplestatus", "N/A"),
                        "samplecollected_time": detail.get("samplecollected_time", "N/A"),
                        "batch_number": detail.get("batch_number", "N/A"),
                        "remarks": detail.get("remarks"),
                        "received_time": detail.get("received_time"),
                        "received_by": detail.get("received_by"),
                        "rejected_time": detail.get("rejected_time"),
                        "rejected_by": detail.get("rejected_by"),
                        "outsourced_time": detail.get("outsourced_time"),
                        "outsourced_by": detail.get("outsourced_by")
                    }
                    enhanced_test_details.append(enhanced_detail)

                # Handle date conversion
                created_date = sample.get('created_date')
                if created_date and hasattr(created_date, 'isoformat'):
                    created_date = created_date.isoformat()
                elif created_date:
                    created_date = str(created_date)

                # Create sample record with employee information
                sample_record = {
                    "date": created_date or "N/A",
                    "employee_id": employee_id or "N/A",
                    "employee_name": safe_get(employee_data, 'employee_name', "N/A"),
                    "barcode": barcode,
                    "age": str(safe_get(employee_data, 'age', "")) if safe_get(employee_data, 'age') else "N/A",
                    "gender": safe_get(employee_data, 'gender', "N/A"),
                    "company_id": safe_get(employee_data, 'company_id', safe_get(sample, 'company_id', "N/A")),
                    "batch_number": batch_number,
                    "testdetails": enhanced_test_details
                }

                batch_samples.append(sample_record)

            return JsonResponse({
                "status": "success",
                "data": batch_samples,
                "batch_number": batch_number,
                "count": len(batch_samples)
            }, safe=False)

        except Exception as e:
            logger.error(f"Error fetching batch samples for batch {batch_number}: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": f"Database connection error: {str(e)}"
            }, status=500)

        finally:
            if client:
                client.close()
@api_view(['PUT'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def update_corporate_sample(request,barcode):
    """
    Bulk update sample status for multiple samples/tests
    """
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Corporatehealthcheckup
    collection = db.core_sample
    
    if request.method == "PUT":
        try:
            if hasattr(request, 'data'):
                body = request.data
            else:
                body = json.loads(request.body)
            
            bulk_updates = body.get("bulk_updates", [])
            
            if not bulk_updates:
                return JsonResponse({"error": "bulk_updates are required"}, status=400)
            
            # Configure IST timezone
            from django.utils import timezone
            import pytz
            ist_timezone = pytz.timezone('Asia/Kolkata')
            current_time = timezone.now().astimezone(ist_timezone)
            formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S')
            
            success_count = 0
            error_count = 0
            errors = []
            
            for update_data in bulk_updates:
                try:
                    barcode = update_data.get("barcode")
                    updates = update_data.get("updates", [])
                    
                    if not barcode or not updates:
                        error_count += 1
                        errors.append(f"Missing barcode or updates for one item")
                        continue
                    
                    # Find the patient sample record
                    patient_sample = collection.find_one({"barcode": barcode})
                    if not patient_sample:
                        error_count += 1
                        errors.append(f"Sample not found for barcode: {barcode}")
                        continue
                    
                    # Parse testdetails
                    testdetails = json.loads(patient_sample.get('testdetails', '[]'))
                    
                    for update in updates:
                        test_id = update.get("test_id")
                        testname = update.get("testname")
                        new_status = update.get("samplestatus")
                        received_by = update.get("received_by")
                        rejected_by = update.get("rejected_by")
                        outsourced_by = update.get("outsourced_by")
                        remarks = update.get("remarks")
                        batch_number = update.get("batch_number")
                        
                        if new_status is None:
                            error_count += 1
                            errors.append(f"samplestatus is required for barcode: {barcode}")
                            continue
                        
                        # Find the specific test entry
                        test_entry = None
                        for entry in testdetails:
                            test_match = (
                                (testname and entry.get("testname") == testname) or 
                                (test_id and entry.get("test_id") == test_id)
                            )
                            batch_match = (
                                batch_number is None or 
                                entry.get("batch_number") == batch_number
                            )
                            
                            if test_match and batch_match:
                                test_entry = entry
                                break
                        
                        if test_entry is None:
                            error_count += 1
                            errors.append(f"Test not found for barcode: {barcode}, test_id: {test_id}")
                            continue
                        
                        # Update the sample status and associated fields
                        test_entry['samplestatus'] = new_status
                        
                        if new_status == "Received":
                            test_entry['received_time'] = formatted_time
                            test_entry['received_by'] = received_by
                            # Clear rejection fields
                            test_entry.pop('rejected_time', None)
                            test_entry.pop('rejected_by', None)
                            test_entry.pop('remarks', None)
                            
                        elif new_status == "Rejected":
                            test_entry['rejected_time'] = formatted_time
                            test_entry['rejected_by'] = rejected_by
                            test_entry['remarks'] = remarks
                            # Clear other status fields
                            test_entry.pop('received_time', None)
                            test_entry.pop('received_by', None)
                            test_entry.pop('outsourced_time', None)
                            test_entry.pop('outsourced_by', None)
                            
                        elif new_status == "Outsource":
                            test_entry['outsourced_time'] = formatted_time
                            test_entry['outsourced_by'] = outsourced_by
                            # Clear other status fields
                            test_entry.pop('received_time', None)
                            test_entry.pop('received_by', None)
                            test_entry.pop('rejected_time', None)
                            test_entry.pop('rejected_by', None)
                            test_entry.pop('remarks', None)
                    
                    # Save changes back to the database
                    collection.update_one(
                        {"barcode": barcode},
                        {"$set": {"testdetails": json.dumps(testdetails)}}
                    )
                    
                    success_count += 1
                    
                except Exception as e:
                    error_count += 1
                    errors.append(f"Error updating barcode {barcode}: {str(e)}")
            
            return JsonResponse({
                "status": "success" if error_count == 0 else "partial_success",
                "message": f"Successfully updated {success_count} samples. {error_count} errors occurred.",
                "success_count": success_count,
                "error_count": error_count,
                "errors": errors
            }, status=200)
            
        except Exception as e:
            logger.error(f"Error in bulk update: {str(e)}")
            return JsonResponse({"error": str(e)}, status=500)
        
        finally:
            client.close()

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_corporate_batch_generation_data(request):
    """
    Get all batch generation data where received=false with optional date filtering
    """
    if request.method == "GET":
        client = None
        try:
            # Connect to MongoDB
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Corporatehealthcheckup  # Database name
            collection = db.core_batch  # Collection name
            
            # Build query with date filtering
            query = {"received": False}
            
            # Get date parameters from request
            from_date = request.GET.get('from_date')
            to_date = request.GET.get('to_date')
            
            # Add date filtering if provided
            if from_date or to_date:
                date_query = {}
                
                if from_date:
                    # Parse from_date and convert to UTC datetime (start of day)
                    from_datetime = datetime.strptime(from_date, '%Y-%m-%d')
                    # Convert to UTC by treating as UTC (since database stores in UTC)
                    from_datetime = from_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
                    date_query['$gte'] = from_datetime
                
                if to_date:
                    # Parse to_date and convert to UTC datetime (end of day)
                    to_datetime = datetime.strptime(to_date, '%Y-%m-%d')
                    # Set to end of day in UTC
                    to_datetime = to_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
                    date_query['$lte'] = to_datetime
                
                # Add date filter to query
                if date_query:
                    query['created_date'] = date_query
            
            # Log the query for debugging
            logger.info(f"MongoDB query: {query}")
            
            # Also log the actual date range being queried
            if 'created_date' in query:
                logger.info(f"Date range: {query['created_date']}")
            
            # Fetch documents with the built query
            batches = list(collection.find(query))
            
            # Log the count of found documents
            logger.info(f"Found {len(batches)} batches matching criteria")
            
            # Process the data
            processed_data = []
            for batch in batches:
                # Convert ObjectId to string for JSON serialization
                batch['_id'] = str(batch['_id'])
                
                # Parse JSON strings if they exist
                if 'batch_details' in batch and isinstance(batch['batch_details'], str):
                    try:
                        batch['batch_details'] = json.loads(batch['batch_details'])
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse batch_details for batch {batch['_id']}")
                        batch['batch_details'] = {}
                
                if 'specimen_count' in batch and isinstance(batch['specimen_count'], str):
                    try:
                        batch['specimen_count'] = json.loads(batch['specimen_count'])
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse specimen_count for batch {batch['_id']}")
                        batch['specimen_count'] = []
                
                # Convert datetime objects to ISO format strings
                if 'created_date' in batch and batch['created_date']:
                    batch['created_date'] = batch['created_date'].isoformat() if hasattr(batch['created_date'], 'isoformat') else str(batch['created_date'])
                if 'lastmodified_date' in batch and batch['lastmodified_date']:
                    batch['lastmodified_date'] = batch['lastmodified_date'].isoformat() if hasattr(batch['lastmodified_date'], 'isoformat') else str(batch['lastmodified_date'])
                
                processed_data.append(batch)
            
            return JsonResponse({
                "status": "success",
                "data": processed_data,
                "count": len(processed_data),
                "filters": {
                    "from_date": from_date,
                    "to_date": to_date
                },
                "debug_info": {
                    "query_used": str(query),
                    "total_found": len(batches)
                }
            }, safe=False)
            
        except ValueError as e:
            logger.error(f"Date parsing error: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": f"Invalid date format. Use YYYY-MM-DD format: {str(e)}"
            }, status=400)
            
        except Exception as e:
            logger.error(f"Error fetching batch generation data: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": f"Database connection error: {str(e)}"
            }, status=500)
        
        finally:
            if client:
                client.close()

@api_view(['PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def update_corporate_batch_received_status(request, batch_no):
    """
    Update the received status and optionally remarks for a specific batch using batch_no
    """
    # MongoDB connection details
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Corporatehealthcheckup  # Database name
    collection = db.core_batch  # Collection name
    
    try:
        # Parse request body
        try:
            if hasattr(request, 'data'):
                body = request.data
            else:
                body = json.loads(request.body)
            received_status = body.get('received', True)
            remarks = body.get('remarks', None)
            employee_id = request.data.get('auth-user-id') or "system"
        except json.JSONDecodeError:
            received_status = True
            remarks = None
        
        # Validate remarks for rejection
        if received_status is False and (remarks is None or remarks.strip() == ""):
            return JsonResponse({
                "status": "error",
                "message": "Remarks are required when rejecting a batch"
            }, status=400)
        # Validate employee_id
        if employee_id is None:
            return JsonResponse({
                "status": "error",
                "message": "auth-user-id is required"
            }, status=400)
       # Use employee_id for lastmodified_by
        lastmodified_by = employee_id
        
        # Build update data
        update_data = {
            "$set": {
                "received": received_status,
                "lastmodified_date": datetime.now(),
                "lastmodified_by": lastmodified_by
            }
        }
        
        # Update remarks only if provided
        if remarks is not None:
            update_data["$set"]["remarks"] = remarks.strip()
        
        # Update document in MongoDB
        result = collection.update_one(
            {"batch_number": str(batch_no)},
            update_data
        )
        
        logger.info(f"Update attempt for batch {batch_no}: matched={result.matched_count}, modified={result.modified_count}, payload={body}")
        
        if result.matched_count == 0:
            return JsonResponse({
                "status": "error",
                "message": f"Batch with batch_number '{batch_no}' not found"
            }, status=404)
        
        if result.modified_count == 0:
            return JsonResponse({
                "status": "info",
                "message": "No changes made to the batch (already in desired state)",
                "batch_no": batch_no
            }, status=200)
        
        return JsonResponse({
            "status": "success",
            "message": "Batch status updated successfully",
            "batch_no": batch_no,
            "received": received_status,
            "remarks": remarks
        }, status=200)
    
    except Exception as e:
        logger.error(f"Error updating batch status for batch {batch_no}: {str(e)}")
        return JsonResponse({
            "status": "error",
            "message": f"Database update error: {str(e)}"
        }, status=500)
    
    finally:
        if client:
            client.close()
            

@api_view(['GET','PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def corporate_overall_report(request):
    try:
        # MongoDB setup
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup  # Database name
        patients_collection = db.core_billing  # Changed to franchise_billing
        sample_status_colletion = db.core_sample # Collection name for sample 
        franchise_patient_collection = db.core_employeeregistration  # Collection for patient details

        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        employee_id = request.GET.get("employee_id")
        
        try:
            if from_date:
                from_date = datetime.strptime(from_date, "%Y-%m-%d")
            if to_date:
                to_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        
        # Build MongoDB query - using created_date for consistency
        query = {}
        if employee_id:
            query["employee_id"] = employee_id
        if from_date and to_date:
            query["created_date"] = {"$gte": from_date, "$lt": to_date}
        elif from_date:
            query["created_date"] = {"$gte": from_date}
        elif to_date:
            query["created_date"] = {"$lt": to_date}
        
        patients = list(patients_collection.find(query))
        if not patients:
            return JsonResponse([], safe=False)
        
        employee_ids = [p.get("employee_id") for p in patients if p.get("employee_id")]
        barcodes = [p.get("barcode") for p in patients if p.get("barcode")]
        
        # Get patient details from franchise_patient collection
        patient_details_map = {}
        if employee_ids:
            patient_details = franchise_patient_collection.find({"employee_id": {"$in": employee_ids}})
            for patient_detail in patient_details:
                patient_details_map[patient_detail.get("employee_id")] = patient_detail
        
        # FIXED: Status data - fetch from core_sample using barcode instead of employee_id
        sample_status_records = []
        if barcodes:
            sample_status_records = list(sample_status_colletion.find({
                "barcode": {"$in": barcodes}
            }))

        # FIXED: Convert to list and extract needed fields with barcode mapping
        sample_status_list = []
        for record in sample_status_records:
            if record and isinstance(record, dict):
                barcode = record.get("barcode")
                testdetails = record.get("testdetails")
                if barcode and testdetails:
                    sample_status_list.append({
                        "barcode": barcode,
                        "testdetails": testdetails
                    })
        
        # For TestValue objects, use barcode to link with franchise_billing
        if from_date and to_date:
            from_datetime = make_aware(from_date)
            to_datetime = make_aware(to_date - timedelta(days=1))
            test_value_records = TestValue.objects.filter(
                barcode__in=barcodes,
                date__range=(from_datetime, to_datetime)
            ).values("barcode", "testdetails")
        else:
            test_value_records = TestValue.objects.filter(
                barcode__in=barcodes
            ).values("barcode", "testdetails")
        
        # Create a mapping from barcode to employee_id from billing records
        barcode_to_patient_map = {}
        for patient in patients:
            if patient.get("barcode") and patient.get("employee_id"):
                barcode_to_patient_map[patient.get("barcode")] = patient.get("employee_id")
        
        # FIXED: Organize sample status data using barcode mapping
        sample_status_map = {}
        for record in sample_status_list:
            if record and isinstance(record, dict):
                barcode = record.get("barcode")
                testdetails = record.get("testdetails")
                
                if barcode and barcode in barcode_to_patient_map and testdetails:
                    employee_id_key = barcode_to_patient_map[barcode]
                    
                    # Parse testdetails if it's a JSON string
                    parsed_testdetails = []
                    if isinstance(testdetails, str):
                        try:
                            parsed_testdetails = json.loads(testdetails)
                        except json.JSONDecodeError:
                            parsed_testdetails = []
                    elif isinstance(testdetails, list):
                        parsed_testdetails = testdetails
                    
                    if parsed_testdetails:
                        sample_status_map.setdefault(employee_id_key, []).extend(parsed_testdetails)
        
        # Organize test value data using barcode mapping
        test_value_map = {}
        for record in test_value_records:
            if record and isinstance(record, dict):
                barcode = record.get("barcode")
                if barcode and barcode in barcode_to_patient_map:
                    employee_id = barcode_to_patient_map[barcode]
                    test_value_map.setdefault(employee_id, {"barcode": barcode, "testdetails": []})
                    if record.get("testdetails"):
                        test_value_map[employee_id]["testdetails"].extend(record["testdetails"])
        
        # Final result
        formatted_data = []
        for patient in patients:
            # SAFETY CHECK: Ensure patient is a dict
            if not isinstance(patient, dict):
                print(f"Warning: Patient record is not a dict: {type(patient)}")
                continue
                
            pid = patient.get("employee_id", "N/A")
            
            # Get patient details from franchise_patient collection
            patient_detail = patient_details_map.get(pid, {})
            # SAFETY CHECK: Ensure patient_detail is a dict
            if not isinstance(patient_detail, dict):
                patient_detail = {}
          
            # FIXED: Get test names from core_sample instead of core_billing
            sample_tests = sample_status_map.get(pid, [])
            testnames = ", ".join([
                test.get("testname", "") if isinstance(test, dict) else str(test)
                for test in sample_tests
            ])
            no_of_tests = len(sample_tests)
            
            # Age handling - similar to first document
            age_value = patient_detail.get("age", "N/A")
            age_type = patient_detail.get("age_type", "")
            age = f"{age_value} {age_type}" if age_type else str(age_value)            
            
            # Status determination
            barcode = patient.get("barcode")
            status = "Registered"
            test_values = test_value_map.get(pid, {}).get("testdetails", [])
            
            # Use barcode from test_value_map if available, similar to first document
            if not barcode and test_value_map.get(pid, {}).get("barcode"):
                barcode = test_value_map.get(pid, {}).get("barcode")
            
            # SAFETY CHECKS for sample_tests
            all_collected = all(
                t.get("samplestatus") == "Collected" if isinstance(t, dict) else False
                for t in sample_tests
            ) if sample_tests else False
            
            partially_collected = any(
                t.get("samplestatus") == "Collected" if isinstance(t, dict) else False
                for t in sample_tests
            )

            all_transferred = all(
                t.get("samplestatus") == "Transferred" if isinstance(t, dict) else False
                for t in sample_tests
            ) if sample_tests else False
            
            partially_transferred = any(
                t.get("samplestatus") == "Transferred" if isinstance(t, dict) else False
                for t in sample_tests
            )
            
            all_received = all(
                t.get("samplestatus") == "Received" if isinstance(t, dict) else False
                for t in sample_tests
            ) if sample_tests else False
            
            partially_received = any(
                t.get("samplestatus") == "Received" if isinstance(t, dict) else False
                for t in sample_tests
            )
            
            if all_collected:
                status = "Collected"
            elif partially_collected:
                status = "Partially Collected"
            if all_transferred:
                status = "Transferred"
            elif partially_transferred:
                status = "Partially Transferred"
            
            if all_received:
                status = "Received"
            elif partially_received:
                status = "Partially Received"
            
            # FIXED: Status logic for approval - compare against sample_tests count
            if test_values:
                # Check if test has parameters with values (for complex tests like CBC)
                all_tested = False
                partially_tested = False
                
                for test in test_values:
                    if isinstance(test, dict):
                        # Check if test has direct value
                        if test.get("value") is not None:
                            partially_tested = True
                        # Check if test has parameters with values (for complex tests)
                        elif test.get("parameters"):
                            params_with_values = [
                                p for p in test.get("parameters", []) 
                                if isinstance(p, dict) and p.get("value") is not None
                            ]
                            if params_with_values:
                                partially_tested = True
                
                # Check if all tests are tested
                all_tests_have_values = True
                for test in test_values:
                    if isinstance(test, dict):
                        has_value = False
                        # Check direct value
                        if test.get("value") is not None:
                            has_value = True
                        # Check parameters
                        elif test.get("parameters"):
                            params_with_values = [
                                p for p in test.get("parameters", []) 
                                if isinstance(p, dict) and p.get("value") is not None
                            ]
                            if params_with_values:
                                has_value = True
                        
                        if not has_value:
                            all_tests_have_values = False
                            break
                
                all_tested = all_tests_have_values and len(test_values) > 0
                
                # FIXED: Compare approved tests with total sample tests, not just test_values
                approved_tests_count = sum(
                    1 for t in test_values 
                    if isinstance(t, dict) and t.get("approve")
                )
                total_sample_tests = len(sample_tests)
                
                approve_all = (approved_tests_count == total_sample_tests) and total_sample_tests > 0
                approve_partial = approved_tests_count > 0 and approved_tests_count < total_sample_tests
                
                dispatch_all = all(
                    t.get("dispatch") if isinstance(t, dict) else False
                    for t in test_values
                )
                
                # Update status based on test completion
                if all_tested:
                    status = "Tested"
                elif partially_tested:
                    status = "Partially Tested"
                
                # FIXED: Use the corrected approval logic
                if approve_all:
                    status = "Approved"
                elif approve_partial:
                    status = "Partially Approved"
                
                if dispatch_all:
                    status = "Dispatched"
            
            # Handle date formatting - use created_date consistently
            created_date = patient.get("created_date")
            if created_date:
                if isinstance(created_date, datetime):
                    formatted_date = created_date.strftime("%Y-%m-%d")
                else:
                    # Handle string dates
                    try:
                        parsed_date = datetime.strptime(str(created_date), "%Y-%m-%d")
                        formatted_date = parsed_date.strftime("%Y-%m-%d")
                    except:
                        formatted_date = str(created_date)
            else:
                formatted_date = "N/A"
            
            # Final patient object - matching structure with first document
            formatted_data.append({
                "date": formatted_date,
                "patient_id": pid,
                "patient_name": patient_detail.get("employee_name", "N/A"),  # From franchise_patient
                "gender": patient_detail.get("gender", "N/A"),  # From franchise_patient
                "department": patient_detail.get("department", "N/A"),  # From franchise_patient
                "age": age,
                "email": patient_detail.get("email", "N/A"),  # From franchise_patient             
                "branch": patient_detail.get("company_id", "N/A"),  # Use franchise_id as branch  
                "test_names": testnames,
                "no_of_tests": no_of_tests,
                "barcode": barcode,
                "status": status,
            })
        
        # Close MongoDB connection
        client.close()
        
        return JsonResponse(formatted_data, safe=False)
    
    except Exception as e:
        print("Critical Error:", str(e))
        print(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)
    


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def corporate_patient_test_details(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)
    
    try:
        # MongoDB connection
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        franchise_billing_collection = db.core_billing
        franchise_sample_collection = db.core_sample
        franchise_patient_collection = db.core_employeeregistration
        
        # Get franchise billing data using barcode
        franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
        if not franchise_billing:
            return JsonResponse({'error': 'Franchise billing record not found for the given barcode'}, status=404)
        
        # Extract employee_id from franchise_billing
        employee_id = franchise_billing.get('employee_id')
        if not employee_id:
            return JsonResponse({'error': 'Patient ID not found in billing record'}, status=404)
        
        # Get franchise patient data using employee_id
        franchise_patient = franchise_patient_collection.find_one({"employee_id": employee_id})
        if not franchise_patient:
            return JsonResponse({'error': 'Franchise patient not found for the given patient ID'}, status=404)
        
        # Get franchise sample data using barcode
        franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})
        if not franchise_sample:
            return JsonResponse({'error': 'No sample records found for the given barcode'}, status=404)
        
        # Get test values from Django model - THIS IS NOW THE PRIMARY FILTER
        test_values = TestValue.objects.filter(barcode=barcode)
        if not test_values.exists():
            return JsonResponse({'error': 'No test value records found for the given barcode'}, status=404)
        
        # Get barcodes information - collect all unique barcodes for this patient
        barcodes = []
        try:
            # Get all barcodes associated with this employee_id
            all_barcodes_for_patient = franchise_billing_collection.find(
                {"employee_id": employee_id}, 
                {"barcode": 1, "_id": 0}
            )
            barcodes = [bc.get("barcode") for bc in all_barcodes_for_patient if bc.get("barcode")]
            # Remove duplicates while preserving order
            barcodes = list(dict.fromkeys(barcodes))
        except Exception:
            barcodes = []
        
        # Parse testdetails from franchise_billing
        try:
            billing_testdetails = json.loads(franchise_billing.get('testdetails', '[]'))
        except json.JSONDecodeError:
            billing_testdetails = []
        
        # Parse testdetails from franchise_sample
        sample_testdetails = []
        if franchise_sample:
            try:
                sample_testdetails = json.loads(franchise_sample.get('testdetails', '[]'))
            except json.JSONDecodeError:
                sample_testdetails = []
        
        # Build patient details response
        patient_details = {
            "patient_id": employee_id,
            "patientname": franchise_patient.get("employee_name", "N/A"),
            "age": franchise_patient.get("age", "N/A"),
            "age_type": franchise_patient.get("age_type", "Years"),
            "gender": franchise_patient.get("gender", "N/A"),
            "date": franchise_billing.get("created_date"),
            "barcode": franchise_billing.get("barcode", "N/A"),
            "barcodes": barcodes,
            "branch": franchise_billing.get("franchise_id", "N/A"),
            "refby": "SELF",
            "testdetails": []
        }
        
        # Process ONLY tests that exist in TestValue model
        for test_value in test_values:
            try:
                # Parse testdetails JSON from TestValue model
                testvalue_details = json.loads(test_value.testdetails) if isinstance(test_value.testdetails, str) else test_value.testdetails
                if not isinstance(testvalue_details, list):
                    continue
                    
                # Process each test in TestValue
                for test_detail in testvalue_details:
                    testname = test_detail.get("testname")
                    if not testname:
                        continue
                    
                    # Find corresponding sample status
                    sample_status = None
                    for sample_test in sample_testdetails:
                        if sample_test.get("testname") == testname:
                            sample_status = sample_test
                            break
                    
                    # Find corresponding billing info for MRP and test_id  
                    billing_info = None
                    for billing_test in billing_testdetails:
                        billing_testname = billing_test.get("testname") or billing_test.get("test_name")
                        if billing_testname == testname:
                            billing_info = billing_test
                            break
                    
                    # Build test detail object - ONLY if we have TestValue data
                    test_response = {
                        "department": test_detail.get("department", sample_status.get("department", "N/A") if sample_status else "N/A"),
                        "NABL": test_detail.get("NABL", True),
                        "testname": testname,
                        "verified_by": test_detail.get("verified_by", "N/A"),
                        "approve_by": test_detail.get("approve_by", "N/A"),
                        "approve_time": test_detail.get("approve_time", "N/A"),
                        "samplecollected_time": sample_status.get("samplecollected_time") if sample_status else None,
                        "received_time": sample_status.get("received_time") if sample_status else None
                    }
                    
                    # Check if test has parameters
                    if test_detail.get("parameters"):
                        # Test with parameters - add parameters array
                        processed_parameters = []
                        for param in test_detail.get("parameters", []):
                            processed_param = {
                                "name": param.get("name", "N/A"),
                                "value": param.get("value", "N/A"),
                                "unit": param.get("unit", "N/A"),
                                "specimen_type": param.get("specimen_type", "N/A"),
                                "reference_range": param.get("reference_range", "N/A"),
                                "method": param.get("method", "N/A")
                            }
                            processed_parameters.append(processed_param)
                        
                        test_response["parameters"] = processed_parameters
                    else:
                        # Test without parameters - add direct values
                        test_response.update({
                            "method": test_detail.get("method", "N/A"),
                            "specimen_type": test_detail.get("specimen_type", "N/A"),
                            "value": test_detail.get("value", "N/A"),
                            "unit": test_detail.get("unit", "N/A"),
                            "reference_range": test_detail.get("reference_range", "N/A")
                        })
                    
                    patient_details["testdetails"].append(test_response)
                    
            except (json.JSONDecodeError, AttributeError):
                continue
        
        # Close MongoDB connection
        client.close()
        
        # Return empty if no test details found
        if not patient_details["testdetails"]:
            return JsonResponse({'error': 'No approved test records found'}, status=404)
        
        return JsonResponse(patient_details, safe=False)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([ HasRoleAndDataPermission])
def get_corporate_test_value(request):
    date = request.GET.get('date')
    franchise_id = request.GET.get('franchise_id')

    print(f"Received date: {date}, franchise_id: {franchise_id}")

    if not franchise_id or not date:
        return JsonResponse({'error': 'franchise_id and date are required'}, status=400)

    try:
        test_values = TestValue.objects.filter(
            franchise_id=franchise_id,
            date__startswith=date  # ✅ fix: only match date part
        )

        if not test_values:
            return JsonResponse({'message': 'No test values found'}, status=404)

        result = []
        for test_value in test_values:
            try:
                testdetails = (
                    json.loads(test_value.testdetails)
                    if isinstance(test_value.testdetails, str)
                    else test_value.testdetails
                )
            except Exception as e:
                print(f"Error parsing testdetails: {e}")
                testdetails = test_value.testdetails

            result.append({
                'franchise_id': test_value.franchise_id,
                'barcode':test_value.barcode,
                'date': str(test_value.date),
                'testdetails': testdetails,
            })

        return JsonResponse(
            {'status': 'success', 'data': result},
            safe=False,
            status=200,
            json_dumps_params={'default': json_util.default}
        )

    except Exception as e:
        print(f"[ERROR] While processing test values: {str(e)}")
        return JsonResponse({'error': 'Internal server error', 'details': str(e)}, status=500)




from django.http import HttpResponse, JsonResponse
from rest_framework.decorators import api_view
from bson import ObjectId
import gridfs
from pymongo import MongoClient
import os

@api_view(['GET'])
# @csrf_exempt  # optional
# @permission_classes([HasRoleAndDataPermission])  # optional
def get_pdf_from_gridfs_corporate(request, file_id):
    try:
        # Connect to MongoDB
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        fs = gridfs.GridFS(db)

        # Get the file from GridFS
        file_obj = fs.get(ObjectId(file_id))
        
        # Return as HTTP response
        response = HttpResponse(file_obj.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{file_obj.filename}"'  # forces download
        return response
    except gridfs.errors.NoFile:
        return JsonResponse({"error": "File not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)



import base64
import gridfs
from bson import ObjectId

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def corporate_health_report(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)
    
    try:
        # MongoDB connection
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        
        # Collections
        franchise_billing_collection = db.core_billing
        franchise_sample_collection = db.core_sample
        franchise_patient_collection = db.core_employeeregistration
        franchise_investigation_collection = db.core_investigation
        franchise_ophthalmology_collection = db.core_ophthalmology
        
        # Get franchise billing data
        franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
        if not franchise_billing:
            return JsonResponse({'error': 'Billing record not found'}, status=404)
        
        employee_id = franchise_billing.get('employee_id')
        if not employee_id:
            return JsonResponse({'error': 'Employee ID not found'}, status=404)
        
        # Get patient data
        franchise_patient = franchise_patient_collection.find_one({"employee_id": employee_id})
        if not franchise_patient:
            return JsonResponse({'error': 'Patient not found'}, status=404)
        
        # Get sample data
        franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})
        
        # Get investigation data (only if status is approved)
        franchise_investigation = franchise_investigation_collection.find_one({
            "barcode": barcode,
            "status": "approved"
        })
        
        # Get ophthalmology data (only if status is approved)
        franchise_ophthalmology = franchise_ophthalmology_collection.find_one({
            "barcode": barcode,
            "status": "approved"
        })
        
        # Get test values from Django model
        test_values = TestValue.objects.filter(barcode=barcode)
        
        # Parse vitals from investigation - only include if available and not empty
        vitals_data = {}
        if franchise_investigation:
            try:
                vitals_raw = json.loads(franchise_investigation.get('vitals', '{}'))
                # Only include non-empty vitals
                for key, value in vitals_raw.items():
                    if value and str(value).strip() and str(value).strip() != "0":
                        vitals_data[key] = value
            except json.JSONDecodeError:
                vitals_data = {}
        
        # Store investigation file IDs and notes - only if available
        investigation_file_ids = {}
        investigation_notes = {}
        
        if franchise_investigation:
            # Only add file IDs that exist
            for file_field in ["ecg_file", "pft_file", "audiometric_file", "xray_file", "xrayfilm_file"]:
                file_id = franchise_investigation.get(file_field)
                if file_id:
                    investigation_file_ids[file_field] = file_id
            
            # Only add notes that exist and are not empty/default
            for note_field, key in [
                ("ecg_notes", "ecg_notes"),
                ("pft_notes", "pft_notes"),
                ("audiometry_notes", "audiometry_notes")
            ]:
                note_value = franchise_investigation.get(note_field)
                if note_value and note_value.strip():
                    investigation_notes[key] = note_value
        
        # Parse medical history - only if exists and not default values
        medical_history_data = {}
        if franchise_investigation:
            patient_history = franchise_investigation.get("patient_history")
            if patient_history and patient_history.strip():
                # Exclude default/empty values
                if patient_history.lower() not in ["nil", "nil significant", "no previous history", "none"]:
                    medical_history_data["patient_history"] = patient_history
        
        # Parse clinical examination - only if exists and not default
        clinical_examination_data = {}
        if franchise_investigation:
            for field in ["cardiovascular_system", "respiratory_system", "central_nervous_system", 
                         "locomotor_system", "skin"]:
                value = franchise_investigation.get(field)
                if value and value.strip() and value.lower() not in ["normal", "nil", "nil significant"]:
                    clinical_examination_data[field] = value
        
        # Parse ophthalmology data - UPDATED LOGIC
        ophthalmology_data = None
        if franchise_ophthalmology:
            try:
                ophthalmology_data = {}
                
                # Parse visual_acuity string - handle malformed JSON
                visual_acuity_str = franchise_ophthalmology.get('visual_acuity', '')
                print(f"Raw visual_acuity from DB: {visual_acuity_str}")
                
                if visual_acuity_str:
                    try:
                        # The string format is: { "distance": {"right": "7", "left": "7"}},{ "nearVision": {"right": "7", "left": "7"}},{ "colourVision": {"right": "7", "left": "7"}}
                        # We need to wrap it in array brackets and parse
                        
                        # Method 1: Convert to valid JSON array
                        json_array_str = '[' + visual_acuity_str + ']'
                        parsed_array = json.loads(json_array_str)
                        
                        # Merge all objects into one
                        parsed_va = {}
                        for obj in parsed_array:
                            parsed_va.update(obj)
                        
                        print(f"Merged parsed_va: {parsed_va}")
                        
                        # Transform to simplified structure without uncorrected/corrected
                        visual_acuity_data = {}
                        
                        # Add distance vision if available
                        if parsed_va.get("distance"):
                            visual_acuity_data["distance"] = {
                                "right": str(parsed_va.get("distance", {}).get("right", "6/6")),
                                "left": str(parsed_va.get("distance", {}).get("left", "6/6"))
                            }
                        
                        # Add near vision if available
                        if parsed_va.get("nearVision"):
                            visual_acuity_data["near_vision"] = {
                                "right": str(parsed_va.get("nearVision", {}).get("right", "N-6")),
                                "left": str(parsed_va.get("nearVision", {}).get("left", "N-6"))
                            }
                        
                        # Add color vision if available
                        if parsed_va.get("colourVision"):
                            visual_acuity_data["color_vision"] = {
                                "right": str(parsed_va.get("colourVision", {}).get("right", "Normal")),
                                "left": str(parsed_va.get("colourVision", {}).get("left", "Normal"))
                            }
                        
                        print(f"Final visual_acuity_data: {visual_acuity_data}")
                        
                        if visual_acuity_data:
                            ophthalmology_data["visual_acuity"] = visual_acuity_data
                            
                    except (json.JSONDecodeError, KeyError, AttributeError, TypeError) as e:
                        print(f"Error parsing visual_acuity: {str(e)}")
                        print(f"Error type: {type(e).__name__}")
                
                # Add remarks if available
                remarks = franchise_ophthalmology.get('remarks', '')
                if remarks and remarks.strip():
                    ophthalmology_data["remarks"] = remarks
                
                # Add patient complaints if available
                patient_complaints = franchise_ophthalmology.get('patient_complaints', '')
                if patient_complaints and patient_complaints.strip():
                    ophthalmology_data["patient_complaints"] = patient_complaints
                
                # Add right_eye and left_eye if available (for SPH, CYL, AXIS, ADD)
                right_eye = franchise_ophthalmology.get('right_eye')
                if right_eye:
                    try:
                        ophthalmology_data["right_eye"] = json.loads(right_eye) if isinstance(right_eye, str) else right_eye
                    except json.JSONDecodeError:
                        pass
                
                left_eye = franchise_ophthalmology.get('left_eye')
                if left_eye:
                    try:
                        ophthalmology_data["left_eye"] = json.loads(left_eye) if isinstance(left_eye, str) else left_eye
                    except json.JSONDecodeError:
                        pass
                
                # Add color_vision field if available (separate field from visual_acuity)
                color_vision = franchise_ophthalmology.get('color_vision', '')
                if color_vision and color_vision.strip():
                    ophthalmology_data["color_vision_status"] = color_vision
                
                # Add vision_status if available
                vision_status = franchise_ophthalmology.get('vision_status', '')
                if vision_status and vision_status.strip():
                    ophthalmology_data["vision_status"] = vision_status
                
                # Only include ophthalmology if there's actual data
                if not ophthalmology_data:
                    ophthalmology_data = None
                    
            except Exception as e:
                print(f"Error parsing ophthalmology data: {str(e)}")
                ophthalmology_data = None
        
        # Get all barcodes for this patient
        barcodes = []
        try:
            all_barcodes = franchise_billing_collection.find(
                {"employee_id": employee_id}, 
                {"barcode": 1, "_id": 0}
            )
            barcodes = [bc.get("barcode") for bc in all_barcodes if bc.get("barcode")]
            barcodes = list(dict.fromkeys(barcodes))
        except Exception:
            barcodes = []
        
        # Parse sample testdetails
        sample_testdetails = []
        if franchise_sample:
            try:
                sample_testdetails = json.loads(franchise_sample.get('testdetails', '[]'))
            except json.JSONDecodeError:
                sample_testdetails = []
        
        # Build patient details response - only with available data
        patient_details = {
            "patient_id": employee_id,
            "patientname": franchise_patient.get("employee_name"),
            "age": franchise_patient.get("age"),
            "gender": franchise_patient.get("gender"),
            "date": franchise_billing.get("created_date"),
            "barcode": barcode,
            "barcodes": barcodes,
            "testdetails": []
        }
        
        # Only add optional fields if they exist
        if franchise_patient.get("company_name"):
            patient_details["company_name"] = franchise_patient.get("company_name")
        
        if franchise_patient.get("department"):
            patient_details["department"] = franchise_patient.get("department")
        
        if franchise_patient.get("dob"):
            patient_details["dob"] = franchise_patient.get("dob")
        
        # Only add vitals if data exists
        if vitals_data:
            patient_details["vitals"] = {}
            for key in ["height_cm", "weight_kg", "bmi", "blood_pressure", "pulse", "spo2"]:
                if vitals_data.get(key):
                    display_key = key.replace("_cm", "").replace("_kg", "")
                    patient_details["vitals"][display_key] = vitals_data.get(key)
        
        # Only add medical history if data exists (excluding defaults)
        if medical_history_data:
            patient_details["medical_history"] = medical_history_data
        
        # Only add clinical examination if data exists
        if clinical_examination_data:
            patient_details["clinical_examination"] = clinical_examination_data
        
        # Only add ophthalmology if data exists
        if ophthalmology_data:
            patient_details["ophthalmology"] = ophthalmology_data
        
        # Only add investigation notes if they exist
        if investigation_notes:
            patient_details["investigation_notes"] = investigation_notes
        
        # Only add investigation file IDs if they exist
        if investigation_file_ids:
            patient_details["investigation_file_ids"] = investigation_file_ids
        
        # Process lab tests from TestValue model
        if test_values.exists():
            for test_value in test_values:
                try:
                    testvalue_details = json.loads(test_value.testdetails) if isinstance(test_value.testdetails, str) else test_value.testdetails
                    if not isinstance(testvalue_details, list):
                        continue
                    
                    for test_detail in testvalue_details:
                        testname = test_detail.get("testname")
                        if not testname:
                            continue
                        
                        # Find corresponding sample status
                        sample_status = None
                        for sample_test in sample_testdetails:
                            if sample_test.get("testname") == testname:
                                sample_status = sample_test
                                break
                        
                        # Build test detail object - only with available fields
                        test_response = {"testname": testname}
                        
                        if test_detail.get("department"):
                            test_response["department"] = test_detail.get("department")
                        
                        if test_detail.get("verified_by"):
                            test_response["verified_by"] = test_detail.get("verified_by")
                        
                        if test_detail.get("approve_by"):
                            test_response["approve_by"] = test_detail.get("approve_by")
                        
                        if test_detail.get("approve_time"):
                            test_response["approve_time"] = test_detail.get("approve_time")
                        
                        if sample_status:
                            if sample_status.get("samplecollected_time"):
                                test_response["samplecollected_time"] = sample_status.get("samplecollected_time")
                            if sample_status.get("received_time"):
                                test_response["received_time"] = sample_status.get("received_time")
                        
                        # Check if test has parameters
                        if test_detail.get("parameters"):
                            processed_parameters = []
                            for param in test_detail.get("parameters", []):
                                processed_param = {}
                                
                                if param.get("name"):
                                    processed_param["name"] = param.get("name")
                                if param.get("value"):
                                    processed_param["value"] = param.get("value")
                                if param.get("unit"):
                                    processed_param["unit"] = param.get("unit")
                                if param.get("specimen_type"):
                                    processed_param["specimen_type"] = param.get("specimen_type")
                                if param.get("reference_range"):
                                    processed_param["reference_range"] = param.get("reference_range")
                                if param.get("method"):
                                    processed_param["method"] = param.get("method")
                                
                                if processed_param:
                                    processed_parameters.append(processed_param)
                            
                            if processed_parameters:
                                test_response["parameters"] = processed_parameters
                        else:
                            # Add individual test fields only if they exist
                            if test_detail.get("method"):
                                test_response["method"] = test_detail.get("method")
                            if test_detail.get("specimen_type"):
                                test_response["specimen_type"] = test_detail.get("specimen_type")
                            if test_detail.get("value"):
                                test_response["value"] = test_detail.get("value")
                            if test_detail.get("unit"):
                                test_response["unit"] = test_detail.get("unit")
                            if test_detail.get("reference_range"):
                                test_response["reference_range"] = test_detail.get("reference_range")
                        
                        patient_details["testdetails"].append(test_response)
                        
                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"Error processing test: {str(e)}")
                    continue
        
        # Close MongoDB connection
        client.close()
        
        return JsonResponse(patient_details, safe=False)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)



# Add new endpoint to fetch individual files
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_investigation_file(request):
    file_id = request.GET.get('file_id')
    if not file_id:
        return JsonResponse({'error': 'File ID is required'}, status=400)
    
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        fs = gridfs.GridFS(db)
        
        file_obj_id = ObjectId(file_id)
        if not fs.exists(file_obj_id):
            return JsonResponse({'error': 'File not found'}, status=404)
        
        grid_out = fs.get(file_obj_id)
        file_data = grid_out.read()
        
        # Convert to base64 and return as JSON
        import base64
        base64_data = base64.b64encode(file_data).decode('utf-8')
        
        response_data = {
            'data': base64_data,
            'contentType': grid_out.content_type,
            'filename': grid_out.filename,
            'length': grid_out.length
        }
        
        client.close()
        return JsonResponse(response_data)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
