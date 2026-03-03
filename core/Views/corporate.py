from django.http import JsonResponse, HttpResponse
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
import gridfs
import base64
from bson.objectid import ObjectId
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
                        "testname": safe_get(test_data, "test_name", "N/A"),
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
                        "outsourced_by": detail.get("outsourced_by"),
                        "outsource_lab": detail.get("outsource_lab")
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
                        outsource_lab = update.get("outsource_lab")
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
                            test_entry['outsource_lab'] = outsource_lab
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
            employee_id = body.get('auth-user-id')
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
            
def get_department_status_corporate(test_list, employee_id, sample_status_map, test_value_map):
    """
    Determine status for each department based on test details
    Returns dict: {department_name: status}
    """
    department_status = {}
    
    # Group tests by department
    tests_by_dept = {}
    for test in test_list:
        dept = test.get('department', 'N/A')
        if dept and dept != 'N/A':  # Skip N/A departments
            if dept not in tests_by_dept:
                tests_by_dept[dept] = []
            tests_by_dept[dept].append(test)
    
    # If no valid departments found, return empty dict
    if not tests_by_dept:
        return {}
    
    # Determine status for each department
    for dept, tests in tests_by_dept.items():
        dept_test_ids = {t.get('test_id') for t in tests if t.get('test_id')}
        
        # Get test values for this employee_id
        all_test_values = test_value_map.get(employee_id, {}).get('testdetails', [])
        
        # Filter test values for this department - match by test_id
        dept_test_values = [tv for tv in all_test_values 
                           if tv.get('test_id') in dept_test_ids and not tv.get('rerun', False)]
        
        # Check sample collection status
        sample_tests = sample_status_map.get(employee_id, [])
        dept_samples = [st for st in sample_tests 
                       if st.get('test_id') in dept_test_ids]
        
        # Determine department status
        # First check if we have test values (higher priority)
        if dept_test_values:
            # Check if tests have values
            def has_test_values(test):
                parameters = test.get("parameters", [])
                if not parameters:
                    return bool(test.get("value"))
                return any(
                    param.get("value") is not None and str(param.get("value")).strip() != ""
                    for param in parameters
                )
            
            all_tested = all(has_test_values(tv) for tv in dept_test_values)
            
            # Check approval status
            approved_test_ids = {tv.get('test_id') for tv in dept_test_values if tv.get('approve', False)}
            all_approved = dept_test_ids.issubset(approved_test_ids) and len(approved_test_ids) > 0
            
            # Check dispatch status
            approved_dept_tests = [tv for tv in dept_test_values if tv.get('approve', False)]
            all_dispatched = all(tv.get('dispatch', False) for tv in approved_dept_tests) if approved_dept_tests else False
            
            if all_dispatched and all_approved:
                department_status[dept] = 'Dispatched'
            elif all_approved:
                department_status[dept] = 'Approved'
            elif all_tested:
                department_status[dept] = 'Tested'
            else:
                # Has test values but not fully tested - check sample status
                if dept_samples:
                    all_received = all(t.get('samplestatus') == 'Received' for t in dept_samples)
                    if all_received:
                        department_status[dept] = 'Received'
                    else:
                        department_status[dept] = 'In Progress'
                else:
                    department_status[dept] = 'In Progress'
        # No test values yet, check sample status
        elif dept_samples:
            all_collected = all(t.get('samplestatus') == 'Collected' for t in dept_samples)
            all_transferred = all(t.get('samplestatus') == 'Transferred' for t in dept_samples)
            all_received = all(t.get('samplestatus') == 'Received' for t in dept_samples)
            
            if all_received:
                department_status[dept] = 'Received'
            elif all_transferred:
                department_status[dept] = 'Transferred'
            elif all_collected:
                department_status[dept] = 'Collected'
            else:
                department_status[dept] = 'Pending'
        else:
            # No test values and no samples
            department_status[dept] = 'Pending'
    
    return department_status

