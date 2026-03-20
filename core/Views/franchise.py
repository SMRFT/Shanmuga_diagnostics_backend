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
from ..models import  TestValue, MBTestValue
load_dotenv()
logger = logging.getLogger(__name__)
import gridfs

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
def update_franchise_sample(request,barcode):
    """
    Bulk update sample status for multiple samples/tests
    """
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.franchise
    collection = db.franchise_sample
    
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
                            test_entry.pop('outsource_lab', None)
                            
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
            
def get_department_status_franchise(test_list, barcode, sample_status_map, test_value_map, mb_test_value_map=None):
    """
    Determine status for each department based on test details.
    All maps are keyed by barcode — isolates data to the exact visit.
    Returns dict: {department_name: status}
    """
    department_status = {}

    tests_by_dept = {}
    for test in test_list:
        dept = test.get('department', 'N/A')
        if dept and dept != 'N/A':
            if dept not in tests_by_dept:
                tests_by_dept[dept] = []
            tests_by_dept[dept].append(test)

    if not tests_by_dept:
        return {}

    for dept, tests in tests_by_dept.items():
        dept_test_ids = {t.get('test_id') for t in tests if t.get('test_id')}

        all_test_values = test_value_map.get(barcode, {}).get('testdetails', []).copy()
        if mb_test_value_map:
            mb_test_values = mb_test_value_map.get(barcode, {}).get('testdetails', [])
            if mb_test_values:
                all_test_values.extend(mb_test_values)

        dept_test_values = [tv for tv in all_test_values
                            if tv.get('test_id') in dept_test_ids and not tv.get('rerun', False)]

        sample_tests = sample_status_map.get(barcode, [])
        dept_samples  = [st for st in sample_tests if st.get('test_id') in dept_test_ids]

        if dept_test_values:
            def has_test_values(test):
                parameters = test.get("parameters", [])
                if not parameters:
                    return bool(test.get("value"))
                return any(
                    param.get("value") is not None and str(param.get("value")).strip() != ""
                    for param in parameters
                )

            all_tested     = all(has_test_values(tv) for tv in dept_test_values)
            approved_test_ids = {tv.get('test_id') for tv in dept_test_values if tv.get('approve', False)}
            all_approved   = dept_test_ids.issubset(approved_test_ids) and len(approved_test_ids) > 0
            approved_dept_tests = [tv for tv in dept_test_values if tv.get('approve', False)]
            all_dispatched = all(tv.get('dispatch', False) for tv in approved_dept_tests) if approved_dept_tests else False

            if all_dispatched and all_approved:
                department_status[dept] = 'Dispatched'
            elif all_approved:
                department_status[dept] = 'Approved'
            elif all_tested:
                department_status[dept] = 'Tested'
            else:
                if dept_samples:
                    all_received = all(t.get('samplestatus') == 'Received' for t in dept_samples)
                    department_status[dept] = 'Received' if all_received else 'In Progress'
                else:
                    department_status[dept] = 'In Progress'

        elif dept_samples:
            all_collected  = all(t.get('samplestatus') == 'Sample Collected' for t in dept_samples)
            all_received   = all(t.get('samplestatus') == 'Received' for t in dept_samples)

            if all_received:
                department_status[dept] = 'Received'
            elif all_collected:
                department_status[dept] = 'Collected'
            else:
                department_status[dept] = 'Pending'
        else:
            department_status[dept] = 'Pending'

    return department_status


