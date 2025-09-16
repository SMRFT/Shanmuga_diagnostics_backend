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
load_dotenv()
logger = logging.getLogger(__name__)

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_franchise_sample(request, batch_number):
    """
    Get all sample details for a specific batch number with patient information
    """
    if request.method == "GET":
        client = None
        try:
            # Connect to MongoDB
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.franchise
            
            # Collections
            samples_collection = db["franchise_sample"]
            patient_collection = db["franchise_patient"]
            billing_collection = db["franchise_billing"]
            
            # Connect to Diagnostics database for test details
            diagnostics_db = client["Diagnostics"]
            test_details_collection = diagnostics_db["core_testdetails"]
            
            # Find all samples for this batch number with samplestatus = "Transferred"
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
            patients = list(patient_collection.find())
            billing = list(billing_collection.find())
            test_details_data = list(test_details_collection.find())
            
            # Create lookup dictionaries
            billing_lookup = {bill.get('barcode'): bill.get('patient_id') for bill in billing if bill.get('barcode')}
            patient_lookup = {pat.get('patient_id'): pat for pat in patients if pat.get('patient_id')}
            test_lookup = {test.get('test_id'): test for test in test_details_data if test.get('test_id')}
            
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
                
                # Get patient information
                patient_id = billing_lookup.get(barcode)
                patient_data_obj = patient_lookup.get(patient_id, {}) if patient_id else {}
                
                # Prepare enhanced test details
                enhanced_test_details = []
                for detail in batch_tests:
                    test_id = detail.get("test_id")
                    test_data = test_lookup.get(test_id, {})
                    
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
                
                # Create patient sample record
                sample_record = {
                    "date": safe_get(sample, 'created_date'),
                    "patient_id": patient_id or "N/A",
                    "patientname": safe_get(patient_data_obj, 'patientname', "N/A"),
                    "barcode": barcode,
                    "age": str(safe_get(patient_data_obj, 'age', "")) if safe_get(patient_data_obj, 'age') else "N/A",
                    "locationId": safe_get(sample, 'franchise_id'),
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
def update_franchise_sample(request, barcode):
    """
    Update sample status for a specific barcode in a batch context
    """
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.franchise
    collection = db.franchise_sample
    
    if request.method == "PUT":
        try:
            # Handle different data formats
            if hasattr(request, 'data'):
                body = request.data
            else:
                body = json.loads(request.body)
            
            updates = body.get("updates", [])
            batch_number = body.get("batch_number")  # Optional batch context
            
            if not updates:
                return JsonResponse({"error": "Updates are required"}, status=400)
            
            # Find the patient sample record
            patient_sample = collection.find_one({"barcode": barcode})
            if not patient_sample:
                return JsonResponse({"error": "Sample not found"}, status=404)
            
            # Parse testdetails as a Python list
            testdetails = json.loads(patient_sample.get('testdetails', '[]'))
            
            # Configure IST timezone
            from django.utils import timezone
            import pytz
            ist_timezone = pytz.timezone('Asia/Kolkata')
            
            for update in updates:
                test_id = update.get("test_id")
                testname = update.get("testname")
                new_status = update.get("samplestatus")
                received_by = update.get("received_by")
                rejected_by = update.get("rejected_by")
                outsourced_by = update.get("outsourced_by")
                remarks = update.get("remarks")
                update_batch_number = update.get("batch_number", batch_number)
                
                if new_status is None:
                    return JsonResponse({"error": "samplestatus is required"}, status=400)
                
                if testname is None and test_id is None:
                    return JsonResponse({"error": "Either testname or test_id is required"}, status=400)
                
                # Find the specific test entry
                test_entry = None
                for entry in testdetails:
                    # Match by test criteria and optionally by batch number
                    test_match = (
                        (testname and entry.get("testname") == testname) or 
                        (test_id and entry.get("test_id") == test_id)
                    )
                    
                    batch_match = (
                        update_batch_number is None or 
                        entry.get("batch_number") == update_batch_number
                    )
                    
                    if test_match and batch_match:
                        test_entry = entry
                        break
                
                if test_entry is None:
                    error_msg = f"Test not found with testname: {testname} or test_id: {test_id}"
                    if update_batch_number:
                        error_msg += f" in batch: {update_batch_number}"
                    return JsonResponse({"error": error_msg}, status=404)
                
                # Update the sample status and associated fields
                test_entry['samplestatus'] = new_status
                
                # Get current time in IST timezone
                current_time = timezone.now().astimezone(ist_timezone)
                formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S')
                
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
            
            return JsonResponse({
                "status": "success",
                "message": "Sample status updated successfully"
            }, status=200)
            
        except Exception as e:
            logger.error(f"Error updating sample status for barcode {barcode}: {str(e)}")
            return JsonResponse({"error": str(e)}, status=500)
        
        finally:
            client.close()

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_batch_generation_data(request):
    """
    Get all batch generation data where received=false with optional date filtering
    """
    if request.method == "GET":
        client = None
        try:
            # Connect to MongoDB
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.franchise  # Database name
            collection = db.franchise_batch  # Collection name
            
            # Build query with date filtering
            query = {"received": False}
            
            # Get date parameters from request
            from_date = request.GET.get('from_date')
            to_date = request.GET.get('to_date')
            
            # Add date filtering if provided
            if from_date or to_date:
                date_query = {}
                
                if from_date:
                    # Parse from_date and set time to start of day (00:00:00)
                    from_datetime = datetime.strptime(from_date, '%Y-%m-%d')
                    date_query['$gte'] = from_datetime
                
                if to_date:
                    # Parse to_date and set time to end of day (23:59:59)
                    to_datetime = datetime.strptime(to_date, '%Y-%m-%d')
                    to_datetime = to_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
                    date_query['$lte'] = to_datetime
                
                # Add date filter to query - FIXED: Use 'created_date' instead of 'createdDate'
                if date_query:
                    query['created_date'] = date_query
            
            # Log the query for debugging
            logger.info(f"MongoDB query: {query}")
            
            # Fetch documents with the built query
            batches = list(collection.find(query))
            
            # Process the data
            processed_data = []
            for batch in batches:
                # Convert ObjectId to string for JSON serialization
                batch['_id'] = str(batch['_id'])
                
                # Parse JSON strings if they exist - FIXED: Use correct field names
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
                
                # Convert datetime objects to ISO format strings - FIXED: Use correct field names
                if 'created_date' in batch:
                    batch['created_date'] = batch['created_date'].isoformat() if batch['created_date'] else None
                if 'lastmodified_date' in batch:
                    batch['lastmodified_date'] = batch['lastmodified_date'].isoformat() if batch['lastmodified_date'] else None
                
                processed_data.append(batch)
            
            return JsonResponse({
                "status": "success",
                "data": processed_data,
                "count": len(processed_data),
                "filters": {
                    "from_date": from_date,
                    "to_date": to_date
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
def update_batch_received_status(request, batch_no):
    """
    Update the received status and optionally remarks for a specific batch using batch_no
    """
    # MongoDB connection details
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.franchise
    collection = db.franchise_batch
    
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
            

@api_view(['GET','PATCH'])
@csrf_exempt
@permission_classes([ HasRoleAndDataPermission])
def franchise_overall_report(request):
    try:
        # MongoDB setup
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.franchise  # Database name
        patients_collection = db.franchise_billing  # Changed to franchise_billing
        sample_status_colletion = db.franchise_sample # Collection name for sample 
        franchise_patient_collection = db.franchise_patient  # Collection for patient details

        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        patient_id = request.GET.get("patient_id")
        
        try:
            if from_date:
                from_date = datetime.strptime(from_date, "%Y-%m-%d")
            if to_date:
                to_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        
        # Build MongoDB query - using created_date for consistency
        query = {}
        if patient_id:
            query["patient_id"] = patient_id
        if from_date and to_date:
            query["created_date"] = {"$gte": from_date, "$lt": to_date}
        elif from_date:
            query["created_date"] = {"$gte": from_date}
        elif to_date:
            query["created_date"] = {"$lt": to_date}
        
        patients = list(patients_collection.find(query))
        if not patients:
            return JsonResponse([], safe=False)
        
        patient_ids = [p.get("patient_id") for p in patients if p.get("patient_id")]
        barcodes = [p.get("barcode") for p in patients if p.get("barcode")]
        
        # Get patient details from franchise_patient collection
        patient_details_map = {}
        if patient_ids:
            patient_details = franchise_patient_collection.find({"patient_id": {"$in": patient_ids}})
            for patient_detail in patient_details:
                patient_details_map[patient_detail.get("patient_id")] = patient_detail
        
        # Status data: bulk fetch from MongoDB - use patient_id
        sample_status_records = sample_status_colletion.find({
            "patient_id": {"$in": patient_ids}
        })

        # Convert to list and extract only needed fields - ADD NULL CHECKS
        sample_status_records = [
            {"patient_id": record.get("patient_id"), "testdetails": record.get("testdetails")}
            for record in sample_status_records
            if record and isinstance(record, dict)  # Ensure record is a dict
        ]
        
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
        
        # Create a mapping from barcode to patient_id from billing records
        barcode_to_patient_map = {}
        for patient in patients:
            if patient.get("barcode") and patient.get("patient_id"):
                barcode_to_patient_map[patient.get("barcode")] = patient.get("patient_id")
        
        # Organize status data - ADD SAFETY CHECKS
        sample_status_map = {}
        for record in sample_status_records:
            if record and isinstance(record, dict) and record.get("testdetails"):
                patient_id_key = record.get("patient_id")
                if patient_id_key:
                    sample_status_map.setdefault(patient_id_key, []).extend(record["testdetails"])
        
        # Organize test value data using barcode mapping
        test_value_map = {}
        for record in test_value_records:
            if record and isinstance(record, dict):
                barcode = record.get("barcode")
                if barcode and barcode in barcode_to_patient_map:
                    patient_id = barcode_to_patient_map[barcode]
                    test_value_map.setdefault(patient_id, {"barcode": barcode, "testdetails": []})
                    if record.get("testdetails"):
                        test_value_map[patient_id]["testdetails"].extend(record["testdetails"])
        
        # Final result
        formatted_data = []
        for patient in patients:
            # SAFETY CHECK: Ensure patient is a dict
            if not isinstance(patient, dict):
                print(f"Warning: Patient record is not a dict: {type(patient)}")
                continue
                
            pid = patient.get("patient_id", "N/A")
            
            # Get patient details from franchise_patient collection
            patient_detail = patient_details_map.get(pid, {})
            # SAFETY CHECK: Ensure patient_detail is a dict
            if not isinstance(patient_detail, dict):
                patient_detail = {}
            
            # Payment method parsing - UPDATED TO RETURN COMPLETE DETAILS
            payment_details = {}
            raw = patient.get("paymentMode", "")  # Changed from payment_method to paymentMode
            if raw:
                if isinstance(raw, dict):
                    payment_details = raw
                elif isinstance(raw, str):
                    try:
                        cleaned = raw.strip('"')
                        payment_data = json.loads(cleaned) if cleaned else {}
                        if isinstance(payment_data, dict):
                            payment_details = payment_data
                        else:
                            payment_details = {"paymentmethod": str(payment_data)}
                    except:
                        payment_details = {"paymentmethod": raw}
            else:
                payment_details = {"paymentmethod": "N/A"}
            
            # Partial payment handling - similar to first document
            if payment_details.get("paymentmethod") == "PartialPayment":
                partial_data = patient.get("PartialPayment", "")
                try:
                    if isinstance(partial_data, str):
                        partial_data = json.loads(partial_data.strip('"')) if partial_data.strip('"') else {}
                    if isinstance(partial_data, dict):
                        # Merge partial payment details with existing payment details
                        payment_details.update(partial_data)
                        payment_details["paymentmethod"] = "PartialPayment"
                except:
                    pass
            
            # Test list - ADD SAFETY CHECKS
            test_list = []
            test_field = patient.get("testdetails", [])
            if isinstance(test_field, str):
                try:
                    test_list = json.loads(test_field)
                    if not isinstance(test_list, list):
                        test_list = []
                except:
                    test_list = []
            elif isinstance(test_field, list):
                test_list = test_field
            
            # SAFETY CHECK: Ensure test_list items are dicts and handle different field names
            testnames = ", ".join([
                test.get("test_name", test.get("testname", "")) if isinstance(test, dict) else str(test)
                for test in test_list
            ])
            no_of_tests = len(test_list)
            
            # Age handling - similar to first document
            age_value = patient_detail.get("age", "N/A")
            age_type = patient_detail.get("age_type", "")
            age = f"{age_value} {age_type}" if age_type else str(age_value)
            
            # Handle discount from franchise_billing structure
            discount_percentage = patient.get('discountPercentage', '0')
            discount_amount = patient.get('discountAmount', '0')
            try:
                discount = int(float(discount_percentage or 0))
            except:
                discount = 0
            
            # Amounts - updated field names for franchise_billing
            try:
                total_amount = int(float(patient.get("netAmount", 0) or 0))
            except:
                total_amount = 0
            
            # Credit amount handling (may not exist in franchise_billing)
            try:
                credit_amount = int(float(patient.get("credit_amount", 0) or 0))
            except:
                credit_amount = 0
            
            credit_details = []
            credit_details_raw = patient.get("credit_details")
            if isinstance(credit_details_raw, str):
                try:
                    credit_details = json.loads(credit_details_raw)
                    if not isinstance(credit_details, list):
                        credit_details = []
                except:
                    credit_details = []
            elif isinstance(credit_details_raw, list):
                credit_details = credit_details_raw
            
            # Status determination
            barcode = patient.get("barcode")
            status = "Registered"
            sample_tests = sample_status_map.get(pid, [])
            test_values = test_value_map.get(pid, {}).get("testdetails", [])
            
            # Use barcode from test_value_map if available, similar to first document
            if not barcode and test_value_map.get(pid, {}).get("barcode"):
                barcode = test_value_map.get(pid, {}).get("barcode")
            
            # SAFETY CHECKS for sample_tests
            all_collected = all(
                t.get("samplestatus") == "Sample Collected" if isinstance(t, dict) else False
                for t in sample_tests
            ) if sample_tests else False
            
            partially_collected = any(
                t.get("samplestatus") == "Sample Collected" if isinstance(t, dict) else False
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
            
            if all_received:
                status = "Received"
            elif partially_received:
                status = "Partially Received"
            
            # SAFETY CHECKS for test_values
            if test_values:
                all_tested = all(
                    t.get("value") is not None if isinstance(t, dict) else False
                    for t in test_values
                )
                partially_tested = any(
                    t.get("value") is not None if isinstance(t, dict) else False
                    for t in test_values
                )
                approve_all = all(
                    t.get("approve") if isinstance(t, dict) else False
                    for t in test_values
                )
                approve_partial = any(
                    t.get("approve") if isinstance(t, dict) else False
                    for t in test_values
                )
                dispatch_all = all(
                    t.get("dispatch") if isinstance(t, dict) else False
                    for t in test_values
                )
                
                if all_received or partially_received:
                    if all_tested:
                        status = "Tested"
                    elif partially_tested:
                        status = "Partially Tested"
                
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
                "patient_name": patient_detail.get("patientname", "N/A"),  # From franchise_patient
                "gender": patient_detail.get("gender", "N/A"),  # From franchise_patient
                "refby": patient.get("referredDoctor", "N/A"),
                "age": age,
                "email": patient_detail.get("email", "N/A"),  # From franchise_patient             
                "branch": patient.get("franchise_id", "N/A"),  # Use franchise_id as branch               
                "total_amount": total_amount,
                "credit_amount": credit_amount,
                "credit_details": credit_details,
                "discount": discount,
                "payment_method": payment_details,
                "test_names": testnames,
                "no_of_tests": no_of_tests,
                "bill_no": patient.get("bill_no", "N/A"),  # May not exist in franchise_billing
                "registeredby": patient.get("registeredBy", "N/A"),
                "barcode": barcode,
                "status": status,
            })
        
        return JsonResponse(formatted_data, safe=False)
    
    except Exception as e:
        print("Critical Error:", str(e))
        print(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)
    


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def franchise_patient_test_details(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)
    
    try:
        # MongoDB connection
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.franchise
        franchise_billing_collection = db.franchise_billing
        franchise_sample_collection = db.franchise_sample
        franchise_patient_collection = db.franchise_patient
        
        # Get franchise billing data using barcode
        franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
        if not franchise_billing:
            return JsonResponse({'error': 'Franchise billing record not found for the given barcode'}, status=404)
        
        # Extract patient_id from franchise_billing
        patient_id = franchise_billing.get('patient_id')
        if not patient_id:
            return JsonResponse({'error': 'Patient ID not found in billing record'}, status=404)
        
        # Get franchise patient data using patient_id
        franchise_patient = franchise_patient_collection.find_one({"patient_id": patient_id})
        if not franchise_patient:
            return JsonResponse({'error': 'Franchise patient not found for the given patient ID'}, status=404)
        
        # Get franchise sample data using barcode
        franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})
        
        # Get barcodes information - collect all unique barcodes for this patient
        barcodes = []
        try:
            # Get all barcodes associated with this patient_id
            all_barcodes_for_patient = franchise_billing_collection.find(
                {"patient_id": patient_id}, 
                {"barcode": 1, "_id": 0}
            )
            barcodes = [bc.get("barcode") for bc in all_barcodes_for_patient if bc.get("barcode")]
            # Remove duplicates while preserving order
            barcodes = list(dict.fromkeys(barcodes))
        except Exception:
            barcodes = []
        
        # Get test values from Django model for additional details
        test_values = TestValue.objects.filter(barcode=barcode)
        
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
            "patient_id": patient_id,
            "patientname": franchise_patient.get("patientname", "N/A"),
            "age": franchise_patient.get("age", "N/A"),
            "gender": franchise_patient.get("gender", "N/A"),
            "date": franchise_billing.get("created_date"),
            "barcode": franchise_billing.get("barcode", "N/A"),
            "barcodes": barcodes,  # Added barcodes field
            "refby": franchise_billing.get("referredDoctor", "N/A"),
            "branch": franchise_billing.get("franchise_id", "N/A"),
            "testdetails": []
        }
        
        # Process test details
        for billing_test in billing_testdetails:
            # Use test_name from billing (matches your document structure)
            testname = billing_test.get("test_name")
            
            # Find corresponding sample status
            sample_status = None
            for sample_test in sample_testdetails:
                if sample_test.get("testname") == testname:
                    sample_status = sample_test
                    break
            
            # Find corresponding test value details
            test_value_details = None
            if test_values.exists():
                for test_value in test_values:
                    for test_detail in test_value.testdetails:
                        if test_detail.get("testname") == testname:
                            test_value_details = test_detail
                            break
                    if test_value_details:
                        break
            
            # Build test detail object
            test_detail = {
                "testname": testname,
                "test_id": billing_test.get("test_id", "N/A"),
                "MRP": billing_test.get("MRP", "N/A"),
                "department": sample_status.get("department", "N/A") if sample_status else "N/A",
                "samplestatus": sample_status.get("samplestatus", "N/A") if sample_status else "N/A",
                "samplecollected_time": sample_status.get("samplecollected_time") if sample_status else None,
                "collected_by": sample_status.get("collected_by", "N/A") if sample_status else "N/A",
                "sampletransferred_time": sample_status.get("sampletransferred_time") if sample_status else None,
                "transferred_by": sample_status.get("transferred_by", "N/A") if sample_status else "N/A",
                "received_time": sample_status.get("received_time") if sample_status else None,
                "received_by": sample_status.get("received_by", "N/A") if sample_status else "N/A",
                "batch_number": sample_status.get("batch_number", "N/A") if sample_status else "N/A",
                "remarks": sample_status.get("remarks") if sample_status else None
            }
            
            # Add test value details if available
            if test_value_details:
                test_detail.update({
                    "verified_by": test_value_details.get("verified_by", "N/A"),
                    "method": test_value_details.get("method", "N/A"),
                    "specimen_type": test_value_details.get("specimen_type", "N/A"),
                    "value": test_value_details.get("value", "N/A"),
                    "unit": test_value_details.get("unit", "N/A"),
                    "reference_range": test_value_details.get("reference_range", "N/A"),
                    "parameters": test_value_details.get("parameters", [])
                })
            
            patient_details["testdetails"].append(test_detail)
        
        # Close MongoDB connection
        client.close()
        
        return JsonResponse(patient_details, safe=False)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


from rest_framework.decorators import api_view, permission_classes
from django.http import JsonResponse
from bson import json_util
import json

@api_view(['GET'])
@permission_classes([ HasRoleAndDataPermission])
def get_test_value_for_franchise(request):
    date = request.GET.get('date')
    franchise_id = request.GET.get('franchise_id')

    # print(f"Received date: {date}, franchise_id: {franchise_id}")

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