@api_view(['GET','PATCH'])
@csrf_exempt
# @permission_classes([HasRoleAndDataPermission])
def corporate_overall_report(request):
    try:
        # MongoDB setup
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup  # Database name
        patients_collection = db.core_billing
        sample_status_colletion = db.core_sample
        franchise_patient_collection = db.core_employeeregistration
        
        # Test details from Diagnostics database
        diagnostics_db = client.Diagnostics  # Diagnostics database
        test_details_collection = diagnostics_db.core_testdetails  # Collection for test details

        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        selected_date = request.GET.get("selected_date")
        employee_id = request.GET.get("employee_id")
        
        print("Received query parameters:", request.GET)
        print(f"from_date: {from_date}, to_date: {to_date}, selected_date: {selected_date}, employee_id: {employee_id}")
        
        try:
            if selected_date:
                selected_date_parsed = datetime.strptime(selected_date, "%Y-%m-%d")
                from_date = selected_date_parsed
                to_date = selected_date_parsed + timedelta(days=1)
                print(f"Using selected_date: {selected_date}, parsed from_date: {from_date}, to_date: {to_date}")
            elif from_date and to_date:
                from_date = datetime.strptime(from_date, "%Y-%m-%d")
                to_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
                print(f"Using date range - parsed from_date: {from_date}, to_date: {to_date}")
            else:
                print("Missing date parameters")
                return JsonResponse({"error": "Either 'selected_date' or both 'from_date' and 'to_date' are required"}, status=400)
        except ValueError:
            print("Invalid date format received")
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        
        # Build MongoDB query - using created_date for consistency
        query = {}
        if employee_id:
            query["employee_id"] = employee_id
        query["created_date"] = {"$gte": from_date, "$lt": to_date}
        
        print(f"Corporate billing query: {query}")
        
        patients = list(patients_collection.find(query))
        print(f"Found {len(patients)} corporate billing records")
        if patients:
            print("Sample corporate billing record:", patients[0])
        
        if not patients:
            return JsonResponse([], safe=False)
        
        employee_ids = [p.get("employee_id") for p in patients if p.get("employee_id")]
        barcodes = [p.get("barcode") for p in patients if p.get("barcode")]
        
        print(f"Employee IDs for querying: {employee_ids}")
        print(f"Barcodes for querying: {barcodes}")
        
        # Get patient details from franchise_patient collection
        patient_details_map = {}
        if employee_ids:
            patient_details = franchise_patient_collection.find({"employee_id": {"$in": employee_ids}})
            for patient_detail in patient_details:
                patient_details_map[patient_detail.get("employee_id")] = patient_detail
        
        print(f"Fetched {len(patient_details_map)} employee detail records")
        
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
        
        print(f"Fetched {len(sample_status_list)} corporate sample status records")
        
        # For TestValue objects, use barcode to link with franchise_billing
        test_value_records = TestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(from_date.date(), to_date.date())
        ).values("barcode", "testdetails", "created_date")
        
        print(f"Fetched {len(test_value_records)} TestValue records")
        
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
        
        # Organize test value data using barcode mapping - COMBINE ALL RECORDS FOR SAME BARCODE
        test_value_map = {}
        for record in test_value_records:
            if not isinstance(record, dict):
                continue
                
            barcode = record.get("barcode")
            created_date = record.get("created_date")
            testdetails = record.get("testdetails")
            
            if not barcode or barcode not in barcode_to_patient_map:
                continue
                
            employee_id = barcode_to_patient_map[barcode]
            
            # Parse testdetails if it's a string
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails.strip('"'))
                except json.JSONDecodeError:
                    testdetails = []
            
            if employee_id not in test_value_map:
                test_value_map[employee_id] = {
                    "barcode": barcode,
                    "testdetails": [],
                    "created_date": created_date
                }
            
            # Add all test details from this record
            if isinstance(testdetails, list):
                test_value_map[employee_id]["testdetails"].extend(testdetails)
            
            # Update to latest created_date
            if created_date and test_value_map[employee_id]["created_date"]:
                if created_date > test_value_map[employee_id]["created_date"]:
                    test_value_map[employee_id]["created_date"] = created_date
            elif created_date:
                test_value_map[employee_id]["created_date"] = created_date
        
        print(f"Processed test value map with {len(test_value_map)} unique employee IDs")
        
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
          
            # Get sample tests
            sample_tests = sample_status_map.get(pid, [])
            
            # NEW: Extract test_ids and enrich with department info from MongoDB
            test_list = []
            test_ids = []
            departments_set = set()
            
            # Get test_ids from sample tests
            if isinstance(sample_tests, list):
                test_ids = [test.get("test_id") for test in sample_tests if isinstance(test, dict) and test.get("test_id")]
            
            # Enrich test details from MongoDB Diagnostics.core_testdetails with department
            if test_ids:
                for test_id in test_ids:
                    test_detail = test_details_collection.find_one(
                        {"test_id": test_id},
                        {"_id": 0, "test_id": 1, "test_name": 1, "department": 1}
                    )
                    if test_detail:
                        dept = test_detail.get("department", "N/A")
                        test_list.append({
                            "test_id": test_id,
                            "testname": test_detail.get("test_name", "N/A"),
                            "test_name": test_detail.get("test_name", "N/A"),
                            "department": dept
                        })
                        # Collect department
                        if dept and dept != "N/A":
                            departments_set.add(dept)
            
            # Fallback - use sample tests if no enrichment available
            if not test_list and sample_tests:
                test_list = sample_tests
            
            # Get test names
            testnames = ", ".join([
                test.get("testname", test.get("test_name", "")) if isinstance(test, dict) else str(test)
                for test in test_list
            ])
            no_of_tests = len(test_list)
            
            # Format departments as comma-separated string
            department = ", ".join(sorted(departments_set)) if departments_set else "N/A"
            
            # Age handling
            age_value = patient_detail.get("age", "N/A")
            age_type = patient_detail.get("age_type", "")
            age = f"{age_value} {age_type}" if age_type else str(age_value)            
            
            # Get barcode
            barcode = patient.get("barcode")
            
            # Get combined test value data
            latest_test_data = test_value_map.get(pid, {})
            all_test_values = latest_test_data.get("testdetails", [])
            test_created_date = latest_test_data.get("created_date", None)
            
            # Use barcode from test_value_map if available
            if not barcode and latest_test_data.get("barcode"):
                barcode = latest_test_data.get("barcode")
            
            # Filter out rerun records
            valid_test_values = []
            unapproved_tests = []
            if all_test_values:
                for test_record in all_test_values:
                    if not test_record.get("rerun", False):
                        valid_test_values.append(test_record)
                        if not test_record.get("approve", False):
                            unapproved_tests.append(test_record)
            
            print(f"Employee ID: {pid}, Barcode: {barcode}, Total test records: {len(all_test_values)}, Valid (non-rerun) tests: {len(valid_test_values)}, Unapproved tests: {len(unapproved_tests)}")
            
            # Sample collection status and timestamps
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
            
            # NEW: Extract collection time and date
            collection_time_val = "N/A"
            collected_date_val = "N/A"
            if sample_tests:
                for t in sample_tests:
                    st = t.get("samplecollected_time")
                    if st:
                        try:
                            if isinstance(st, str):
                                if 'T' in st:
                                    dt = datetime.fromisoformat(st)
                                else:
                                    try:
                                        dt = datetime.strptime(st, "%Y-%m-%d %H:%M:%S")
                                    except ValueError:
                                        dt = None
                                
                                if dt:
                                    collection_time_val = dt.strftime("%I:%M %p")
                                    collected_date_val = dt.strftime("%d-%m-%Y")
                                else:
                                    collection_time_val = st
                            elif isinstance(st, datetime):
                                collection_time_val = st.strftime("%I:%M %p")
                                collected_date_val = st.strftime("%d-%m-%Y")
                            
                            if collection_time_val != "N/A":
                                break
                        except Exception:
                            collection_time_val = str(st)
                            break
            
            # STATUS DETERMINATION
            status = "Registered"  # Default status
            
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
            
            # NEW: Get individual test statuses
            individual_test_statuses = []
            if pid and test_list:
                for test in test_list:
                    test_id = test.get('test_id')
                    test_name = test.get('testname') or test.get('test_name', 'N/A')
                    
                    # Get sample info
                    sample_info = next((t for t in sample_tests if t.get('test_id') == test_id), {})
                    
                    # Get test value info
                    test_value_info = next((t for t in valid_test_values if t.get('test_id') == test_id), {})
                    
                    # Determine individual test status
                    test_status = "Registered"
                    
                    if sample_info:
                        if sample_info.get('samplestatus') == 'Collected':
                            test_status = "Collected"
                        if sample_info.get('samplestatus') == 'Transferred':
                            test_status = "Transferred"
                        if sample_info.get('samplestatus') == 'Received':
                            test_status = "Received"
                        if sample_info.get('samplestatus') == 'Rejected':
                            test_status = "Rejected"
                    
                    if test_value_info:
                        # Check if test has values
                        has_values = False
                        parameters = test_value_info.get("parameters", [])
                        if not parameters:
                            has_values = bool(test_value_info.get("value"))
                        else:
                            has_values = any(
                                param.get("value") is not None and str(param.get("value")).strip() != ""
                                for param in parameters
                            )
                        
                        if has_values:
                            test_status = "Tested"
                        
                        if test_value_info.get('approve'):
                            test_status = "Approved"
                        
                        if test_value_info.get('dispatch'):
                            test_status = "Dispatched"
                    
                    individual_test_statuses.append({
                        'test_id': test_id,
                        'test_name': test_name,
                        'status': test_status,
                    })
            
            # Test value status logic (using test_id for comparison)
            if valid_test_values:
                # Check testing status
                def has_test_values(test):
                    parameters = test.get("parameters", [])
                    if not parameters:
                        return bool(test.get("value"))
                    return any(
                        param.get("value") is not None and str(param.get("value")).strip() != ""
                        for param in parameters
                    )
                
                all_tested = all(has_test_values(t) for t in valid_test_values)
                partially_tested = any(has_test_values(t) for t in valid_test_values)
                
                # Get test_ids from sample tests (since billing might not have test_id)
                all_ordered_test_ids = {
                    str(test.get("test_id", "")).strip() 
                    for test in test_list 
                    if isinstance(test, dict) and test.get("test_id")
                }
                
                # Get approved and dispatch test_ids from ALL test value records
                approved_test_ids = {
                    str(t.get("test_id", "")).strip() 
                    for t in valid_test_values 
                    if t.get("approve", False) and t.get("test_id")
                }
                
                dispatch_test_ids = {
                    str(t.get("test_id", "")).strip() 
                    for t in valid_test_values 
                    if t.get("dispatch", False) and t.get("test_id")
                }
                
                # Check approval status based on test_id
                all_approved = False
                partially_approved = False
                
                if len(all_ordered_test_ids) > 0:
                    # Compare test IDs
                    if all_ordered_test_ids.issubset(approved_test_ids) and len(approved_test_ids) == len(all_ordered_test_ids):
                        all_approved = True
                    elif len(approved_test_ids) > 0:
                        partially_approved = True
                    
                    # Fallback - check if all individual tests are approved
                    if not all_approved and valid_test_values:
                        approved_count = sum(1 for t in valid_test_values if t.get("approve", False))
                        total_expected = no_of_tests
                        
                        if approved_count == total_expected and approved_count > 0:
                            all_approved = True
                            partially_approved = False
                        elif approved_count > 0:
                            partially_approved = True
                
                # NEW: Check dispatch status (matching overall_report and franchise_overall_report)
                all_dispatched = False
                partially_dispatched = False
                
                if len(all_ordered_test_ids) > 0:
                    # Compare test_ids for dispatch
                    if all_ordered_test_ids.issubset(dispatch_test_ids) and len(dispatch_test_ids) == len(all_ordered_test_ids):
                        all_dispatched = True
                    elif len(dispatch_test_ids) > 0:
                        partially_dispatched = True
                    
                    # Fallback - check if all individual tests are dispatched
                    if not all_dispatched and valid_test_values:
                        dispatch_count = sum(1 for t in valid_test_values if t.get("dispatch", False))
                        total_expected = no_of_tests
                        
                        if dispatch_count == total_expected and dispatch_count > 0:
                            all_dispatched = True
                            partially_dispatched = False
                        elif dispatch_count > 0:
                            partially_dispatched = True
                
                print(f"Approval status for Employee {pid}: all_approved={all_approved}, partially_approved={partially_approved}")
                print(f"Dispatch status for Employee {pid}: all_dispatched={all_dispatched}, partially_dispatched={partially_dispatched}")
                print(f"Ordered test IDs: {all_ordered_test_ids}, Approved test IDs: {approved_test_ids}, Dispatch test IDs: {dispatch_test_ids}")
                print(f"Testing status: all_tested={all_tested}, partially_tested={partially_tested}")
                
                # Set status based on testing progress
                if all_tested:
                    status = "Tested"
                elif partially_tested:
                    status = "Partially Tested"
                
                # Set status based on approval
                if all_approved:
                    status = "Approved"
                elif partially_approved:
                    status = "Partially Approved"
                
                # Set status based on dispatch
                if all_dispatched:
                    status = "Dispatched"
                elif partially_dispatched:
                    status = "Partially Dispatched"
            
            print(f"Final status for Employee {pid}: {status}")
            
            # NEW: Get department-wise status
            department_statuses = {}
            if pid and test_list:
                department_statuses = get_department_status_corporate(
                    test_list, 
                    pid, 
                    sample_status_map, 
                    test_value_map
                )
            
            # Handle date formatting - use created_date consistently
            created_date = patient.get("created_date")
            formatted_date = "N/A"
            registration_date = "N/A"
            
            if created_date:
                if isinstance(created_date, datetime):
                    formatted_date = created_date.strftime("%Y-%m-%d")
                    registration_date = created_date.isoformat()
                else:
                    # Handle string dates
                    try:
                        parsed_date = datetime.strptime(str(created_date), "%Y-%m-%d")
                        formatted_date = parsed_date.strftime("%Y-%m-%d")
                        registration_date = parsed_date.isoformat()
                    except:
                        formatted_date = str(created_date)
                        registration_date = str(created_date)
            
            test_created_date_formatted = None
            if test_created_date:
                if isinstance(test_created_date, datetime):
                    test_created_date_formatted = test_created_date.isoformat()
                else:
                    test_created_date_formatted = str(test_created_date)
            
            # Final patient object
            formatted_data.append({
                "date": formatted_date,
                "registration_date": registration_date,
                "patient_id": pid,
                "patient_name": patient_detail.get("employee_name", "N/A"),
                "gender": patient_detail.get("gender", "N/A"),
                "age": age,
                "age_type": age_type,
                "email": patient_detail.get("email", "N/A"),
                "branch": patient_detail.get("company_id", "N/A"),
                "test_names": testnames,
                "department": department,  # NEW
                "department_statuses": department_statuses,  # NEW
                "test_statuses": individual_test_statuses,  # NEW
                "no_of_tests": no_of_tests,
                "barcode": barcode,
                "status": status,
                "test_created_date": test_created_date_formatted,
                "collection_time": collection_time_val,  # NEW
                "collected_date": collected_date_val,  # NEW
            })
        
        # Close MongoDB connection
        client.close()
        
        return JsonResponse(formatted_data, safe=False)
    
    except Exception as e:
        print("Critical Error:", str(e))
        print(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)