@api_view(['GET', 'PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def franchise_overall_report(request):
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.franchise
        patients_collection          = db.franchise_billing
        sample_status_collection     = db.franchise_sample       # keyed by barcode
        franchise_patient_collection = db.franchise_patient

        diagnostics_db          = client.Diagnostics
        test_details_collection = diagnostics_db.core_testdetails

        from_date      = request.GET.get("from_date")
        to_date        = request.GET.get("to_date")
        selected_date  = request.GET.get("selected_date")
        patient_id_req = request.GET.get("patient_id")

        print("Received query parameters:", request.GET)

        try:
            if selected_date:
                selected_date_parsed = datetime.strptime(selected_date, "%Y-%m-%d")
                from_date = selected_date_parsed
                to_date   = selected_date_parsed + timedelta(days=1)
            elif from_date and to_date:
                from_date = datetime.strptime(from_date, "%Y-%m-%d")
                to_date   = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
            else:
                return JsonResponse({"error": "Either 'selected_date' or both 'from_date' and 'to_date' are required"}, status=400)
        except ValueError:
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        query = {}
        if patient_id_req:
            query["patient_id"] = patient_id_req
        query["created_date"] = {"$gte": from_date, "$lt": to_date}

        patients = list(patients_collection.find(query))
        print(f"Found {len(patients)} franchise billing records")

        if not patients:
            return JsonResponse([], safe=False)

        patient_ids = [p.get("patient_id") for p in patients if p.get("patient_id")]
        barcodes    = [p.get("barcode")    for p in patients if p.get("barcode")]

        # ── Patient details ───────────────────────────────────────────────────
        patient_details_map = {}
        if patient_ids:
            for pd in franchise_patient_collection.find({"patient_id": {"$in": patient_ids}}):
                patient_details_map[pd.get("patient_id")] = pd

        # ── barcode → patient_id mapping ──────────────────────────────────────
        barcode_to_patient_map = {}
        for patient in patients:
            if patient.get("barcode") and patient.get("patient_id"):
                barcode_to_patient_map[patient["barcode"]] = patient["patient_id"]

        # ── Sample status — fetch by BARCODE (franchise_sample is barcode-keyed) ──
        # BUG FIX: The original code queried franchise_sample by patient_id, but
        # franchise_sample stores records keyed by barcode — same as core_sample
        # in the corporate path.  Querying by patient_id always returned nothing,
        # leaving sample_status_map empty and freezing status at "Registered".
        sample_status_records = list(sample_status_collection.find({"barcode": {"$in": barcodes}}))

        # Keyed by BARCODE — prevents cross-barcode contamination for patients
        # who have multiple barcodes (each visit is its own barcode).
        sample_status_map = {}
        for record in sample_status_records:
            if not isinstance(record, dict):
                continue
            rec_barcode = record.get("barcode")
            testdetails = record.get("testdetails")
            if not rec_barcode or not testdetails:
                continue
            parsed = []
            if isinstance(testdetails, str):
                try:
                    parsed = json.loads(testdetails)
                except json.JSONDecodeError:
                    parsed = []
            elif isinstance(testdetails, list):
                parsed = testdetails
            if parsed:
                sample_status_map.setdefault(rec_barcode, []).extend(parsed)

        print(f"Fetched {len(sample_status_records)} franchise sample status records")

        # ── TestValue records ─────────────────────────────────────────────────
        test_value_records = TestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(from_date.date(), to_date.date())
        ).values("barcode", "testdetails", "created_date")

        # Keyed by BARCODE — one entry per barcode, never merges across visits.
        test_value_map = {}
        for record in test_value_records:
            if not isinstance(record, dict):
                continue
            rec_barcode  = record.get("barcode")
            created_date = record.get("created_date")
            testdetails  = record.get("testdetails")
            if not rec_barcode:
                continue
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails.strip('"'))
                except json.JSONDecodeError:
                    testdetails = []
            if rec_barcode not in test_value_map:
                test_value_map[rec_barcode] = {"testdetails": [], "created_date": created_date}
            if isinstance(testdetails, list):
                test_value_map[rec_barcode]["testdetails"].extend(testdetails)
            if created_date:
                existing = test_value_map[rec_barcode]["created_date"]
                if not existing or created_date > existing:
                    test_value_map[rec_barcode]["created_date"] = created_date

        print(f"Processed test value map with {len(test_value_map)} unique barcodes")

        # ── MBTestValue records ───────────────────────────────────────────────
        mb_test_value_records = MBTestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(from_date.date(), to_date.date())
        ).values("barcode", "testdetails", "created_date")

        # Keyed by BARCODE — isolates MB test values per visit.
        mb_test_value_map = {}
        for record in mb_test_value_records:
            if not isinstance(record, dict):
                continue
            rec_barcode  = record.get("barcode")
            created_date = record.get("created_date")
            testdetails  = record.get("testdetails")
            if not rec_barcode:
                continue
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails.strip('"'))
                except json.JSONDecodeError:
                    testdetails = []
            if rec_barcode not in mb_test_value_map:
                mb_test_value_map[rec_barcode] = {"testdetails": [], "created_date": created_date}
            if isinstance(testdetails, list):
                mb_test_value_map[rec_barcode]["testdetails"].extend(testdetails)
            if created_date:
                existing = mb_test_value_map[rec_barcode]["created_date"]
                if not existing or created_date > existing:
                    mb_test_value_map[rec_barcode]["created_date"] = created_date

        print(f"Processed MB test value map with {len(mb_test_value_map)} unique barcodes")

        # ── Build formatted response ──────────────────────────────────────────
        formatted_data = []

        for patient in patients:
            if not isinstance(patient, dict):
                continue

            pid            = patient.get("patient_id", "N/A")
            patient_detail = patient_details_map.get(pid, {})
            if not isinstance(patient_detail, dict):
                patient_detail = {}

            # Payment method
            payment_details = {}
            raw = patient.get("paymentMode", "")
            if raw:
                if isinstance(raw, dict):
                    payment_details = raw
                elif isinstance(raw, str):
                    try:
                        cleaned = raw.strip('"')
                        payment_data = json.loads(cleaned) if cleaned else {}
                        payment_details = payment_data if isinstance(payment_data, dict) else {"paymentmethod": str(payment_data)}
                    except Exception:
                        payment_details = {"paymentmethod": raw}
            else:
                payment_details = {"paymentmethod": "N/A"}

            if payment_details.get("paymentmethod") == "PartialPayment":
                partial_data = patient.get("PartialPayment", "")
                try:
                    if isinstance(partial_data, str):
                        partial_data = json.loads(partial_data.strip('"')) if partial_data.strip('"') else {}
                    if isinstance(partial_data, dict):
                        payment_details.update(partial_data)
                        payment_details["paymentmethod"] = "PartialPayment"
                except Exception:
                    pass

            # Test list with department enrichment
            test_list      = []
            test_ids       = []
            departments_set = set()

            test_field = patient.get("testdetails", [])
            if isinstance(test_field, str):
                try:
                    test_field = json.loads(test_field)
                    if not isinstance(test_field, list):
                        test_field = []
                except Exception:
                    test_field = []
            elif not isinstance(test_field, list):
                test_field = []

            test_ids = [t.get("test_id") for t in test_field if isinstance(t, dict) and t.get("test_id")]

            if test_ids:
                for test_id in test_ids:
                    test_detail_doc = test_details_collection.find_one(
                        {"test_id": test_id},
                        {"_id": 0, "test_id": 1, "test_name": 1, "department": 1}
                    )
                    if test_detail_doc:
                        dept = test_detail_doc.get("department", "N/A")
                        test_list.append({
                            "test_id":   test_id,
                            "testname":  test_detail_doc.get("test_name", "N/A"),
                            "test_name": test_detail_doc.get("test_name", "N/A"),
                            "department": dept,
                        })
                        if dept and dept != "N/A":
                            departments_set.add(dept)

            if not test_list and test_field:
                test_list = test_field
                for t in test_list:
                    if isinstance(t, dict):
                        if not t.get('testname') and t.get('test_name'):
                            t['testname'] = t['test_name']
                        elif not t.get('test_name') and t.get('testname'):
                            t['test_name'] = t['testname']

            testnames    = ", ".join([t.get("test_name", t.get("testname", "")) if isinstance(t, dict) else str(t) for t in test_list])
            no_of_tests  = len(test_list)
            department   = ", ".join(sorted(departments_set)) if departments_set else "N/A"

            age_value = patient_detail.get("age", "N/A")
            age_type  = patient_detail.get("age_type", "")
            age       = f"{age_value} {age_type}" if age_type else str(age_value)

            try:
                discount_percentage = float(patient.get('discountPercentage', 0) or 0)
                discount = int(discount_percentage)
            except Exception:
                discount = 0
            try:
                total_amount = int(float(patient.get("netAmount", 0) or 0))
            except Exception:
                total_amount = 0
            try:
                credit_amount = int(float(patient.get("credit_amount", 0) or 0))
            except Exception:
                credit_amount = 0

            credit_details     = []
            credit_details_raw = patient.get("credit_details")
            if isinstance(credit_details_raw, str):
                try:
                    credit_details = json.loads(credit_details_raw)
                    if not isinstance(credit_details, list):
                        credit_details = []
                except Exception:
                    credit_details = []
            elif isinstance(credit_details_raw, list):
                credit_details = credit_details_raw

            barcode = patient.get("barcode")

            # Look up test values by BARCODE — avoids bleeding in values from
            # other barcodes that belong to the same patient_id.
            latest_test_data  = test_value_map.get(barcode, {})
            all_test_values   = latest_test_data.get("testdetails", []).copy()
            test_created_date = latest_test_data.get("created_date")

            mb_test_data    = mb_test_value_map.get(barcode, {})
            mb_test_values  = mb_test_data.get("testdetails", [])
            mb_created_date = mb_test_data.get("created_date")
            if mb_test_values:
                all_test_values.extend(mb_test_values)
                if mb_created_date and (not test_created_date or mb_created_date > test_created_date):
                    test_created_date = mb_created_date

            valid_test_values = []
            unapproved_tests  = []
            for test_record in all_test_values:
                if not test_record.get("rerun", False):
                    valid_test_values.append(test_record)
                    if not test_record.get("approve", False):
                        unapproved_tests.append(test_record)

            # Fetch sample tests for THIS barcode only
            sample_tests = sample_status_map.get(barcode, [])

            # Sample collection booleans
            all_collected       = bool(sample_tests) and all(t.get("samplestatus") == "Sample Collected" for t in sample_tests if isinstance(t, dict))
            partially_collected = any(t.get("samplestatus") == "Sample Collected" for t in sample_tests if isinstance(t, dict))
            all_received        = bool(sample_tests) and all(t.get("samplestatus") == "Received" for t in sample_tests if isinstance(t, dict))
            partially_received  = any(t.get("samplestatus") == "Received" for t in sample_tests if isinstance(t, dict))

            # Collection timestamps
            collection_time_val = "N/A"
            collected_date_val  = "N/A"
            for t in sample_tests:
                st = t.get("samplecollected_time")
                if not st:
                    continue
                try:
                    if isinstance(st, str):
                        dt = datetime.fromisoformat(st) if 'T' in st else datetime.strptime(st, "%Y-%m-%d %H:%M:%S")
                        collection_time_val = dt.strftime("%I:%M %p")
                        collected_date_val  = dt.strftime("%d-%m-%Y")
                    elif isinstance(st, datetime):
                        collection_time_val = st.strftime("%I:%M %p")
                        collected_date_val  = st.strftime("%d-%m-%Y")
                    if collection_time_val != "N/A":
                        break
                except Exception:
                    collection_time_val = str(st)
                    break

            # Status determination
            status = "Registered"
            if all_collected:
                status = "Collected"
            elif partially_collected:
                status = "Partially Collected"
            if all_received:
                status = "Received"
            elif partially_received:
                status = "Partially Received"

            # Individual test statuses
            individual_test_statuses = []
            for test in test_list:
                test_id   = test.get('test_id')
                test_name = test.get('testname') or test.get('test_name', 'N/A')
                sample_info     = next((t for t in sample_tests     if t.get('test_id') == test_id), {})
                test_value_info = next((t for t in valid_test_values if t.get('test_id') == test_id), {})
                test_status = "Registered"
                if sample_info:
                    ss = sample_info.get('samplestatus', '')
                    if ss == 'Collected':    test_status = "Collected"
                    if ss == 'Transferred':  test_status = "Transferred"
                    if ss == 'Received':     test_status = "Received"
                    if ss == 'Rejected':     test_status = "Rejected"
                    if ss == 'Outsourced':   test_status = "Outsourced"
                if test_value_info:
                    parameters = test_value_info.get("parameters", [])
                    has_values = bool(test_value_info.get("value")) if not parameters else any(
                        p.get("value") is not None and str(p.get("value")).strip() != "" for p in parameters
                    )
                    if has_values:               test_status = "Tested"
                    if test_value_info.get('approve'):  test_status = "Approved"
                    if test_value_info.get('dispatch'): test_status = "Dispatched"
                individual_test_statuses.append({'test_id': test_id, 'test_name': test_name, 'status': test_status})

            # Overall approval / dispatch status
            if valid_test_values:
                def has_test_values(test):
                    parameters = test.get("parameters", [])
                    if not parameters:
                        return bool(test.get("value"))
                    return any(p.get("value") is not None and str(p.get("value")).strip() != "" for p in parameters)

                all_tested       = all(has_test_values(t) for t in valid_test_values)
                partially_tested = any(has_test_values(t) for t in valid_test_values)

                all_ordered_test_ids = {str(t.get("test_id", "")).strip() for t in test_list if isinstance(t, dict) and t.get("test_id")}
                approved_test_ids    = {str(t.get("test_id", "")).strip() for t in valid_test_values if t.get("approve", False) and t.get("test_id")}
                dispatch_test_ids    = {str(t.get("test_id", "")).strip() for t in valid_test_values if t.get("dispatch", False) and t.get("test_id")}

                all_approved = partially_approved = False
                if all_ordered_test_ids:
                    if all_ordered_test_ids.issubset(approved_test_ids) and len(approved_test_ids) == len(all_ordered_test_ids):
                        all_approved = True
                    elif approved_test_ids:
                        partially_approved = True
                    if not all_approved:
                        approved_count = sum(1 for t in valid_test_values if t.get("approve", False))
                        if approved_count == no_of_tests and approved_count > 0:
                            all_approved = True; partially_approved = False
                        elif approved_count > 0:
                            partially_approved = True

                all_dispatched = partially_dispatched = False
                if all_ordered_test_ids:
                    if all_ordered_test_ids.issubset(dispatch_test_ids) and len(dispatch_test_ids) == len(all_ordered_test_ids):
                        all_dispatched = True
                    elif dispatch_test_ids:
                        partially_dispatched = True
                    if not all_dispatched:
                        dispatch_count = sum(1 for t in valid_test_values if t.get("dispatch", False))
                        if dispatch_count == no_of_tests and dispatch_count > 0:
                            all_dispatched = True; partially_dispatched = False
                        elif dispatch_count > 0:
                            partially_dispatched = True

                if all_tested:        status = "Tested"
                elif partially_tested: status = "Partially Tested"
                if all_approved:      status = "Approved"
                elif partially_approved: status = "Partially Approved"
                if all_dispatched:    status = "Dispatched"
                elif partially_dispatched: status = "Partially Dispatched"

            print(f"Final status for Patient {pid}: {status}")

            # Department statuses
            department_statuses = {}
            if pid and test_list:
                # Pass barcode-scoped maps: helper receives all maps keyed by barcode,
                # but uses pid internally — adapt to pass barcode directly.
                department_statuses = get_department_status_franchise(
                    test_list, barcode, sample_status_map, test_value_map, mb_test_value_map
                )

            # Date formatting
            created_date    = patient.get("created_date")
            formatted_date  = "N/A"
            registration_date = "N/A"
            if created_date:
                if isinstance(created_date, datetime):
                    formatted_date    = created_date.strftime("%Y-%m-%d")
                    registration_date = created_date.isoformat()
                else:
                    try:
                        parsed_date       = datetime.strptime(str(created_date), "%Y-%m-%d")
                        formatted_date    = parsed_date.strftime("%Y-%m-%d")
                        registration_date = parsed_date.isoformat()
                    except Exception:
                        formatted_date = registration_date = str(created_date)

            test_created_date_formatted = None
            if test_created_date:
                test_created_date_formatted = (
                    test_created_date.isoformat()
                    if isinstance(test_created_date, datetime)
                    else str(test_created_date)
                )

            formatted_data.append({
                "date":              formatted_date,
                "registration_date": registration_date,
                "patient_id":        pid,
                "patient_name":      patient_detail.get("patientname", "N/A"),
                "gender":            patient_detail.get("gender", "N/A"),
                "refby":             patient.get("referredDoctor", "N/A"),
                "age":               age,
                "age_type":          age_type,
                "email":             patient_detail.get("email", "N/A"),
                "branch":            patient.get("franchise_id", "N/A"),
                "total_amount":      total_amount,
                "credit_amount":     credit_amount,
                "credit_details":    credit_details,
                "discount":          discount,
                "payment_method":    payment_details,
                "test_names":        testnames,
                "department":        department,
                "department_statuses": department_statuses,
                "test_statuses":     individual_test_statuses,
                "no_of_tests":       no_of_tests,
                "bill_no":           patient.get("bill_no", "N/A"),
                "registeredby":      patient.get("registeredBy", "N/A"),
                "barcode":           barcode,
                "status":            status,
                "test_created_date": test_created_date_formatted,
                "collection_time":   collection_time_val,
                "collected_date":    collected_date_val,
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
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.franchise
        franchise_billing_collection = db.franchise_billing
        franchise_sample_collection  = db.franchise_sample
        franchise_patient_collection = db.franchise_patient

        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

        franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
        if not franchise_billing:
            return JsonResponse({'error': 'Franchise billing record not found for the given barcode'}, status=404)

        patient_id = franchise_billing.get('patient_id')
        if not patient_id:
            return JsonResponse({'error': 'Patient ID not found in billing record'}, status=404)

        franchise_patient = franchise_patient_collection.find_one({"patient_id": patient_id})
        if not franchise_patient:
            return JsonResponse({'error': 'Franchise patient not found for the given patient ID'}, status=404)

        franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})

        barcodes = []
        try:
            all_barcodes_for_patient = franchise_billing_collection.find(
                {"patient_id": patient_id},
                {"barcode": 1, "_id": 0}
            )
            barcodes = [bc.get("barcode") for bc in all_barcodes_for_patient if bc.get("barcode")]
            barcodes = list(dict.fromkeys(barcodes))
        except Exception:
            barcodes = []

        test_values = TestValue.objects.filter(barcode=barcode)

        # ── Resolve patient gender for reference range selection ──────────────
        # franchise_patient has a 'gender' field
        patient_gender = (franchise_patient.get('gender') or '').strip()

        # ── Gender-aware reference_range resolver ─────────────────────────────
        def resolve_reference_range(meta, gender):
            """
            Pick reference_range from male/female fields based on patient gender.
            Falls back to generic reference_range when gender-specific value absent.
            """
            gender_key = (gender or '').strip().lower()   # 'male', 'female', or ''

            if gender_key == 'male' and meta.get('male'):
                return meta['male']
            if gender_key == 'female' and meta.get('female'):
                return meta['female']
            return meta.get('reference_range', '') or ''

        # ── Parameter helper ──────────────────────────────────────────────────
        def get_parameter_from_core(core_test, device_id, test_code=None, param_index=None):
            core_parameters = core_test.get("parameters", {})
            params_list = []

            if isinstance(core_parameters, dict):
                if device_id and device_id != "N/A" and device_id in core_parameters:
                    params_list = core_parameters[device_id]
                else:
                    if len(core_parameters) > 0:
                        first_device = list(core_parameters.keys())[0]
                        params_list = core_parameters[first_device]
            elif isinstance(core_parameters, list):
                params_list = core_parameters

            if not isinstance(params_list, list):
                return None

            if param_index is not None and 0 <= param_index < len(params_list):
                return params_list[param_index]

            if test_code:
                matching_params = [p for p in params_list if isinstance(p, dict) and p.get("test_code") == test_code]
                if matching_params:
                    return matching_params[0]

            return None

        # ── Signature helper ──────────────────────────────────────────────────
        def get_employee_signature_data(employee_id):
            if not employee_id:
                return None
            try:
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None

                employee_name     = profile.get("employeeName", "")
                designation       = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                signature_base64  = None

                if signature_file_id:
                    try:
                        from bson import ObjectId
                        import base64
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        signature_file   = fs.get(signature_file_id)
                        signature_bytes  = signature_file.read()
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {employee_id}: {str(e)}")

                return {
                    "employeeName":    employee_name,
                    "designation":     designation,
                    "signatureBase64": signature_base64,
                }
            except Exception as e:
                print(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None

        try:
            billing_testdetails = json.loads(franchise_billing.get('testdetails', '[]'))
        except json.JSONDecodeError:
            billing_testdetails = []

        sample_testdetails = []
        if franchise_sample:
            try:
                sample_testdetails = json.loads(franchise_sample.get('testdetails', '[]'))
            except json.JSONDecodeError:
                sample_testdetails = []

        patient_details = {
            "patient_id":  patient_id,
            "patientname": franchise_patient.get("patientname", "N/A"),
            "age":         franchise_patient.get("age", "N/A"),
            "age_type":    franchise_patient.get("age_type", "Years"),
            "gender":      franchise_patient.get("gender", "N/A"),
            "date":        franchise_billing.get("created_date"),
            "barcode":     franchise_billing.get("barcode", "N/A"),
            "bill_no":     franchise_billing.get("bill_no", "N/A"),
            "barcodes":    barcodes,
            "refby":       franchise_billing.get("referredDoctor", "N/A"),
            "branch":      franchise_billing.get("franchise_id", "N/A"),
            "testdetails": [],
        }

        all_approvers = set()

        for billing_test in billing_testdetails:
            test_id  = billing_test.get("test_id")
            testname = billing_test.get("test_name")

            core_test = core_testdetails_collection.find_one({"test_id": test_id})
            if not core_test:
                continue

            sample_status = None
            for sample_test in sample_testdetails:
                if sample_test.get("testname") == testname or sample_test.get("test_id") == test_id:
                    sample_status = sample_test
                    break

            test_value_details = None
            device_id          = "N/A"
            if test_values.exists():
                for test_value in test_values:
                    test_details_list = test_value.testdetails
                    if isinstance(test_details_list, str):
                        try:
                            test_details_list = json.loads(test_details_list)
                        except Exception:
                            test_details_list = []
                    if not isinstance(test_details_list, list):
                        test_details_list = []
                    for test_detail_item in test_details_list:
                        if test_detail_item.get("test_id") == test_id:
                            test_value_details = test_detail_item
                            device_id          = test_detail_item.get("device_id", "N/A")
                            break
                    if test_value_details:
                        break

            department    = core_test.get("department", "N/A")
            NABL          = core_test.get("NABL", False)
            specimen_type = core_test.get("specimen_type", "N/A")

            test_detail = {
                "test_id":               test_id,
                "testname":              core_test.get("test_name", testname),
                "department":            department,
                "NABL":                  NABL,
                "MRP":                   billing_test.get("MRP", "N/A"),
                "specimen_type":         specimen_type,
                "samplestatus":          sample_status.get("samplestatus", "N/A")          if sample_status else "N/A",
                "samplecollected_time":  sample_status.get("samplecollected_time")          if sample_status else None,
                "collected_by":          sample_status.get("collected_by", "N/A")           if sample_status else "N/A",
                "sampletransferred_time":sample_status.get("sampletransferred_time")         if sample_status else None,
                "transferred_by":        sample_status.get("transferred_by", "N/A")         if sample_status else "N/A",
                "received_time":         sample_status.get("received_time")                  if sample_status else None,
                "received_by":           sample_status.get("received_by", "N/A")            if sample_status else "N/A",
                "batch_number":          sample_status.get("batch_number", "N/A")           if sample_status else "N/A",
                "remarks":               sample_status.get("remarks")                        if sample_status else None,
            }

            if test_value_details:
                outsourced   = test_value_details.get("outsourced", False)
                comment      = test_value_details.get("comment", "")
                verified_by  = test_value_details.get("verified_by", "N/A")
                approve_by   = test_value_details.get("approve_by", "N/A")
                approve_time = test_value_details.get("approve_time", "N/A")

                if approve_by:
                    all_approvers.add(approve_by)

                test_detail.update({
                    "outsourced":   outsourced,
                    "comment":      comment,
                    "verified_by":  verified_by,
                    "approve_by":   approve_by,
                    "approve_time": approve_time,
                })

                parameters = test_value_details.get("parameters", [])

                if parameters and len(parameters) > 0:
                    # ── Parameterised test ────────────────────────────────────
                    enriched_parameters = []
                    for param_index, param_value in enumerate(parameters):
                        test_code     = param_value.get("test_code")
                        value         = param_value.get("value", "")
                        param_comment = param_value.get("comment", "")

                        param_def = get_parameter_from_core(
                            core_test, device_id,
                            test_code=test_code,
                            param_index=param_index,
                        )

                        if param_def:
                            # Gender-resolved reference_range
                            ref_range = resolve_reference_range(param_def, patient_gender)

                            enriched_param = {
                                "name":            param_def.get("test_name", ""),
                                "test_code":       test_code,
                                "value":           value,
                                "unit":            param_def.get("unit", ""),
                                "reference_range": ref_range,   # gender-resolved
                                "method":          param_def.get("method", ""),
                                "specimen_type":   specimen_type,
                                "sub_title":       param_def.get("sub_title", ""),
                                "value_option":    param_def.get("value_option", []),
                                "comment":         param_comment,
                            }
                        else:
                            # Fallback: no core definition found
                            enriched_param = {
                                "name":            "N/A",
                                "test_code":       test_code,
                                "value":           value,
                                "unit":            "N/A",
                                "reference_range": "N/A",
                                "method":          "N/A",
                                "specimen_type":   specimen_type,
                                "sub_title":       "",
                                "value_option":    [],
                                "comment":         param_comment,
                            }

                        enriched_parameters.append(enriched_param)
                    test_detail["parameters"] = enriched_parameters

                else:
                    # ── Single-value test (with test value details) ───────────
                    # Gender-resolved reference_range from core_test
                    ref_range = resolve_reference_range(core_test, patient_gender)

                    test_detail.update({
                        "method":          core_test.get("method", ""),
                        "value":           test_value_details.get("value", ""),
                        "unit":            core_test.get("unit", ""),
                        "reference_range": ref_range,   # gender-resolved
                        "sub_title":       test_value_details.get("sub_title", ""),
                    })

            else:
                # ── No test value details — use core test defaults ────────────
                # Still resolve gender-based reference_range for consistency
                ref_range = resolve_reference_range(core_test, patient_gender)

                test_detail.update({
                    "method":          core_test.get("method", "N/A"),
                    "unit":            core_test.get("unit", "N/A"),
                    "reference_range": ref_range,   # gender-resolved
                    "value":           "N/A",
                    "verified_by":     "N/A",
                    "approve_by":      "N/A",
                    "approve_time":    "N/A",
                    "outsourced":      False,
                    "comment":         "",
                })

            patient_details["testdetails"].append(test_detail)

        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)

        client.close()

        response_data = {
            "patient_data": patient_details,
            "signatures":   signatures_data,
        }
        return JsonResponse(response_data, safe=False)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