@api_view(['GET'])
# @permission_classes([HasRoleAndDataPermission])
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
        
        # Connect to Diagnostics database for core_testdetails
        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails
        
        # Connect to global database for employee profiles
        global_db = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs = gridfs.GridFS(global_db)
        
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
        
        # Helper function to get parameter details by index or test_code
        def get_parameter_from_core(core_test, device_id, test_code=None, param_index=None):
            """
            Get parameter details from core_testdetails
            Supports both dict (device_id-keyed) and list formats
            Uses param_index for accurate matching when available
            """
            core_parameters = core_test.get("parameters", {})
            params_list = []
            
            # Case 1: parameters is a dictionary with device_id keys
            if isinstance(core_parameters, dict):
                # Try to get parameters for the specific device_id
                if device_id and device_id != "N/A" and device_id in core_parameters:
                    params_list = core_parameters[device_id]
                else:
                    # Use first available device's parameters
                    if len(core_parameters) > 0:
                        first_device = list(core_parameters.keys())[0]
                        params_list = core_parameters[first_device]
            
            # Case 2: parameters is a list (like PT test)
            elif isinstance(core_parameters, list):
                params_list = core_parameters
            
            # Ensure params_list is actually a list
            if not isinstance(params_list, list):
                return None
            
            # If param_index is provided, use it directly (most accurate)
            if param_index is not None and 0 <= param_index < len(params_list):
                return params_list[param_index]
            
            # Fallback: Find by test_code (may not be unique)
            if test_code:
                matching_params = [p for p in params_list if isinstance(p, dict) and p.get("test_code") == test_code]
                if matching_params:
                    return matching_params[0]
            
            return None
        
        # Helper function to get employee signature data
        def get_employee_signature_data(employee_id):
            """
            Fetch employee profile and signature image from MongoDB
            Returns dict with employeeName, designation, and signature base64
            """
            if not employee_id:
                return None
            
            try:
                # Get employee profile
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None
                
                employee_name = profile.get("employeeName", "")
                designation = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                
                signature_base64 = None
                if signature_file_id:
                    try:
                        # Convert string ID to ObjectId if needed
                        from bson import ObjectId
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        
                        # Fetch signature image from GridFS
                        signature_file = fs.get(signature_file_id)
                        signature_bytes = signature_file.read()
                        
                        # Convert to base64
                        import base64
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {employee_id}: {str(e)}")
                
                return {
                    "employeeName": employee_name,
                    "designation": designation,
                    "signatureBase64": signature_base64
                }
            except Exception as e:
                print(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None
        
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
            "patientname": franchise_patient.get("employee_name", ""),
            "age": franchise_patient.get("age", ""),
            "age_type": franchise_patient.get("age_type", "Years"),
            "gender": franchise_patient.get("gender", ""),
            "date": franchise_billing.get("created_date"),
            "barcode": franchise_billing.get("barcode", ""),
            "barcodes": barcodes,
            "branch": franchise_billing.get("franchise_id", ""),
            "refby": "SELF",
            "testdetails": []
        }
        
        # Collect all unique approvers across all test values
        all_approvers = set()
        
        # Process ONLY tests that exist in TestValue model
        for test_value in test_values:
            try:
                # Parse testdetails JSON from TestValue model
                testvalue_details = json.loads(test_value.testdetails) if isinstance(test_value.testdetails, str) else test_value.testdetails
                if not isinstance(testvalue_details, list):
                    continue
                    
                # Process each test in TestValue
                for test_detail in testvalue_details:
                    test_id = test_detail.get("test_id")
                    testname = test_detail.get("testname")
                    device_id = test_detail.get("device_id", "N/A")
                    
                    if not test_id:
                        continue
                    
                    # Fetch test details from core_testdetails using test_id
                    core_test = core_testdetails_collection.find_one({"test_id": test_id})
                    
                    # Find corresponding sample status using test_id
                    sample_status = None
                    for sample_test in sample_testdetails:
                        if sample_test.get("test_id") == test_id or sample_test.get("testname") == testname:
                            sample_status = sample_test
                            break
                    
                    # Find corresponding billing info for MRP using test_id
                    billing_info = None
                    for billing_test in billing_testdetails:
                        if billing_test.get("test_id") == test_id:
                            billing_info = billing_test
                            break
                    
                    # Get core test details
                    if core_test:
                        department = core_test.get("department", "N/A")
                        NABL = core_test.get("NABL", False)
                        specimen_type = core_test.get("specimen_type", "N/A")
                        testname = core_test.get("test_name", testname)
                    else:
                        department = test_detail.get("department", sample_status.get("department", "") if sample_status else "")
                        NABL = test_detail.get("NABL", "")
                        specimen_type = test_detail.get("specimen_type", "")
                    
                    # Get test value details
                    outsourced = test_detail.get("outsourced", False)
                    comment = test_detail.get("comment", "")
                    verified_by = test_detail.get("verified_by", "N/A")
                    approve_by = test_detail.get("approve_by", "N/A")
                    approve_time = test_detail.get("approve_time", "N/A")
                    
                    # Collect approver ID
                    if approve_by:
                        all_approvers.add(approve_by)
                    
                    # Build test detail object
                    test_response = {
                        "test_id": test_id,
                        "testname": testname,
                        "department": department,
                        "NABL": NABL,
                        "specimen_type": specimen_type,
                        "outsourced": outsourced,
                        "comment": comment,
                        "verified_by": verified_by,
                        "approve_by": approve_by,
                        "approve_time": approve_time,
                        "samplecollected_time": sample_status.get("samplecollected_time") if sample_status else None,
                        "received_time": sample_status.get("received_time") if sample_status else None
                    }
                    
                    # Add MRP if available from billing
                    if billing_info:
                        test_response["MRP"] = billing_info.get("MRP", "N/A")
                    
                    # Handle parameters - match with core_testdetails using INDEX
                    parameters = test_detail.get("parameters", [])
                    if parameters and len(parameters) > 0:
                        enriched_parameters = []
                        
                        # Use index-based matching for accurate parameter retrieval
                        for param_index, param_value in enumerate(parameters):
                            test_code = param_value.get("test_code")
                            value = param_value.get("value", "")
                            param_comment = param_value.get("comment", "")
                            
                            # Get parameter definition using INDEX (most accurate)
                            if core_test:
                                param_def = get_parameter_from_core(
                                    core_test, 
                                    device_id, 
                                    test_code=test_code, 
                                    param_index=param_index
                                )
                            else:
                                param_def = None
                            
                            if param_def:
                                enriched_param = {
                                    "name": param_def.get("test_name", ""),
                                    "test_code": test_code,
                                    "value": value,
                                    "unit": param_def.get("unit", ""),
                                    "reference_range": param_def.get("reference_range", ""),
                                    "method": param_def.get("method", ""),
                                    "specimen_type": specimen_type,
                                    "sub_title": param_def.get("sub_title", ""),
                                    "value_option": param_def.get("value_option", []),
                                    "comment": param_comment
                                }
                                enriched_parameters.append(enriched_param)
                            else:
                                # Fallback if parameter definition not found
                                enriched_param = {
                                    "name": param_value.get("name", "N/A"),
                                    "test_code": test_code,
                                    "value": value,
                                    "unit": param_value.get("unit", "N/A"),
                                    "reference_range": param_value.get("reference_range", "N/A"),
                                    "method": param_value.get("method", "N/A"),
                                    "specimen_type": specimen_type,
                                    "sub_title": param_value.get("sub_title", ""),
                                    "value_option": [],
                                    "comment": param_comment
                                }
                                enriched_parameters.append(enriched_param)
                        
                        test_response["parameters"] = enriched_parameters
                    else:
                        # No parameters - single test with value
                        if core_test:
                            test_response.update({
                                "method": core_test.get("method", ""),
                                "value": test_detail.get("value", ""),
                                "unit": core_test.get("unit", ""),
                                "reference_range": core_test.get("reference_range", ""),
                                "sub_title": test_detail.get("sub_title", "")
                            })
                        else:
                            test_response.update({
                                "method": test_detail.get("method", ""),
                                "value": test_detail.get("value", ""),
                                "unit": test_detail.get("unit", ""),
                                "reference_range": test_detail.get("reference_range", ""),
                                "sub_title": test_detail.get("sub_title", "")
                            })
                    
                    patient_details["testdetails"].append(test_response)
                    
            except (json.JSONDecodeError, AttributeError):
                continue
        
        # Fetch signature data for all approvers
        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)
        
        # Close MongoDB connection
        client.close()
        
        # Return empty if no test details found
        if not patient_details["testdetails"]:
            return JsonResponse({'error': 'No approved test records found'}, status=404)
        
        # Prepare final response with signatures
        response_data = {
            "patient_data": patient_details,
            "signatures": signatures_data
        }
        
        return JsonResponse(response_data, safe=False)
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
    
@api_view(['GET','PATCH'])
@csrf_exempt
# @permission_classes([HasRoleAndDataPermission])
def corporate_approval_report(request):
    try:
        # MongoDB setup
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup  # Database name
        patients_collection = db.core_billing  # Changed to corporate_billing
        sample_status_colletion = db.core_sample # Collection name for sample 
        franchise_patient_collection = db.core_employeeregistration  # Collection for patient details
        overall_approval_collection = db.overallApproval  # NEW: Collection for approval status
        
        # Connect to Diagnostics database for core_testdetails
        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

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
        
        # NEW: Fetch approval status from overallApproval collection using barcode
        approval_status_map = {}
        if barcodes:
            approval_records = overall_approval_collection.find({
                "barcode": {"$in": barcodes}
            })
            for approval in approval_records:
                barcode = approval.get("barcode")
                if barcode:
                    approval_status_map[barcode] = {
                        "status": approval.get("status", "approved"),
                        "approved_date": approval.get("approved_date"),
                        "impression": approval.get("impression"),
                        "remarks": approval.get("remarks")
                    }
        
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
        
        # Create a mapping from barcode to employee_id from billing records
        barcode_to_patient_map = {}
        for patient in patients:
            if patient.get("barcode") and patient.get("employee_id"):
                barcode_to_patient_map[patient.get("barcode")] = patient.get("employee_id")
        
        # UPDATED: Organize sample status data using barcode mapping and fetch test names from core_testdetails
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
                    
                    # UPDATED: Enrich test details with names from core_testdetails
                    enriched_testdetails = []
                    for test in parsed_testdetails:
                        if isinstance(test, dict):
                            test_id = test.get("test_id")
                            
                            # Fetch test name from core_testdetails if test_id exists
                            if test_id:
                                core_test = core_testdetails_collection.find_one({"test_id": test_id})
                                if core_test:
                                    # Use test_name from core_testdetails
                                    test["testname"] = core_test.get("test_name", test.get("testname", ""))
                            
                            enriched_testdetails.append(test)
                    
                    if enriched_testdetails:
                        sample_status_map.setdefault(employee_id_key, []).extend(enriched_testdetails)
        
        # Final result
        formatted_data = []
        for patient in patients:
            # SAFETY CHECK: Ensure patient is a dict
            if not isinstance(patient, dict):
                print(f"Warning: Patient record is not a dict: {type(patient)}")
                continue
                
            pid = patient.get("employee_id", "N/A")
            barcode = patient.get("barcode")
            
            # Get patient details from franchise_patient collection
            patient_detail = patient_details_map.get(pid, {})
            # SAFETY CHECK: Ensure patient_detail is a dict
            if not isinstance(patient_detail, dict):
                patient_detail = {}
          
            # FIXED: Get test names from core_sample (now enriched with core_testdetails names)
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
            
            # NEW: Simplified status determination - only check overallApproval collection
            if barcode and barcode in approval_status_map:
                status = "Approved"
            else:
                status = "Pending"
            
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
# @permission_classes([HasRoleAndDataPermission])
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
        franchise_overall_approval_collection = db.overallApproval
        franchise_company_collection = db.core_company  # NEW: Company collection
        
        # Connect to Diagnostics database for core_testdetails
        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails
        
        # Connect to global database for employee profiles
        global_db = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs = gridfs.GridFS(global_db)
        
        # Helper function to get parameter details by test_code
        def get_parameter_from_core(core_test, device_id, test_code):
            """
            Get parameter details from core_testdetails based on test_code
            Supports both dict (device_id-keyed) and list formats
            """
            core_parameters = core_test.get("parameters", {})
            params_list = []
            
            # Case 1: parameters is a dictionary with device_id keys
            if isinstance(core_parameters, dict):
                # Try to get parameters for the specific device_id
                if device_id and device_id != "N/A" and device_id in core_parameters:
                    params_list = core_parameters[device_id]
                else:
                    # Use first available device's parameters
                    if len(core_parameters) > 0:
                        first_device = list(core_parameters.keys())[0]
                        params_list = core_parameters[first_device]
            
            # Case 2: parameters is a list (like PT test)
            elif isinstance(core_parameters, list):
                params_list = core_parameters
            
            # Ensure params_list is actually a list
            if not isinstance(params_list, list):
                return None
            
            # Find by test_code
            if test_code:
                matching_params = [p for p in params_list if isinstance(p, dict) and p.get("test_code") == test_code]
                if matching_params:
                    return matching_params[0]
            
            return None
        
        # Helper function to get employee signature data
        def get_employee_signature_data(employee_id):
            """
            Fetch employee profile and signature image from MongoDB
            Returns dict with employeeName, designation, and signature base64
            """
            if not employee_id:
                return None
            
            try:
                # Get employee profile
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None
                
                employee_name = profile.get("employeeName", "")
                designation = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                
                signature_base64 = None
                if signature_file_id:
                    try:
                        # Convert string ID to ObjectId if needed
                        from bson import ObjectId
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        
                        # Fetch signature image from GridFS
                        signature_file = fs.get(signature_file_id)
                        signature_bytes = signature_file.read()
                        
                        # Convert to base64
                        import base64
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {employee_id}: {str(e)}")
                
                return {
                    "employeeName": employee_name,
                    "designation": designation,
                    "signatureBase64": signature_base64
                }
            except Exception as e:
                print(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None
        
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
        
        # NEW: Get company data based on company_id from employee registration
        company_data = None
        company_id = franchise_patient.get("company_id")
        if company_id:
            company_data = franchise_company_collection.find_one({"company_id": company_id})
        
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
        
        # Get overall approval data
        franchise_overall_approval = franchise_overall_approval_collection.find_one({
            "barcode": barcode,
            "status": "approved"
        })
        
        # Get test values from Django model
        test_values = TestValue.objects.filter(barcode=barcode)
        
        # Parse vitals from investigation
        vitals_data = {}
        if franchise_investigation:
            try:
                vitals_raw = json.loads(franchise_investigation.get('vitals', '{}'))
                for key, value in vitals_raw.items():
                    if value and str(value).strip() and str(value).strip() != "0":
                        vitals_data[key] = value
            except json.JSONDecodeError:
                vitals_data = {}
        
        # Store investigation file IDs, notes, and X-ray report
        investigation_file_ids = {}
        investigation_notes = {}
        
        if franchise_investigation:
            # File IDs
            for file_field in ["ecg_file", "pft_file", "audiometric_file", "xrayfilm_file"]:
                file_id = franchise_investigation.get(file_field)
                if file_id:
                    investigation_file_ids[file_field] = file_id
            
            # Notes - including X-ray notes and report
            for note_field, key in [
                ("ecg_notes", "ecg_notes"),
                ("pft_notes", "pft_notes"),
                ("audiometry_notes", "audiometry_notes"),
                ("xray_notes", "xray_notes"),
                ("xray_report", "xray_report")
            ]:
                note_value = franchise_investigation.get(note_field)
                if note_value and note_value.strip():
                    investigation_notes[key] = note_value
        
        # Parse medical history
        medical_history_data = {}
        if franchise_investigation:
            patient_history = franchise_investigation.get("patient_history")
            if patient_history and patient_history.strip():
                if patient_history.lower() not in ["nil", "nil significant", "no previous history", "none"]:
                    medical_history_data["patient_history"] = patient_history
        
        # Parse clinical examination
        clinical_examination_data = {}
        if franchise_investigation:
            for field in ["cardiovascular_system", "respiratory_system", "central_nervous_system", 
                         "locomotor_system", "skin"]:
                value = franchise_investigation.get(field)
                if value and value.strip() and value.lower() not in ["normal", "nil", "nil significant"]:
                    clinical_examination_data[field] = value
        
        # Parse ophthalmology data - now includes ocular movement
        ophthalmology_data = None
        if franchise_ophthalmology:
            try:
                ophthalmology_data = {}
                
                # Parse visual_acuity string
                visual_acuity_str = franchise_ophthalmology.get('visual_acuity', '')
                print(f"Raw visual_acuity from DB: {visual_acuity_str}")
                
                if visual_acuity_str:
                    try:
                        # Convert to valid JSON array
                        json_array_str = '[' + visual_acuity_str + ']'
                        parsed_array = json.loads(json_array_str)
                        
                        # Merge all objects into one
                        parsed_va = {}
                        for obj in parsed_array:
                            parsed_va.update(obj)
                        
                        print(f"Merged parsed_va: {parsed_va}")
                        
                        # Transform to simplified structure
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
                        
                        # Add ocular movement if available
                        if parsed_va.get("ocularmovement"):
                            visual_acuity_data["ocularmovement"] = {
                                "right": str(parsed_va.get("ocularmovement", {}).get("right", "Normal")),
                                "left": str(parsed_va.get("ocularmovement", {}).get("left", "Normal"))
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
                
                # Add right_eye and left_eye if available
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
                
                # Add color_vision field if available
                color_vision = franchise_ophthalmology.get('color_vision', '')
                if color_vision and color_vision.strip():
                    ophthalmology_data["color_vision_status"] = color_vision
                
                # Add vision_status if available
                vision_status = franchise_ophthalmology.get('vision_status', '')
                if vision_status and vision_status.strip():
                    ophthalmology_data["vision_status"] = vision_status
                
                # Add optometrist_name if available
                optometrist_name = franchise_ophthalmology.get('optometrist_name', '')
                if optometrist_name and optometrist_name.strip():
                    ophthalmology_data["optometrist_name"] = optometrist_name
                
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
        
        # Build patient details response
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
        
        # UPDATED: Add company_name from core_company collection
        if company_data and company_data.get("company_name"):
            patient_details["company_name"] = company_data.get("company_name")
        
        # Add department if it exists
        if franchise_patient.get("department"):
            patient_details["department"] = franchise_patient.get("department")
        
        # Add dob if it exists
        if franchise_patient.get("dob"):
            patient_details["dob"] = franchise_patient.get("dob")
        
        # Add vitals if data exists
        if vitals_data:
            patient_details["vitals"] = {}
            for key in ["height_cm", "weight_kg", "bmi", "blood_pressure", "pulse", "spo2"]:
                if vitals_data.get(key):
                    display_key = key.replace("_cm", "").replace("_kg", "")
                    patient_details["vitals"][display_key] = vitals_data.get(key)
        
        # Add medical history if data exists
        if medical_history_data:
            patient_details["medical_history"] = medical_history_data
        
        # Add clinical examination if data exists
        if clinical_examination_data:
            patient_details["clinical_examination"] = clinical_examination_data
        
        # Add ophthalmology if data exists
        if ophthalmology_data:
            patient_details["ophthalmology"] = ophthalmology_data
        
        # Add investigation notes if they exist
        if investigation_notes:
            patient_details["investigation_notes"] = investigation_notes
        
        # Add investigation file IDs if they exist
        if investigation_file_ids:
            patient_details["investigation_file_ids"] = investigation_file_ids
        
        # Add final assessment from overall approval (impression and remarks only)
        if franchise_overall_approval:
            final_assessment = {}
            
            # Get impression
            impression = franchise_overall_approval.get("impression", "")
            if impression and impression.strip():
                final_assessment["impression"] = impression
            
            # Get remarks
            remarks = franchise_overall_approval.get("remarks", "")
            if remarks and remarks.strip():
                final_assessment["remarks"] = remarks
            
            # Only add if there's data
            if final_assessment:
                patient_details["final_assessment"] = final_assessment
        
        # Collect all unique approvers across all test values
        all_approvers = set()
        
        # UPDATED: Process lab tests from TestValue model with parameter enrichment from core_testdetails
        if test_values.exists():
            for test_value in test_values:
                try:
                    testvalue_details = json.loads(test_value.testdetails) if isinstance(test_value.testdetails, str) else test_value.testdetails
                    if not isinstance(testvalue_details, list):
                        continue
                    
                    for test_detail in testvalue_details:
                        test_id = test_detail.get("test_id")
                        testname = test_detail.get("testname")
                        device_id = test_detail.get("device_id", "N/A")
                        
                        if not test_id:
                            continue
                        
                        # UPDATED: Fetch test details from core_testdetails using test_id
                        core_test = core_testdetails_collection.find_one({"test_id": test_id})
                        if core_test:
                            testname = core_test.get("test_name", testname)
                            # Get specimen_type from core_test
                            specimen_type = core_test.get("specimen_type", "N/A")
                            # UPDATED: Get department from core_test
                            department = core_test.get("department", test_detail.get("department", ""))
                        else:
                            specimen_type = test_detail.get("specimen_type", "")
                            department = test_detail.get("department", "")
                        
                        if not testname:
                            continue
                        
                        # Collect approver ID
                        approve_by = test_detail.get("approve_by", "")
                        if approve_by:
                            all_approvers.add(approve_by)
                        
                        # Find corresponding sample status
                        sample_status = None
                        for sample_test in sample_testdetails:
                            if sample_test.get("test_id") == test_id or sample_test.get("testname") == testname:
                                sample_status = sample_test
                                break
                        
                        # Build test detail object
                        test_response = {"testname": testname}
                        
                        # UPDATED: Use department from core_testdetails (with fallback to stored data)
                        if department:
                            test_response["department"] = department
                        
                        if test_detail.get("verified_by"):
                            test_response["verified_by"] = test_detail.get("verified_by")
                        
                        if test_detail.get("approve_by"):
                            test_response["approve_by"] = test_detail.get("approve_by")
                        
                        if test_detail.get("approve_time"):
                            test_response["approve_time"] = test_detail.get("approve_time")
                        
                        # Add outsourced and comment from test_detail
                        outsourced = test_detail.get("outsourced", False)
                        if outsourced:
                            test_response["outsourced"] = outsourced
                        
                        comment = test_detail.get("comment", "")
                        if comment:
                            test_response["comment"] = comment
                        
                        if sample_status:
                            if sample_status.get("samplecollected_time"):
                                test_response["samplecollected_time"] = sample_status.get("samplecollected_time")
                            if sample_status.get("received_time"):
                                test_response["received_time"] = sample_status.get("received_time")
                        
                        # UPDATED: Check if test has parameters and enrich from core_testdetails
                        if test_detail.get("parameters"):
                            processed_parameters = []
                            for param in test_detail.get("parameters", []):
                                test_code = param.get("test_code")
                                value = param.get("value", "")
                                param_comment = param.get("comment", "")
                                
                                # Get parameter definition from core_testdetails based on test_code
                                if core_test and test_code:
                                    param_def = get_parameter_from_core(core_test, device_id, test_code)
                                else:
                                    param_def = None
                                
                                processed_param = {}
                                
                                # Use core_testdetails data if available, otherwise fallback to stored data
                                if param_def:
                                    processed_param["name"] = param_def.get("test_name", param.get("name", ""))
                                    processed_param["value"] = value
                                    processed_param["unit"] = param_def.get("unit", "")
                                    processed_param["specimen_type"] = specimen_type
                                    processed_param["reference_range"] = param_def.get("reference_range", "")
                                    processed_param["method"] = param_def.get("method", "")
                                    if param_def.get("sub_title"):
                                        processed_param["sub_title"] = param_def.get("sub_title")
                                    # Add parameter comment if available
                                    if param_comment:
                                        processed_param["comment"] = param_comment
                                else:
                                    # Fallback to stored data
                                    if param.get("name"):
                                        processed_param["name"] = param.get("name")
                                    if value:
                                        processed_param["value"] = value
                                    if param.get("unit"):
                                        processed_param["unit"] = param.get("unit")
                                    if param.get("specimen_type"):
                                        processed_param["specimen_type"] = param.get("specimen_type")
                                    if param.get("reference_range"):
                                        processed_param["reference_range"] = param.get("reference_range")
                                    if param.get("method"):
                                        processed_param["method"] = param.get("method")
                                    if param.get("sub_title"):
                                        processed_param["sub_title"] = param.get("sub_title")
                                    # Add parameter comment if available
                                    if param_comment:
                                        processed_param["comment"] = param_comment
                                
                                if processed_param:
                                    processed_parameters.append(processed_param)
                            
                            if processed_parameters:
                                test_response["parameters"] = processed_parameters
                        else:
                            # Add individual test fields - prefer core_testdetails data
                            if core_test:
                                if core_test.get("method"):
                                    test_response["method"] = core_test.get("method")
                                if specimen_type:
                                    test_response["specimen_type"] = specimen_type
                                if test_detail.get("value"):
                                    test_response["value"] = test_detail.get("value")
                                if core_test.get("unit"):
                                    test_response["unit"] = core_test.get("unit")
                                if core_test.get("reference_range"):
                                    test_response["reference_range"] = core_test.get("reference_range")
                                if test_detail.get("sub_title"):
                                    test_response["sub_title"] = test_detail.get("sub_title")
                            else:
                                # Fallback to stored data
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
                                if test_detail.get("sub_title"):
                                    test_response["sub_title"] = test_detail.get("sub_title")
                        
                        patient_details["testdetails"].append(test_response)
                        
                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"Error processing test: {str(e)}")
                    continue
        
        # Fetch signature data for all approvers
        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)
        
        # Close MongoDB connection
        client.close()
        
        # Prepare final response with signatures
        response_data = {
            "patient_data": patient_details,
            "signatures": signatures_data
        }
        
        return JsonResponse(response_data, safe=False)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@api_view(['GET'])