from rest_framework.decorators import api_view
from django.http import JsonResponse
from datetime import datetime
import json
from bson import json_util

@api_view(['GET'])
def get_test_value_for_franchise(request):

    franchise_id = request.GET.get('franchise_id')
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')

    if not franchise_id or not from_date or not to_date:
        return JsonResponse(
            {'error': 'franchise_id, from_date and to_date are required'},
            status=400
        )

    try:
        # Convert string to date
        from_date_obj = datetime.strptime(from_date, "%Y-%m-%d").date()
        to_date_obj = datetime.strptime(to_date, "%Y-%m-%d").date()

        # 🔥 Step 1: Filter only by locationId (Mongo works fine for equality)
        test_values = TestValue.objects.filter(
            locationId=franchise_id
        )

        result = []

        for test_value in test_values:

            # 🔥 Step 2: Manual date filtering (Mongo-safe)
            record_date = test_value.date

            if from_date_obj <= record_date <= to_date_obj:

                try:
                    testdetails = (
                        json.loads(test_value.testdetails)
                        if isinstance(test_value.testdetails, str)
                        else test_value.testdetails
                    )
                except Exception:
                    testdetails = test_value.testdetails

                result.append({
                    'franchise_id': test_value.locationId,
                    'barcode': test_value.barcode,
                    'date': str(test_value.date),
                    'testdetails': testdetails,
                })

        if not result:
            return JsonResponse(
                {'message': 'No test values found'},
                status=404
            )

        return JsonResponse(
            {'status': 'success', 'data': result},
            status=200,
            safe=False,
            json_dumps_params={'default': json_util.default}
        )

    except Exception as e:
        return JsonResponse(
            {'error': 'Internal server error', 'details': str(e)},
            status=500
        )
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