# @permission_classes([HasRoleAndDataPermission])
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

# Add these new endpoints to your Django views.py

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_investigation_status(request):
    """
    Get investigation and ophthalmology status for a patient
    """
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)
    
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        
        franchise_investigation_collection = db.core_investigation
        franchise_ophthalmology_collection = db.core_ophthalmology
        franchise_sample_collection = db.core_sample

        investigation = franchise_investigation_collection.find_one({"barcode": barcode})
        ophthalmology = franchise_ophthalmology_collection.find_one({"barcode": barcode})

        investigation_status = {}
        if investigation:
            investigation_status = {
                "xray_report": "approved" if investigation.get("xray_report") else "pending",
                "xrayfilm_file": "approved" if investigation.get("xrayfilm_file") else "pending",
                "ecg_file": "approved" if investigation.get("ecg_file") else "pending",
                "pft_file": "approved" if investigation.get("pft_file") else "pending",
                "audiometric_file": "approved" if investigation.get("audiometric_file") else "pending",
                "ecg_notes": investigation.get("ecg_notes", ""),
                "pft_notes": investigation.get("pft_notes", ""),
                "audiometry_notes": investigation.get("audiometry_notes", ""),
                "xray_notes": investigation.get("xray_notes", "")
            }
        
        ophthalmology_status = None
        if ophthalmology:
            ophthalmology_status = ophthalmology.get("status", "pending")

        total_sample_tests = 0
        try:
            franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})
            sample_tests = []
            if franchise_sample and franchise_sample.get('testdetails'):
                raw = franchise_sample.get('testdetails')
                sample_tests = json.loads(raw) if isinstance(raw, str) else (raw or [])
            total_sample_tests = len(sample_tests)
        except Exception:
            total_sample_tests = 0

        approved_tests_count = 0
        try:
            # Fetching testdetails from TestValue model
            test_value_records = TestValue.objects.filter(barcode=barcode)
            for record in test_value_records:
                td = record.testdetails
                tests_list = json.loads(td) if isinstance(td, str) else (td or [])
                for t in tests_list:
                    if isinstance(t, dict) and t.get("approve"):
                        approved_tests_count += 1
        except Exception as e:
            print(f"Error calculating approved_tests_count: {e}")
            approved_tests_count = 0

        lab_approval = "approved" if (total_sample_tests > 0 and approved_tests_count >= total_sample_tests) else "pending"

        client.close()
        
        return JsonResponse({
            'success': True,
            'investigation': investigation_status,
            'ophthalmology': ophthalmology_status,
            'lab_approval': lab_approval,
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@api_view(['POST'])
@csrf_exempt 
@permission_classes([HasRoleAndDataPermission])
def get_batch_investigation_status(request):
    """
    Get investigation and ophthalmology status for multiple patients in one call
    """
    barcodes = request.data.get('barcodes', [])
    
    if not barcodes or not isinstance(barcodes, list):
        return JsonResponse({'error': 'Barcodes array is required'}, status=400)
    
    # Normalize barcodes to strings to avoid type mismatches
    barcodes = [str(bc) for bc in barcodes]
    
    print(f"Processing {len(barcodes)} barcodes: {barcodes[:5]}...")  # Debug
    
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        
        franchise_investigation_collection = db.core_investigation
        franchise_ophthalmology_collection = db.core_ophthalmology
        franchise_sample_collection = db.core_sample

        # Fetch all investigations at once
        investigations = list(franchise_investigation_collection.find(
            {"barcode": {"$in": barcodes}}
        ))
        print(f"Found {len(investigations)} investigations")
        
        # Fetch all ophthalmology records at once
        ophthalmologies = list(franchise_ophthalmology_collection.find(
            {"barcode": {"$in": barcodes}}
        ))
        print(f"Found {len(ophthalmologies)} ophthalmology records")
        
        # Fetch all sample records at once
        samples = list(franchise_sample_collection.find(
            {"barcode": {"$in": barcodes}}
        ))
        print(f"Found {len(samples)} sample records")
        
        # Fetch all test values at once
        test_value_records = TestValue.objects.filter(barcode__in=barcodes)
        print(f"Found {len(test_value_records)} test value records")
        
        # Create maps for quick lookup - normalize barcode keys to strings
        investigation_map = {str(inv['barcode']): inv for inv in investigations}
        ophthalmology_map = {str(oph['barcode']): oph for oph in ophthalmologies}
        sample_map = {str(samp['barcode']): samp for samp in samples}
        
        # Process test values
        approved_tests_map = {}
        for record in test_value_records:
            barcode = str(record.barcode)
            td = record.testdetails
            tests_list = json.loads(td) if isinstance(td, str) else (td or [])
            approved_count = sum(1 for t in tests_list if isinstance(t, dict) and t.get("approve"))
            approved_tests_map[barcode] = approved_tests_map.get(barcode, 0) + approved_count
        
        # Build response for all barcodes
        results = {}
        for barcode in barcodes:
            barcode_str = str(barcode)
            investigation = investigation_map.get(barcode_str)
            ophthalmology = ophthalmology_map.get(barcode_str)
            sample = sample_map.get(barcode_str)
            
            # Investigation status - default to empty dict if no investigation found
            investigation_status = {}
            if investigation:
                investigation_status = {
                    "xray_report": "approved" if investigation.get("xray_report") else "pending",
                    "xrayfilm_file": "approved" if investigation.get("xrayfilm_file") else "pending",
                    "ecg_file": "approved" if investigation.get("ecg_file") else "pending",
                    "pft_file": "approved" if investigation.get("pft_file") else "pending",
                    "audiometric_file": "approved" if investigation.get("audiometric_file") else "pending",
                }
            else:
                # Default all to pending if no investigation record
                investigation_status = {
                    "xray_report": "pending",
                    "xrayfilm_file": "pending",
                    "ecg_file": "pending",
                    "pft_file": "pending",
                    "audiometric_file": "pending",
                }
            
            # Ophthalmology status
            ophthalmology_status = "pending"
            if ophthalmology:
                ophthalmology_status = ophthalmology.get("status", "pending")
            
            # Lab approval calculation
            total_sample_tests = 0
            if sample and sample.get('testdetails'):
                raw = sample.get('testdetails')
                sample_tests = json.loads(raw) if isinstance(raw, str) else (raw or [])
                total_sample_tests = len(sample_tests)
            
            approved_tests_count = approved_tests_map.get(barcode_str, 0)
            lab_approval = "approved" if (total_sample_tests > 0 and approved_tests_count >= total_sample_tests) else "pending"
            
            # Use barcode_str as key to ensure consistency
            results[barcode_str] = {
                'investigation': investigation_status,
                'ophthalmology': ophthalmology_status,
                'lab_approval': lab_approval,
            }
        
        client.close()
        
        print(f"Returning results for {len(results)} barcodes")
        
        return JsonResponse({
            'success': True,
            'results': results
        })
        
    except Exception as e:
        print(f"Error in batch status fetch: {e}")
        print(traceback.format_exc())
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def save_overall_approval(request):
    """
    Save overall approval with impression and remarks to overallApproval collection
    """
    try:
        data = request.data
        barcode = data.get('barcode')
        employee_id = data.get('employee_id')
        impression = data.get('impression')
        remarks = data.get('remarks')
        created_by = data.get('auth-user-id')
        
        if not barcode or not employee_id:
            return JsonResponse({'error': 'Barcode and Employee ID are required'}, status=400)
        
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        
        # Get or create overallApproval collection
        overall_approval_collection = db.overallApproval
        
        # Prepare document
        approval_document = {
            "created_by": created_by,
            "barcode": barcode,
            "employee_id": employee_id,
            "impression": impression,
            "remarks": remarks,
            "approved_date": datetime.now(),
            "status": "approved",
            "created_at": datetime.now()
        }
        
        # Update or insert
        result = overall_approval_collection.update_one(
            {"barcode": barcode},
            {"$set": approval_document},
            upsert=True
        )
        
        client.close()
        
        return JsonResponse({
            'success': True,
            'message': 'Approval saved successfully',
            'modified_count': result.modified_count,
            'upserted_id': str(result.upserted_id) if result.upserted_id else None
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def get_batch_corporate_health_reports(request):
    """
    Get multiple corporate health reports in one call for batch PDF generation
    """
    barcodes = request.data.get('barcodes', [])
    
    if not barcodes or not isinstance(barcodes, list):
        return JsonResponse({'error': 'Barcodes array is required'}, status=400)
    
    if len(barcodes) > 100:
        return JsonResponse({'error': 'Maximum 100 barcodes allowed per batch'}, status=400)
    
    # Normalize barcodes to strings
    barcodes = [str(bc) for bc in barcodes]
    
    print(f"Processing batch of {len(barcodes)} barcodes")
    
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        
        # Collections
        franchise_billing_collection = db.core_billing
        franchise_sample_collection = db.core_sample
        franchise_patient_collection = db.core_employeeregistration
        franchise_investigation_collection = db.core_investigation
        franchise_ophthalmology_collection = db.core_ophthalmology
        franchise_overall_approval_collection = db.overallApproval
        franchise_company_collection = db.core_company
        
        # Connect to Diagnostics database for core_testdetails
        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails
        
        # Connect to global database for employee profiles
        global_db = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs = gridfs.GridFS(global_db)
        
        # Helper function to get parameter details by test_code
        def get_parameter_from_core(core_test, device_id, test_code):
            """
            Get parameter details from core_testdetails based on test_code
            Supports both dict (device_id-keyed) and list formats
            """
            core_parameters = core_test.get("parameters", {})
            params_list = []
            
            # Case 1: parameters is a dictionary with device_id keys
            if isinstance(core_parameters, dict):
                # Try to get parameters for the specific device_id
                if device_id and device_id != "N/A" and device_id in core_parameters:
                    params_list = core_parameters[device_id]
                else:
                    # Use first available device's parameters
                    if len(core_parameters) > 0:
                        first_device = list(core_parameters.keys())[0]
                        params_list = core_parameters[first_device]
            
            # Case 2: parameters is a list (like PT test)
            elif isinstance(core_parameters, list):
                params_list = core_parameters
            
            # Ensure params_list is actually a list
            if not isinstance(params_list, list):
                return None
            
            # Find by test_code
            if test_code:
                matching_params = [p for p in params_list if isinstance(p, dict) and p.get("test_code") == test_code]
                if matching_params:
                    return matching_params[0]
            
            return None
        
        # Helper function to get employee signature data
        def get_employee_signature_data(employee_id):
            """
            Fetch employee profile and signature image from MongoDB
            Returns dict with employeeName, designation, and signature base64
            """
            if not employee_id:
                return None
            
            try:
                # Get employee profile
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None
                
                employee_name = profile.get("employeeName", "")
                designation = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                
                signature_base64 = None
                if signature_file_id:
                    try:
                        # Convert string ID to ObjectId if needed
                        from bson import ObjectId
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        
                        # Fetch signature image from GridFS
                        signature_file = fs.get(signature_file_id)
                        signature_bytes = signature_file.read()
                        
                        # Convert to base64
                        import base64
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {employee_id}: {str(e)}")
                
                return {
                    "employeeName": employee_name,
                    "designation": designation,
                    "signatureBase64": signature_base64
                }
            except Exception as e:
                print(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None
        
        results = {}
        
        for barcode in barcodes:
            try:
                # Get franchise billing data
                franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
                if not franchise_billing:
                    results[barcode] = {'error': 'Billing record not found'}
                    continue
                
                employee_id = franchise_billing.get('employee_id')
                if not employee_id:
                    results[barcode] = {'error': 'Employee ID not found'}
                    continue
                
                # Get patient data
                franchise_patient = franchise_patient_collection.find_one({"employee_id": employee_id})
                if not franchise_patient:
                    results[barcode] = {'error': 'Patient not found'}
                    continue
                
                # Get company data
                company_data = None
                company_id = franchise_patient.get("company_id")
                if company_id:
                    company_data = franchise_company_collection.find_one({"company_id": company_id})
                
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
                
                # Get overall approval data
                franchise_overall_approval = franchise_overall_approval_collection.find_one({
                    "barcode": barcode,
                    "status": "approved"
                })
                
                # Get test values from Django model
                test_values = TestValue.objects.filter(barcode=barcode)
                
                # Parse vitals from investigation
                vitals_data = {}
                if franchise_investigation:
                    try:
                        vitals_raw = json.loads(franchise_investigation.get('vitals', '{}'))
                        for key, value in vitals_raw.items():
                            if value and str(value).strip() and str(value).strip() != "0":
                                vitals_data[key] = value
                    except json.JSONDecodeError:
                        vitals_data = {}
                
                # Store investigation notes (WITHOUT file IDs for simple PDF)
                investigation_notes = {}
                
                if franchise_investigation:
                    # Notes only - no file IDs
                    for note_field, key in [
                        ("ecg_notes", "ecg_notes"),
                        ("pft_notes", "pft_notes"),
                        ("audiometry_notes", "audiometry_notes"),
                        ("xray_notes", "xray_notes"),
                        ("xray_report", "xray_report")
                    ]:
                        note_value = franchise_investigation.get(note_field)
                        if note_value and note_value.strip():
                            investigation_notes[key] = note_value
                
                # Parse medical history
                medical_history_data = {}
                if franchise_investigation:
                    patient_history = franchise_investigation.get("patient_history")
                    if patient_history and patient_history.strip():
                        if patient_history.lower() not in ["nil", "nil significant", "no previous history", "none"]:
                            medical_history_data["patient_history"] = patient_history
                
                # Parse clinical examination
                clinical_examination_data = {}
                if franchise_investigation:
                    for field in ["cardiovascular_system", "respiratory_system", "central_nervous_system", 
                                 "locomotor_system", "skin"]:
                        value = franchise_investigation.get(field)
                        if value and value.strip() and value.lower() not in ["normal", "nil", "nil significant"]:
                            clinical_examination_data[field] = value
                
                # Parse ophthalmology data (same as original)
                ophthalmology_data = None
                if franchise_ophthalmology:
                    try:
                        ophthalmology_data = {}
                        
                        visual_acuity_str = franchise_ophthalmology.get('visual_acuity', '')
                        
                        if visual_acuity_str:
                            try:
                                json_array_str = '[' + visual_acuity_str + ']'
                                parsed_array = json.loads(json_array_str)
                                
                                parsed_va = {}
                                for obj in parsed_array:
                                    parsed_va.update(obj)
                                
                                visual_acuity_data = {}
                                
                                if parsed_va.get("distance"):
                                    visual_acuity_data["distance"] = {
                                        "right": str(parsed_va.get("distance", {}).get("right", "6/6")),
                                        "left": str(parsed_va.get("distance", {}).get("left", "6/6"))
                                    }
                                
                                if parsed_va.get("nearVision"):
                                    visual_acuity_data["near_vision"] = {
                                        "right": str(parsed_va.get("nearVision", {}).get("right", "N-6")),
                                        "left": str(parsed_va.get("nearVision", {}).get("left", "N-6"))
                                    }
                                
                                if parsed_va.get("colourVision"):
                                    visual_acuity_data["color_vision"] = {
                                        "right": str(parsed_va.get("colourVision", {}).get("right", "Normal")),
                                        "left": str(parsed_va.get("colourVision", {}).get("left", "Normal"))
                                    }
                                
                                if parsed_va.get("ocularmovement"):
                                    visual_acuity_data["ocularmovement"] = {
                                        "right": str(parsed_va.get("ocularmovement", {}).get("right", "Normal")),
                                        "left": str(parsed_va.get("ocularmovement", {}).get("left", "Normal"))
                                    }
                                
                                if visual_acuity_data:
                                    ophthalmology_data["visual_acuity"] = visual_acuity_data
                                    
                            except (json.JSONDecodeError, KeyError, AttributeError, TypeError) as e:
                                print(f"Error parsing visual_acuity: {str(e)}")
                        
                        remarks = franchise_ophthalmology.get('remarks', '')
                        if remarks and remarks.strip():
                            ophthalmology_data["remarks"] = remarks
                        
                        patient_complaints = franchise_ophthalmology.get('patient_complaints', '')
                        if patient_complaints and patient_complaints.strip():
                            ophthalmology_data["patient_complaints"] = patient_complaints
                        
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
                        
                        color_vision = franchise_ophthalmology.get('color_vision', '')
                        if color_vision and color_vision.strip():
                            ophthalmology_data["color_vision_status"] = color_vision
                        
                        vision_status = franchise_ophthalmology.get('vision_status', '')
                        if vision_status and vision_status.strip():
                            ophthalmology_data["vision_status"] = vision_status
                        
                        optometrist_name = franchise_ophthalmology.get('optometrist_name', '')
                        if optometrist_name and optometrist_name.strip():
                            ophthalmology_data["optometrist_name"] = optometrist_name
                        
                        if not ophthalmology_data:
                            ophthalmology_data = None
                            
                    except Exception as e:
                        print(f"Error parsing ophthalmology data: {str(e)}")
                        ophthalmology_data = None
                
                # Parse sample testdetails
                sample_testdetails = []
                if franchise_sample:
                    try:
                        sample_testdetails = json.loads(franchise_sample.get('testdetails', '[]'))
                    except json.JSONDecodeError:
                        sample_testdetails = []
                
                # Build patient details response
                patient_details = {
                    "patient_id": employee_id,
                    "patientname": franchise_patient.get("employee_name"),
                    "age": franchise_patient.get("age"),
                    "gender": franchise_patient.get("gender"),
                    "date": franchise_billing.get("created_date"),
                    "barcode": barcode,
                    "testdetails": []
                }
                
                # Add company_name
                if company_data and company_data.get("company_name"):
                    patient_details["company_name"] = company_data.get("company_name")
                
                # Add department if exists
                if franchise_patient.get("department"):
                    patient_details["department"] = franchise_patient.get("department")
                
                # Add dob if exists
                if franchise_patient.get("dob"):
                    patient_details["dob"] = franchise_patient.get("dob")
                
                # Add vitals if data exists
                if vitals_data:
                    patient_details["vitals"] = {}
                    for key in ["height_cm", "weight_kg", "bmi", "blood_pressure", "pulse", "spo2"]:
                        if vitals_data.get(key):
                            display_key = key.replace("_cm", "").replace("_kg", "")
                            patient_details["vitals"][display_key] = vitals_data.get(key)
                
                # Add medical history if data exists
                if medical_history_data:
                    patient_details["medical_history"] = medical_history_data
                
                # Add clinical examination if data exists
                if clinical_examination_data:
                    patient_details["clinical_examination"] = clinical_examination_data
                
                # Add ophthalmology if data exists
                if ophthalmology_data:
                    patient_details["ophthalmology"] = ophthalmology_data
                
                # Add investigation notes (NO file IDs)
                if investigation_notes:
                    patient_details["investigation_notes"] = investigation_notes
                
                # Add final assessment
                if franchise_overall_approval:
                    final_assessment = {}
                    
                    impression = franchise_overall_approval.get("impression", "")
                    if impression and impression.strip():
                        final_assessment["impression"] = impression
                    
                    remarks = franchise_overall_approval.get("remarks", "")
                    if remarks and remarks.strip():
                        final_assessment["remarks"] = remarks
                    
                    if final_assessment:
                        patient_details["final_assessment"] = final_assessment
                
                # Collect all unique approvers across all test values
                all_approvers = set()
                
                # UPDATED: Process lab tests from TestValue model with parameter enrichment from core_testdetails
                if test_values.exists():
                    for test_value in test_values:
                        try:
                            testvalue_details = json.loads(test_value.testdetails) if isinstance(test_value.testdetails, str) else test_value.testdetails
                            if not isinstance(testvalue_details, list):
                                continue
                            
                            for test_detail in testvalue_details:
                                test_id = test_detail.get("test_id")
                                testname = test_detail.get("testname")
                                device_id = test_detail.get("device_id", "N/A")
                                
                                if not test_id:
                                    continue
                                
                                # UPDATED: Fetch test details from core_testdetails using test_id
                                core_test = core_testdetails_collection.find_one({"test_id": test_id})
                                if core_test:
                                    testname = core_test.get("test_name", testname)
                                    # Get specimen_type from core_test
                                    specimen_type = core_test.get("specimen_type", "N/A")
                                    # UPDATED: Get department from core_test
                                    department = core_test.get("department", test_detail.get("department", ""))
                                else:
                                    specimen_type = test_detail.get("specimen_type", "")
                                    department = test_detail.get("department", "")
                                
                                if not testname:
                                    continue
                                
                                # Collect approver ID
                                approve_by = test_detail.get("approve_by", "")
                                if approve_by:
                                    all_approvers.add(approve_by)
                                
                                # Find corresponding sample status
                                sample_status = None
                                for sample_test in sample_testdetails:
                                    if sample_test.get("test_id") == test_id or sample_test.get("testname") == testname:
                                        sample_status = sample_test
                                        break
                                
                                # Build test detail object
                                test_response = {"testname": testname}
                                
                                # UPDATED: Use department from core_testdetails (with fallback to stored data)
                                if department:
                                    test_response["department"] = department
                                
                                if test_detail.get("verified_by"):
                                    test_response["verified_by"] = test_detail.get("verified_by")
                                
                                if test_detail.get("approve_by"):
                                    test_response["approve_by"] = test_detail.get("approve_by")
                                
                                if test_detail.get("approve_time"):
                                    test_response["approve_time"] = test_detail.get("approve_time")
                                
                                # UPDATED: Add outsourced and comment from test_detail
                                outsourced = test_detail.get("outsourced", False)
                                if outsourced:
                                    test_response["outsourced"] = outsourced
                                
                                comment = test_detail.get("comment", "")
                                if comment:
                                    test_response["comment"] = comment
                                
                                if sample_status:
                                    if sample_status.get("samplecollected_time"):
                                        test_response["samplecollected_time"] = sample_status.get("samplecollected_time")
                                    if sample_status.get("received_time"):
                                        test_response["received_time"] = sample_status.get("received_time")
                                
                                # UPDATED: Check if test has parameters and enrich from core_testdetails
                                if test_detail.get("parameters"):
                                    processed_parameters = []
                                    for param in test_detail.get("parameters", []):
                                        test_code = param.get("test_code")
                                        value = param.get("value", "")
                                        param_comment = param.get("comment", "")
                                        
                                        # Get parameter definition from core_testdetails based on test_code
                                        if core_test and test_code:
                                            param_def = get_parameter_from_core(core_test, device_id, test_code)
                                        else:
                                            param_def = None
                                        
                                        processed_param = {}
                                        
                                        # Use core_testdetails data if available, otherwise fallback to stored data
                                        if param_def:
                                            processed_param["name"] = param_def.get("test_name", param.get("name", ""))
                                            processed_param["value"] = value
                                            processed_param["unit"] = param_def.get("unit", "")
                                            processed_param["specimen_type"] = specimen_type
                                            processed_param["reference_range"] = param_def.get("reference_range", "")
                                            processed_param["method"] = param_def.get("method", "")
                                            if param_def.get("sub_title"):
                                                processed_param["sub_title"] = param_def.get("sub_title")
                                            # UPDATED: Add parameter comment if available
                                            if param_comment:
                                                processed_param["comment"] = param_comment
                                        else:
                                            # Fallback to stored data
                                            if param.get("name"):
                                                processed_param["name"] = param.get("name")
                                            if value:
                                                processed_param["value"] = value
                                            if param.get("unit"):
                                                processed_param["unit"] = param.get("unit")
                                            if param.get("specimen_type"):
                                                processed_param["specimen_type"] = param.get("specimen_type")
                                            if param.get("reference_range"):
                                                processed_param["reference_range"] = param.get("reference_range")
                                            if param.get("method"):
                                                processed_param["method"] = param.get("method")
                                            if param.get("sub_title"):
                                                processed_param["sub_title"] = param.get("sub_title")
                                            # UPDATED: Add parameter comment if available
                                            if param_comment:
                                                processed_param["comment"] = param_comment
                                        
                                        if processed_param:
                                            processed_parameters.append(processed_param)
                                    
                                    if processed_parameters:
                                        test_response["parameters"] = processed_parameters
                                else:
                                    # Add individual test fields - prefer core_testdetails data
                                    if core_test:
                                        if core_test.get("method"):
                                            test_response["method"] = core_test.get("method")
                                        if specimen_type:
                                            test_response["specimen_type"] = specimen_type
                                        if test_detail.get("value"):
                                            test_response["value"] = test_detail.get("value")
                                        if core_test.get("unit"):
                                            test_response["unit"] = core_test.get("unit")
                                        if core_test.get("reference_range"):
                                            test_response["reference_range"] = core_test.get("reference_range")
                                        if test_detail.get("sub_title"):
                                            test_response["sub_title"] = test_detail.get("sub_title")
                                    else:
                                        # Fallback to stored data
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
                                        if test_detail.get("sub_title"):
                                            test_response["sub_title"] = test_detail.get("sub_title")
                                
                                patient_details["testdetails"].append(test_response)
                                
                        except (json.JSONDecodeError, AttributeError) as e:
                            print(f"Error processing test: {str(e)}")
                            continue
                
                # Fetch signature data for all approvers
                signatures_data = []
                for approver_id in all_approvers:
                    sig_data = get_employee_signature_data(approver_id)
                    if sig_data:
                        signatures_data.append(sig_data)
                
                # Store result with patient_data and signatures structure
                results[barcode] = {
                    "patient_data": patient_details,
                    "signatures": signatures_data
                }
                
            except Exception as e:
                print(f"Error processing barcode {barcode}: {str(e)}")
                import traceback
                print(traceback.format_exc())
                results[barcode] = {'error': str(e)}
        
        client.close()
        
        return JsonResponse({
            'success': True,
            'results': results,
            'total': len(barcodes),
            'processed': len(results)
        })
        
    except Exception as e:
        print(f"Batch processing error: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
