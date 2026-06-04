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
from ..models import TestValue
from bson import json_util
import gridfs
import base64
from bson.objectid import ObjectId
import re
from datetime import timezone, timedelta


load_dotenv()
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# HELPER: Build CHC-test status map from core_investigation
#
# core_investigation.test_results is a list like:
#   [{"test_id": "CHCT006", "test_name": "PFT", "report": "...", "files": [...], "notes": "..."}, ...]
#
# Returns a dict keyed by barcode → {test_id → {report, files, notes, has_report, has_file}}
# ──────────────────────────────────────────────────────────────────────────────
def _build_chc_status_from_investigation(investigation_records):
    """
    Returns: { barcode_str: { test_id_str: { has_report, has_file, report, notes, files, inv_status } } }
    Now also carries the top-level investigation status per barcode.
    """
    result = {}
    for inv in investigation_records:
        bc = str(inv.get("barcode", ""))
        if not bc:
            continue

        inv_status = inv.get("status", "").strip().lower()  # ← capture status

        test_results = inv.get("test_results", [])
        if isinstance(test_results, str):
            try:
                test_results = json.loads(test_results)
            except Exception:
                test_results = []

        result.setdefault(bc, {})

        for tr in (test_results or []):
            tid = str(tr.get("test_id", ""))
            if not tid:
                continue
            report = tr.get("report", "")
            files = tr.get("files", [])
            notes = tr.get("notes", "")
            has_report = bool(report and report.strip())
            has_file = bool(files)

            result[bc][tid] = {
                "has_report": has_report,
                "has_file": has_file,
                "report": report,
                "notes": notes,
                "files": files,
                "inv_status": inv_status,  # ← store it per test entry
            }

    return result


# ──────────────────────────────────────────────────────────────────────────────
# HELPER: Determine overall CHC approval status for a barcode
#
# "All Approved" means every chctest has either a report OR a file.
# Returns (status_string, pending_list, approved_list)
# ──────────────────────────────────────────────────────────────────────────────
def _chc_approval_status(chc_tests, chc_status_map):
    """
    chc_tests: list of {"testname": ..., "test_id": ...} from billing.chctestdetails
    chc_status_map: {test_id: {has_report, has_file, ...}} for one barcode
    Returns: (overall_status, pending_names, approved_names)
    """
    if not chc_tests:
        return "No CHC Tests", [], []

    pending = []
    approved = []
    for t in chc_tests:
        tid = str(t.get("test_id", ""))
        tname = t.get("testname", tid)
        info = chc_status_map.get(tid, {})
        if info.get("has_report") or info.get("has_file"):
            approved.append(tname)
        else:
            pending.append(tname)

    if not pending:
        return "All Approved", pending, approved
    if approved:
        return "Partial", pending, approved
    return "Pending", pending, approved


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
            employee_collection = db["core_chcregistration"]
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
                    barcode_key = str(barcode).strip()
                    employee_id_value = str(employee_id).strip()
                    billing_lookup[barcode_key] = employee_id_value

            logger.info(f"Sample billing lookup entries: {dict(list(billing_lookup.items())[:5])}")

            # Step 2: employee_id -> employee_data from core_chcregistration
            employee_lookup = {}
            for emp in employees:
                employee_id = emp.get('employee_id')
                if employee_id:
                    emp_id_key = str(employee_id).strip()
                    employee_lookup[emp_id_key] = emp

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

                if isinstance(test_details, str):
                    try:
                        test_details = json.loads(test_details)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse testdetails for sample {sample.get('_id')}")
                        test_details = []

                batch_tests = []
                for detail in test_details:
                    if (detail.get("batch_number") == batch_number and
                            detail.get("samplestatus") == "Transferred"):
                        batch_tests.append(detail)

                if not batch_tests:
                    continue

                employee_id = None
                employee_data = {}

                if barcode:
                    barcode_str = str(barcode).strip()
                    logger.info(f"Processing barcode: '{barcode_str}'")
                    employee_id = billing_lookup.get(barcode_str)
                    logger.info(f"Found employee_id: '{employee_id}' for barcode: '{barcode_str}'")
                    if employee_id:
                        employee_data = employee_lookup.get(employee_id, {})
                        logger.info(f"Found employee data: {bool(employee_data)} for employee_id: '{employee_id}'")
                        if employee_data:
                            logger.info(f"Employee name: {employee_data.get('employee_name', 'N/A')}")
                    else:
                        logger.warning(f"Employee_id not found for barcode: '{barcode_str}'")
                        logger.info(f"Available barcodes (first 10): {list(billing_lookup.keys())[:10]}")
                        similar_barcodes = [k for k in billing_lookup.keys() if barcode_str in k or k in barcode_str]
                        if similar_barcodes:
                            logger.info(f"Similar barcodes found: {similar_barcodes}")
                else:
                    logger.warning("No barcode found in sample")

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

                created_date = sample.get('created_date')
                if created_date and hasattr(created_date, 'isoformat'):
                    created_date = created_date.isoformat()
                elif created_date:
                    created_date = str(created_date)

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
def update_corporate_sample(request, barcode):
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

                    patient_sample = collection.find_one({"barcode": barcode})
                    if not patient_sample:
                        error_count += 1
                        errors.append(f"Sample not found for barcode: {barcode}")
                        continue

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

                        test_entry['samplestatus'] = new_status

                        if new_status == "Received":
                            test_entry['received_time'] = formatted_time
                            test_entry['received_by'] = received_by
                            test_entry.pop('rejected_time', None)
                            test_entry.pop('rejected_by', None)
                            test_entry.pop('remarks', None)
                        elif new_status == "Rejected":
                            test_entry['rejected_time'] = formatted_time
                            test_entry['rejected_by'] = rejected_by
                            test_entry['remarks'] = remarks
                            test_entry.pop('received_time', None)
                            test_entry.pop('received_by', None)
                            test_entry.pop('outsourced_time', None)
                            test_entry.pop('outsourced_by', None)
                        elif new_status == "Outsource":
                            test_entry['outsourced_time'] = formatted_time
                            test_entry['outsourced_by'] = outsourced_by
                            test_entry['outsource_lab'] = outsource_lab
                            test_entry.pop('received_time', None)
                            test_entry.pop('received_by', None)
                            test_entry.pop('rejected_time', None)
                            test_entry.pop('rejected_by', None)
                            test_entry.pop('remarks', None)

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
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Corporatehealthcheckup
            collection = db.core_batch

            query = {"received": False}

            from_date = request.GET.get('from_date')
            to_date = request.GET.get('to_date')

            if from_date or to_date:
                date_query = {}
                if from_date:
                    from_datetime = datetime.strptime(from_date, '%Y-%m-%d')
                    from_datetime = from_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
                    date_query['$gte'] = from_datetime
                if to_date:
                    to_datetime = datetime.strptime(to_date, '%Y-%m-%d')
                    to_datetime = to_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
                    date_query['$lte'] = to_datetime
                if date_query:
                    query['created_date'] = date_query

            logger.info(f"MongoDB query: {query}")
            if 'created_date' in query:
                logger.info(f"Date range: {query['created_date']}")

            batches = list(collection.find(query))
            logger.info(f"Found {len(batches)} batches matching criteria")

            processed_data = []
            for batch in batches:
                batch['_id'] = str(batch['_id'])
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
                if 'created_date' in batch and batch['created_date']:
                    cd = batch['created_date']
                    batch['created_date'] = cd.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat() if hasattr(cd, 'isoformat') else str(cd)
                if 'lastmodified_date' in batch and batch['lastmodified_date']:
                    lmd = batch['lastmodified_date']
                    batch['lastmodified_date'] = lmd.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat() if hasattr(lmd, 'isoformat') else str(lmd)
                processed_data.append(batch)

            return JsonResponse({
                "status": "success",
                "data": processed_data,
                "count": len(processed_data),
                "filters": {"from_date": from_date, "to_date": to_date},
                "debug_info": {"query_used": str(query), "total_found": len(batches)}
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
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Corporatehealthcheckup
    collection = db.core_batch

    try:
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

        if received_status is False and (remarks is None or remarks.strip() == ""):
            return JsonResponse({
                "status": "error",
                "message": "Remarks are required when rejecting a batch"
            }, status=400)

        if employee_id is None:
            return JsonResponse({
                "status": "error",
                "message": "auth-user-id is required"
            }, status=400)

        lastmodified_by = employee_id

        update_data = {
            "$set": {
                "received": received_status,
                "lastmodified_date": datetime.now(),
                "lastmodified_by": lastmodified_by
            }
        }

        if remarks is not None:
            update_data["$set"]["remarks"] = remarks.strip()

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

        all_test_values = test_value_map.get(employee_id, {}).get('testdetails', [])
        dept_test_values = [tv for tv in all_test_values
                            if tv.get('test_id') in dept_test_ids and not tv.get('rerun', False)]

        sample_tests = sample_status_map.get(employee_id, [])
        dept_samples = [st for st in sample_tests if st.get('test_id') in dept_test_ids]

        if dept_test_values:
            def has_test_values(test):
                parameters = test.get("parameters", [])
                if not parameters:
                    return bool(test.get("value"))
                return any(
                    param.get("value") is not None and str(param.get("value")).strip() != ""
                    for param in parameters
                )

            all_tested = all(has_test_values(tv) for tv in dept_test_values)
            approved_test_ids = {tv.get('test_id') for tv in dept_test_values if tv.get('approve', False)}
            all_approved = dept_test_ids.issubset(approved_test_ids) and len(approved_test_ids) > 0
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
                    if all_received:
                        department_status[dept] = 'Received'
                    else:
                        department_status[dept] = 'In Progress'
                else:
                    department_status[dept] = 'In Progress'
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
            department_status[dept] = 'Pending'

    return department_status


@api_view(['GET', 'PATCH'])
@csrf_exempt
# @permission_classes([HasRoleAndDataPermission])
def corporate_overall_report(request):
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        patients_collection = db.core_billing
        sample_status_collection = db.core_sample
        franchise_patient_collection = db.core_chcregistration
        investigation_collection = db.core_investigation

        diagnostics_db = client.Diagnostics
        test_details_collection = diagnostics_db.core_testdetails

        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        selected_date = request.GET.get("selected_date")
        employee_id = request.GET.get("employee_id")

        print("Received query parameters:", request.GET)

        try:
            if selected_date:
                selected_date_parsed = datetime.strptime(selected_date, "%Y-%m-%d")
                from_date = selected_date_parsed
                to_date = selected_date_parsed + timedelta(days=1)
            elif from_date and to_date:
                from_date = datetime.strptime(from_date, "%Y-%m-%d")
                to_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
            else:
                return JsonResponse({"error": "Either 'selected_date' or both 'from_date' and 'to_date' are required"}, status=400)
        except ValueError:
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        # ── STEP 1: Query core_sample first by date ───────────────────────────
        sample_query = {
            "date": {"$gte": from_date, "$lt": to_date}
        }
        sample_records = list(sample_status_collection.find(sample_query))
        print(f"Found {len(sample_records)} core_sample records")

        if not sample_records:
            return JsonResponse([], safe=False)

        # ── STEP 2: Extract barcodes from sample records ───────────────────────
        barcodes = [r.get("barcode") for r in sample_records if r.get("barcode")]
        barcodes = list(set(barcodes))  # deduplicate
        print(f"Barcodes from core_sample: {barcodes}")

        # Build sample date map and sample status map by barcode
        sample_date_by_barcode = {}
        sample_records_by_barcode = {}
        for record in sample_records:
            bc = record.get("barcode")
            if not bc:
                continue
            # sample date
            date_val = record.get("date")
            if date_val:
                if isinstance(date_val, datetime):
                    sample_date_by_barcode[bc] = date_val.strftime("%Y-%m-%d")
                else:
                    try:
                        sample_date_by_barcode[bc] = datetime.strptime(
                            str(date_val), "%Y-%m-%d %H:%M:%S"
                        ).strftime("%Y-%m-%d")
                    except Exception:
                        sample_date_by_barcode[bc] = str(date_val)
            sample_records_by_barcode[bc] = record

        # Build sample status list (testdetails per barcode)
        sample_status_map_by_barcode = {}
        for record in sample_records:
            bc = record.get("barcode")
            testdetails = record.get("testdetails")
            if not bc or not testdetails:
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
                sample_status_map_by_barcode[bc] = parsed

        # ── STEP 3: Get billing records using barcodes ─────────────────────────
        billing_query = {"barcode": {"$in": barcodes}}
        if employee_id:
            billing_query["employee_id"] = employee_id

        billing_records = list(patients_collection.find(billing_query))
        print(f"Found {len(billing_records)} billing records")

        if not billing_records:
            return JsonResponse([], safe=False)

        # Build barcode → billing and barcode → employee_id maps
        barcode_to_billing = {}
        barcode_to_employee_id = {}
        employee_ids = []
        for bill in billing_records:
            bc = bill.get("barcode")
            eid = bill.get("employee_id")
            if bc:
                barcode_to_billing[bc] = bill
            if bc and eid:
                barcode_to_employee_id[bc] = eid
                employee_ids.append(eid)

        employee_ids = list(set(employee_ids))

        # ── STEP 4: Get patient details using employee_ids ─────────────────────
        patient_details_map = {}
        if employee_ids:
            patient_details = franchise_patient_collection.find(
                {"employee_id": {"$in": employee_ids}}
            )
            for pd in patient_details:
                patient_details_map[pd.get("employee_id")] = pd

        print(f"Fetched {len(patient_details_map)} patient detail records")

        # ── STEP 5: TestValue records by barcode ───────────────────────────────
        test_value_records = TestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(from_date.date(), to_date.date())
        ).values("barcode", "testdetails", "created_date")

        test_value_map = {}  # barcode → { testdetails, created_date }
        for record in test_value_records:
            bc = record.get("barcode")
            created_date = record.get("created_date")
            testdetails = record.get("testdetails")
            if not bc:
                continue
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails.strip('"'))
                except json.JSONDecodeError:
                    testdetails = []
            if bc not in test_value_map:
                test_value_map[bc] = {"testdetails": [], "created_date": created_date}
            if isinstance(testdetails, list):
                test_value_map[bc]["testdetails"].extend(testdetails)
            if created_date:
                existing = test_value_map[bc]["created_date"]
                if not existing or created_date > existing:
                    test_value_map[bc]["created_date"] = created_date

        # ── STEP 6: Investigation records ─────────────────────────────────────
        investigation_records = list(investigation_collection.find(
            {"barcode": {"$in": barcodes}}
        ))
        chc_status_by_barcode = _build_chc_status_from_investigation(investigation_records)

        # ── STEP 7: Build response — iterate over barcodes from core_sample ───
        formatted_data = []

        for bc in barcodes:
            billing = barcode_to_billing.get(bc)
            if not billing:
                continue  # no billing record for this barcode, skip

            eid = barcode_to_employee_id.get(bc, "N/A")
            patient_detail = patient_details_map.get(eid, {})
            if not isinstance(patient_detail, dict):
                patient_detail = {}

            sample_tests = sample_status_map_by_barcode.get(bc, [])
            sample_date = sample_date_by_barcode.get(bc, "N/A")

            # Build test list from sample testdetails
            test_list = []
            test_ids = []
            departments_set = set()

            if isinstance(sample_tests, list):
                test_ids = [
                    t.get("test_id") for t in sample_tests
                    if isinstance(t, dict) and t.get("test_id")
                ]

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
                        if dept and dept != "N/A":
                            departments_set.add(dept)

            if not test_list and sample_tests:
                test_list = sample_tests

            testnames = ", ".join([
                t.get("testname", t.get("test_name", "")) if isinstance(t, dict) else str(t)
                for t in test_list
            ])
            no_of_tests = len(test_list)
            department = ", ".join(sorted(departments_set)) if departments_set else "N/A"

            age_value = patient_detail.get("age", "N/A")
            age_type = patient_detail.get("age_type", "")
            age = f"{age_value} {age_type}" if age_type else str(age_value)

            # CHC tests from billing
            chc_raw = billing.get("chctestdetails", "[]")
            chc_tests = []
            if isinstance(chc_raw, str):
                try:
                    chc_tests = json.loads(chc_raw)
                except (json.JSONDecodeError, TypeError):
                    chc_tests = []
            elif isinstance(chc_raw, list):
                chc_tests = chc_raw

            chc_status_map_for_barcode = chc_status_by_barcode.get(str(bc), {})
            chc_tests_with_status = []
            for ct in chc_tests:
                tid = str(ct.get("test_id", ""))
                info = chc_status_map_for_barcode.get(tid, {})
                
                inv_status = info.get("inv_status", "")   # ← read investigation status
                has_content = info.get("has_report") or info.get("has_file")

                # If investigation is explicitly "pending", treat as Pending
                # regardless of whether files/reports exist
                if inv_status == "approved" and has_content:
                    chc_status = "Approved"
                else:
                    chc_status = "Pending"

                chc_tests_with_status.append({
                    "test_id": tid,
                    "testname": ct.get("testname", ""),
                    "has_report": info.get("has_report", False),
                    "has_file": info.get("has_file", False),
                    "report": info.get("report", ""),
                    "notes": info.get("notes", ""),
                    "files": info.get("files", []),
                    "status": chc_status,   # ← driven by inv_status first, then content
                })

            chc_overall, chc_pending, chc_approved = _chc_approval_status(
                chc_tests, chc_status_map_for_barcode
            )

            # Test values
            tv_data = test_value_map.get(bc, {})
            all_test_values = tv_data.get("testdetails", [])
            test_created_date = tv_data.get("created_date", None)

            valid_test_values = []
            for test_record in all_test_values:
                if not test_record.get("rerun", False):
                    valid_test_values.append(test_record)

            # Sample status flags
            all_collected = all(
                t.get("samplestatus") == "Collected" for t in sample_tests
            ) if sample_tests else False
            partially_collected = any(
                t.get("samplestatus") == "Collected" for t in sample_tests
            )
            all_transferred = all(
                t.get("samplestatus") == "Transferred" for t in sample_tests
            ) if sample_tests else False
            partially_transferred = any(
                t.get("samplestatus") == "Transferred" for t in sample_tests
            )
            all_received = all(
                t.get("samplestatus") == "Received" for t in sample_tests
            ) if sample_tests else False
            partially_received = any(
                t.get("samplestatus") == "Received" for t in sample_tests
            )

            collection_time_val = "N/A"
            collected_date_val = "N/A"
            for t in sample_tests:
                st = t.get("samplecollected_time")
                if st:
                    try:
                        if isinstance(st, str):
                            dt = datetime.fromisoformat(st) if 'T' in st else datetime.strptime(st, "%Y-%m-%d %H:%M:%S")
                        elif isinstance(st, datetime):
                            dt = st
                        else:
                            dt = None
                        if dt:
                            collection_time_val = dt.strftime("%I:%M %p")
                            collected_date_val = dt.strftime("%d-%m-%Y")
                            break
                    except Exception:
                        collection_time_val = str(st)
                        break

            status = "Registered"
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

            # Individual test statuses
            individual_test_statuses = []
            for test in test_list:
                test_id = test.get("test_id")
                test_name = test.get("testname") or test.get("test_name", "N/A")
                sample_info = next((t for t in sample_tests if t.get("test_id") == test_id), {})
                test_value_info = next((t for t in valid_test_values if t.get("test_id") == test_id), {})
                test_status = "Registered"
                if sample_info:
                    ss = sample_info.get("samplestatus", "")
                    if ss in ("Collected", "Transferred", "Received", "Rejected", "Outsourced"):
                        test_status = ss
                if test_value_info:
                    parameters = test_value_info.get("parameters", [])
                    has_values = (
                        any(p.get("value") is not None and str(p.get("value")).strip() != "" for p in parameters)
                        if parameters else bool(test_value_info.get("value"))
                    )
                    if has_values:
                        test_status = "Tested"
                    if test_value_info.get("approve"):
                        test_status = "Approved"
                    if test_value_info.get("dispatch"):
                        test_status = "Dispatched"
                individual_test_statuses.append({
                    "test_id": test_id,
                    "test_name": test_name,
                    "status": test_status,
                })

            # Approval / dispatch status
            if valid_test_values:
                def has_test_values(t):
                    params = t.get("parameters", [])
                    return (
                        any(p.get("value") is not None and str(p.get("value")).strip() != "" for p in params)
                        if params else bool(t.get("value"))
                    )

                all_tested = all(has_test_values(t) for t in valid_test_values)
                partially_tested = any(has_test_values(t) for t in valid_test_values)

                all_ordered_ids = {str(t.get("test_id", "")).strip() for t in test_list if t.get("test_id")}
                approved_ids = {str(t.get("test_id", "")).strip() for t in valid_test_values if t.get("approve")}
                dispatch_ids = {str(t.get("test_id", "")).strip() for t in valid_test_values if t.get("dispatch")}

                all_approved = all_ordered_ids.issubset(approved_ids) and len(approved_ids) == len(all_ordered_ids) if all_ordered_ids else False
                partially_approved = bool(approved_ids) and not all_approved

                all_dispatched = all_ordered_ids.issubset(dispatch_ids) and len(dispatch_ids) == len(all_ordered_ids) if all_ordered_ids else False
                partially_dispatched = bool(dispatch_ids) and not all_dispatched

                if all_tested:
                    status = "Tested"
                elif partially_tested:
                    status = "Partially Tested"
                if all_approved:
                    status = "Approved"
                elif partially_approved:
                    status = "Partially Approved"
                if all_dispatched:
                    status = "Dispatched"
                elif partially_dispatched:
                    status = "Partially Dispatched"

            department_statuses = get_department_status_corporate(
                test_list, eid, 
                {eid: sample_tests},   # wrap to match expected signature
                {eid: tv_data}
            ) if test_list else {}

            # Billing created_date (registration date)
            created_date = billing.get("created_date")
            formatted_date = "N/A"
            registration_date = "N/A"
            if created_date:
                if isinstance(created_date, datetime):
                    formatted_date = created_date.strftime("%Y-%m-%d")
                    registration_date = created_date.isoformat()
                else:
                    try:
                        parsed_date = datetime.strptime(str(created_date), "%Y-%m-%d")
                        formatted_date = parsed_date.strftime("%Y-%m-%d")
                        registration_date = parsed_date.isoformat()
                    except Exception:
                        formatted_date = str(created_date)
                        registration_date = str(created_date)

            test_created_date_formatted = None
            if test_created_date:
                test_created_date_formatted = (
                    test_created_date.isoformat()
                    if isinstance(test_created_date, datetime)
                    else str(test_created_date)
                )

            formatted_data.append({                  # ← from core_sample.date
                "date": sample_date,                        # ← from billing.created_date
                "registration_date": registration_date,
                "patient_id": eid,
                "patient_name": patient_detail.get("employee_name", "N/A"),
                "gender": patient_detail.get("gender", "N/A"),
                "age": age,
                "age_type": age_type,
                "email": patient_detail.get("email", "N/A"),
                "branch": patient_detail.get("company_id", "N/A"),
                "test_names": testnames,
                "department": department,
                "department_statuses": department_statuses,
                "test_statuses": individual_test_statuses,
                "no_of_tests": no_of_tests,
                "barcode": bc,
                "status": status,
                "test_created_date": test_created_date_formatted,
                "collection_time": collection_time_val,
                "collected_date": collected_date_val,
                "chc_tests": chc_tests_with_status,
                "chc_investigation_status": chc_overall,
                "chc_pending_tests": chc_pending,
                "chc_approved_tests": chc_approved,
            })

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
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        franchise_billing_collection = db.core_billing
        franchise_sample_collection  = db.core_sample
        franchise_patient_collection = db.core_chcregistration

        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

        franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
        if not franchise_billing:
            return JsonResponse({'error': 'Franchise billing record not found for the given barcode'}, status=404)

        employee_id = franchise_billing.get('employee_id')
        if not employee_id:
            return JsonResponse({'error': 'Patient ID not found in billing record'}, status=404)

        franchise_patient = franchise_patient_collection.find_one({"employee_id": employee_id})
        if not franchise_patient:
            return JsonResponse({'error': 'Franchise patient not found for the given patient ID'}, status=404)

        franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})
        if not franchise_sample:
            return JsonResponse({'error': 'No sample records found for the given barcode'}, status=404)

        test_values = TestValue.objects.filter(barcode=barcode)
        if not test_values.exists():
            return JsonResponse({'error': 'No test value records found for the given barcode'}, status=404)

        barcodes = []
        try:
            all_barcodes_for_patient = franchise_billing_collection.find(
                {"employee_id": employee_id},
                {"barcode": 1, "_id": 0}
            )
            barcodes = [bc.get("barcode") for bc in all_barcodes_for_patient if bc.get("barcode")]
            barcodes = list(dict.fromkeys(barcodes))
        except Exception:
            barcodes = []

        # ── Resolve patient gender for reference range selection ──────────────
        # core_chcregistration has a 'gender' field
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
        def get_employee_signature_data(emp_id):
            if not emp_id:
                return None
            try:
                profile = profile_collection.find_one({"employeeId": emp_id})
                if not profile:
                    return None
                employee_name     = profile.get("employeeName", "")
                designation       = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                signature_base64  = None
                if signature_file_id:
                    try:
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        signature_file   = fs.get(signature_file_id)
                        signature_bytes  = signature_file.read()
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {emp_id}: {str(e)}")
                return {
                    "employeeName":    employee_name,
                    "designation":     designation,
                    "signatureBase64": signature_base64,
                }
            except Exception as e:
                print(f"Error fetching employee data for {emp_id}: {str(e)}")
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
            "patient_id":  employee_id,
            "patientname": franchise_patient.get("employee_name", ""),
            "age":         franchise_patient.get("age", ""),
            "age_type":    franchise_patient.get("age_type", "Years"),
            "gender":      franchise_patient.get("gender", ""),
            "date":        franchise_billing.get("created_date"),
            "barcode":     franchise_billing.get("barcode", ""),
            "barcodes":    barcodes,
            "branch":      franchise_billing.get("company_id", ""),
            "refby":       "SELF",
            "testdetails": [],
        }

        all_approvers = set()

        for test_value in test_values:
            try:
                testvalue_details = (
                    json.loads(test_value.testdetails)
                    if isinstance(test_value.testdetails, str)
                    else test_value.testdetails
                )
                if not isinstance(testvalue_details, list):
                    continue

                for test_detail in testvalue_details:
                    if test_detail.get("approve") is not True:
                        continue

                    test_id   = test_detail.get("test_id")
                    testname  = test_detail.get("testname")
                    device_id = test_detail.get("device_id", "N/A")
                    if not test_id:
                        continue

                    core_test = core_testdetails_collection.find_one({"test_id": test_id})

                    sample_status = None
                    for sample_test in sample_testdetails:
                        if sample_test.get("test_id") == test_id or sample_test.get("testname") == testname:
                            sample_status = sample_test
                            break

                    billing_info = None
                    for billing_test in billing_testdetails:
                        if billing_test.get("test_id") == test_id:
                            billing_info = billing_test
                            break

                    if core_test:
                        department    = core_test.get("department", "N/A")
                        NABL          = core_test.get("NABL", False)
                        specimen_type = core_test.get("specimen_type", "N/A")
                        testname      = core_test.get("test_name", testname)
                    else:
                        department    = test_detail.get("department", sample_status.get("department", "") if sample_status else "")
                        NABL          = test_detail.get("NABL", "")
                        specimen_type = test_detail.get("specimen_type", "")

                    outsourced   = test_detail.get("outsourced", False)
                    comment      = test_detail.get("comment", "")
                    verified_by  = test_detail.get("verified_by", "N/A")
                    approve_by   = test_detail.get("approve_by", "N/A")
                    approve_time = test_detail.get("approve_time", "N/A")

                    if approve_by:
                        all_approvers.add(approve_by)

                    test_response = {
                        "test_id":              test_id,
                        "testname":             testname,
                        "department":           department,
                        "NABL":                 NABL,                       
                        "outsourced":           outsourced,
                        "comment":              comment,
                        "verified_by":          verified_by,
                        "approve_by":           approve_by,
                        "approve_time":         approve_time,
                        "samplecollected_time": sample_status.get("samplecollected_time") if sample_status else None,
                        "received_time":        sample_status.get("received_time")         if sample_status else None,
                    }

                    if billing_info:
                        test_response["MRP"] = billing_info.get("MRP", "N/A")

                    parameters = test_detail.get("parameters", [])

                    if parameters and len(parameters) > 0:
                        # ── Parameterised test ────────────────────────────────
                        enriched_parameters = []
                        for param_index, param_value in enumerate(parameters):
                            test_code     = param_value.get("test_code")
                            value         = param_value.get("value", "")
                            param_comment = param_value.get("comment", "")

                            param_def = get_parameter_from_core(
                                core_test, device_id,
                                test_code=test_code,
                                param_index=param_index,
                            ) if core_test else None

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
                                    "name":            param_value.get("name", "N/A"),
                                    "test_code":       test_code,
                                    "value":           value,
                                    "unit":            param_value.get("unit", "N/A"),
                                    "reference_range": param_value.get("reference_range", "N/A"),
                                    "method":          param_value.get("method", "N/A"),
                                    "specimen_type":   specimen_type,
                                    "sub_title":       param_value.get("sub_title", ""),
                                    "value_option":    [],
                                    "comment":         param_comment,
                                }

                            enriched_parameters.append(enriched_param)
                        test_response["parameters"] = enriched_parameters

                    else:
                        # ── Single-value test ─────────────────────────────────
                        if core_test:
                            # Gender-resolved reference_range
                            ref_range = resolve_reference_range(core_test, patient_gender)

                            test_response.update({
                                "method":          core_test.get("method", ""),
                                "value":           test_detail.get("value", ""),
                                "unit":            core_test.get("unit", ""),
                                "reference_range": ref_range,   # gender-resolved
                                "sub_title":       test_detail.get("sub_title", ""),
                                "specimen_type":        specimen_type,
                            })
                        else:
                            test_response.update({
                                "method":          test_detail.get("method", ""),
                                "value":           test_detail.get("value", ""),
                                "unit":            test_detail.get("unit", ""),
                                "reference_range": test_detail.get("reference_range", ""),
                                "sub_title":       test_detail.get("sub_title", ""),
                                "specimen_type":        specimen_type,
                            })

                    patient_details["testdetails"].append(test_response)

            except (json.JSONDecodeError, AttributeError):
                continue

        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)

        client.close()

        if not patient_details["testdetails"]:
            return JsonResponse({'error': 'No approved test records found'}, status=404)

        response_data = {"patient_data": patient_details, "signatures": signatures_data}
        return JsonResponse(response_data, safe=False)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)



# ── HELPER: Check if a notes string is a whitelisted normal statement ─────────
NORMAL_NOTES_WHITELIST = {
    "Normal Study.",
    "No significant finding in the lungs or mediastinum.",
    "No significant abnormality detected.",
    "Normal Study within normal limits.",
}

NORMAL_VITAL_STATUSES = {"BP_status", "bmi_status", "spo2_status"}


def _is_normal_notes(notes: str) -> bool:
    if not notes or not notes.strip():
        return False
    return notes.strip() in NORMAL_NOTES_WHITELIST  # no .lower()


def _is_vitals_normal(vitals: dict) -> bool:
    """
    Returns True only when all three vital status fields are present
    and each equals "Normal" (case-insensitive).
    Returns False if any field is missing, empty, or not "Normal".
    """
    if not vitals or not isinstance(vitals, dict):
        return False

    for key in NORMAL_VITAL_STATUSES:
        value = vitals.get(key, "")
        if not value or str(value).strip().lower() != "normal":
            return False

    return True


def _compute_corporate_approval_status(
    bc,
    approval_status_map,
    chc_tests_with_status,
    lab_test_values,
    no_of_tests,
    investigation_status,
    vitals=None,
):
    chc_all_normal = (
        investigation_status == "approved"
        and len(chc_tests_with_status) > 0
        and all(_is_normal_notes(ct.get("notes", "")) for ct in chc_tests_with_status)
    )
    valid_lab_tests = [
        t for t in lab_test_values
        if not t.get("rerun", False) and t.get("approve", False)
    ]
    lab_all_normal = (
        no_of_tests > 0
        and len(valid_lab_tests) >= no_of_tests
        and all(t.get("status", "").strip().lower() == "normal" for t in valid_lab_tests)
    )
    vitals_all_normal = _is_vitals_normal(vitals)

    if chc_all_normal and lab_all_normal and vitals_all_normal:
        return "Approved", "auto", None   # ← no approver name for auto

    if bc and bc in approval_status_map:
        approver_name = approval_status_map[bc].get("approved_by_name", "Unknown")
        return "Approved", "manual", approver_name

    return "Pending", "none", None


@api_view(['GET', 'PATCH'])
@csrf_exempt
# @permission_classes([HasRoleAndDataPermission])
def corporate_approval_report(request):
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        patients_collection = db.core_billing
        sample_status_collection = db.core_sample
        franchise_patient_collection = db.core_chcregistration
        overall_approval_collection = db.overallApproval
        investigation_collection = db.core_investigation
        company_collection = db.core_company

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

        # ── STEP 1: Query core_sample first by date ───────────────────────────
        sample_query = {}
        if from_date and to_date:
            sample_query["date"] = {"$gte": from_date, "$lt": to_date}
        elif from_date:
            sample_query["date"] = {"$gte": from_date}
        elif to_date:
            sample_query["date"] = {"$lt": to_date}

        sample_records = list(sample_status_collection.find(sample_query))
        print(f"Found {len(sample_records)} core_sample records")

        if not sample_records:
            return JsonResponse([], safe=False)

        # ── STEP 2: Extract barcodes from sample records ──────────────────────
        barcodes = list(set(r.get("barcode") for r in sample_records if r.get("barcode")))
        print(f"Barcodes from core_sample: {barcodes}")

        # Build sample date map, testdetails map, and valid test count map by barcode
        sample_date_by_barcode = {}
        sample_testdetails_by_barcode = {}
        valid_test_count_by_barcode = {}  # for lab_approval computation

        for record in sample_records:
            bc = record.get("barcode")
            if not bc:
                continue

            # sample date
            date_val = record.get("date")
            if date_val:
                if isinstance(date_val, datetime):
                    sample_date_by_barcode[bc] = date_val.strftime("%Y-%m-%d")
                else:
                    try:
                        sample_date_by_barcode[bc] = datetime.strptime(
                            str(date_val), "%Y-%m-%d %H:%M:%S"
                        ).strftime("%Y-%m-%d")
                    except Exception:
                        sample_date_by_barcode[bc] = str(date_val)

            # testdetails
            testdetails = record.get("testdetails")
            raw_tests = []
            if testdetails:
                if isinstance(testdetails, str):
                    try:
                        raw_tests = json.loads(testdetails)
                    except json.JSONDecodeError:
                        raw_tests = []
                elif isinstance(testdetails, list):
                    raw_tests = testdetails

                # Enrich with test names from core_testdetails
                enriched = []
                for test in raw_tests:
                    if isinstance(test, dict):
                        test_id = test.get("test_id")
                        if test_id:
                            core_test = core_testdetails_collection.find_one({"test_id": test_id})
                            if core_test:
                                test["testname"] = core_test.get("test_name", test.get("testname", ""))
                        enriched.append(test)

                if enriched:
                    sample_testdetails_by_barcode[bc] = enriched

            # Count valid (non-Rejected, non-Outsource) tests per barcode
            invalid_status = {"Rejected", "Outsource"}
            valid_tests = [
                t for t in (raw_tests or [])
                if isinstance(t, dict) and t.get("samplestatus", "").strip() not in invalid_status
            ]
            valid_test_count_by_barcode[bc] = len(valid_tests)

        # ── STEP 3: Get billing records using barcodes ────────────────────────
        billing_query = {"barcode": {"$in": barcodes}}
        if employee_id:
            billing_query["employee_id"] = employee_id

        billing_records = list(patients_collection.find(billing_query))
        print(f"Found {len(billing_records)} billing records")

        if not billing_records:
            return JsonResponse([], safe=False)

        # Build barcode → billing and barcode → employee_id maps
        barcode_to_billing = {}
        barcode_to_employee_id = {}
        employee_ids = []

        for bill in billing_records:
            bc = bill.get("barcode")
            eid = bill.get("employee_id")
            if bc:
                barcode_to_billing[bc] = bill
            if bc and eid:
                barcode_to_employee_id[bc] = eid
                employee_ids.append(eid)

        employee_ids = list(set(employee_ids))

        # ── STEP 4: Get patient details using employee_ids ────────────────────
        patient_details_map = {}
        if employee_ids:
            for pd in franchise_patient_collection.find({"employee_id": {"$in": employee_ids}}):
                patient_details_map[pd.get("employee_id")] = pd

        print(f"Fetched {len(patient_details_map)} patient detail records")

        # After building employee_ids (before step 4), collect all company_ids:
        company_ids = list(set(
            pd.get("company_id") for pd in franchise_patient_collection.find(
                {"employee_id": {"$in": employee_ids}},
                {"company_id": 1}
            ) if pd.get("company_id")
        ))

        # Build company_id → company_name map
        company_name_map = {}
        for company in company_collection.find({"company_id": {"$in": company_ids}}):
            cid = company.get("company_id")
            if cid:
                company_name_map[cid] = company.get("company_name", cid)

        # ── STEP 5: Overall approval map ──────────────────────────────────────────────
        approval_status_map = {}
        approval_records = list(overall_approval_collection.find({"barcode": {"$in": barcodes}}))

        # Collect created_by employee IDs
        approval_creator_ids = list(set(
            r.get("created_by") for r in approval_records if r.get("created_by")
        ))

        # Bulk-fetch names from backend_diagnostics_profile (Global DB)
        global_db = client.Global  # ← adjust if your DB name differs
        diagnostics_profile_collection = global_db.backend_diagnostics_profile

        creator_name_map = {}
        for profile in diagnostics_profile_collection.find(
            {"employeeId": {"$in": approval_creator_ids}},
            {"employeeId": 1, "employeeName": 1}
        ):
            creator_name_map[profile.get("employeeId")] = profile.get("employeeName", "Unknown")

        for approval in approval_records:
            bc = approval.get("barcode")
            if bc:
                creator_id = approval.get("created_by", "")
                approval_status_map[bc] = {
                    "status": approval.get("status", "approved"),
                    "approved_date": approval.get("approved_date"),
                    "impression": approval.get("impression"),
                    "remarks": approval.get("remarks"),
                    "created_by": creator_id,
                    "approved_by_name": creator_name_map.get(creator_id, creator_id or "Unknown"),
                }

        # ── STEP 6: Investigation records ─────────────────────────────────────
        investigation_records = list(investigation_collection.find({"barcode": {"$in": barcodes}}))
        chc_status_by_barcode = _build_chc_status_from_investigation(investigation_records)

        investigation_status_by_barcode = {}
        vitals_by_barcode = {}
        for inv in investigation_records:
            inv_bc = str(inv.get("barcode", ""))
            if inv_bc:
                investigation_status_by_barcode[inv_bc] = inv.get("status", "").strip().lower()
                raw = inv.get("vitals", {})
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        raw = {}
                vitals_by_barcode[inv_bc] = raw

        # ── STEP 7: Bulk-fetch TestValue records ──────────────────────────────
        lab_tests_by_barcode = {}
        approved_count_by_barcode = {}  # approved test count per barcode

        for record in TestValue.objects.filter(barcode__in=barcodes):
            bc = str(record.barcode)
            td = record.testdetails
            parsed = json.loads(td) if isinstance(td, str) else (td or [])
            if isinstance(parsed, list):
                lab_tests_by_barcode.setdefault(bc, []).extend(parsed)
                # Count tests where approve=True
                approved = sum(1 for t in parsed if isinstance(t, dict) and t.get("approve"))
                approved_count_by_barcode[bc] = approved_count_by_barcode.get(bc, 0) + approved

        # ── STEP 8: Build response — iterate over barcodes from core_sample ───
        formatted_data = []

        for bc in barcodes:
            billing = barcode_to_billing.get(bc)
            if not billing:
                continue  # no billing record for this barcode, skip

            eid = barcode_to_employee_id.get(bc, "N/A")
            patient_detail = patient_details_map.get(eid, {})
            if not isinstance(patient_detail, dict):
                patient_detail = {}

            sample_tests = sample_testdetails_by_barcode.get(bc, [])
            sample_date = sample_date_by_barcode.get(bc, "N/A")

            testnames = ", ".join([
                t.get("testname", "") if isinstance(t, dict) else str(t)
                for t in sample_tests
            ])
            no_of_tests = len(sample_tests)

            age_value = patient_detail.get("age", "N/A")
            age_type = patient_detail.get("age_type", "")
            age = f"{age_value} {age_type}" if age_type else str(age_value)

            # CHC tests from billing
            chc_raw = billing.get("chctestdetails", "[]")
            chc_tests = []
            if isinstance(chc_raw, str):
                try:
                    chc_tests = json.loads(chc_raw)
                except (json.JSONDecodeError, TypeError):
                    chc_tests = []
            elif isinstance(chc_raw, list):
                chc_tests = chc_raw

            chc_status_map_for_barcode = chc_status_by_barcode.get(str(bc), {})
            chc_tests_with_status = []
            for ct in chc_tests:
                tid = str(ct.get("test_id", ""))
                info = chc_status_map_for_barcode.get(tid, {})
                
                inv_status = info.get("inv_status", "")   # ← read investigation status
                has_content = info.get("has_report") or info.get("has_file")

                # If investigation is explicitly "pending", treat as Pending
                # regardless of whether files/reports exist
                if inv_status == "approved" and has_content:
                    chc_status = "Approved"
                else:
                    chc_status = "Pending"

                chc_tests_with_status.append({
                    "test_id": tid,
                    "testname": ct.get("testname", ""),
                    "has_report": info.get("has_report", False),
                    "has_file": info.get("has_file", False),
                    "report": info.get("report", ""),
                    "notes": info.get("notes", ""),
                    "files": info.get("files", []),
                    "status": chc_status,   # ← driven by inv_status first, then content
                })

            chc_overall, chc_pending, chc_approved = _chc_approval_status(
                chc_tests, chc_status_map_for_barcode
            )

            raw_lab_tests = lab_tests_by_barcode.get(str(bc), [])
            investigation_status = investigation_status_by_barcode.get(str(bc), "")
            vitals = vitals_by_barcode.get(str(bc), {})

            # Compute lab_approval per barcode
            total_sample = valid_test_count_by_barcode.get(str(bc), 0)
            approved_count = approved_count_by_barcode.get(str(bc), 0)
            lab_approval = (
                "Approved" if total_sample > 0 and approved_count >= total_sample
                else "Pending"
            )

            # ── KEY FIX: Combined investigation status = lab + CHC both must be approved ──
            # If lab is Pending → overall is Pending regardless of CHC status
            # If lab is Approved but CHC has pending tests → Pending
            # Only "All Approved" when BOTH lab and all CHC tests are approved
            if lab_approval == "Pending" or chc_overall != "All Approved":
                combined_investigation_status = "Pending"
            else:
                combined_investigation_status = "All Approved"

            status, approval_type, approved_by_name = _compute_corporate_approval_status(
                bc=bc,
                approval_status_map=approval_status_map,
                chc_tests_with_status=chc_tests_with_status,
                lab_test_values=raw_lab_tests,
                no_of_tests=no_of_tests,
                investigation_status=investigation_status,
                vitals=vitals,
            )

            # Billing created_date
            created_date = billing.get("created_date")
            formatted_date = "N/A"
            if created_date:
                if isinstance(created_date, datetime):
                    formatted_date = created_date.strftime("%Y-%m-%d")
                else:
                    try:
                        formatted_date = datetime.strptime(
                            str(created_date), "%Y-%m-%d"
                        ).strftime("%Y-%m-%d")
                    except Exception:
                        formatted_date = str(created_date)
                        
            company_id = patient_detail.get("company_id", "N/A")
            company_name = company_name_map.get(company_id, company_id)

            formatted_data.append({
                "date": sample_date,
                "patient_id": eid,
                "patient_name": patient_detail.get("employee_name", "N/A"),
                "gender": patient_detail.get("gender", "N/A"),
                "age": age,
                "email": patient_detail.get("email", "N/A"),
                "branch": patient_detail.get("company_id", "N/A"),
                "branch_name": company_name,
                "test_names": testnames,
                "no_of_tests": no_of_tests,
                "barcode": bc,
                "status": status,
                "lab_approval": lab_approval,
                "chc_tests": chc_tests_with_status,
                "chc_investigation_status": combined_investigation_status,  # lab + CHC combined
                "chc_pending_tests": chc_pending,
                "chc_approved_tests": chc_approved,
                "approval_type": approval_type,
                "approved_by_name": approved_by_name,
            })

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
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup

        franchise_billing_collection          = db.core_billing
        franchise_sample_collection           = db.core_sample
        franchise_patient_collection          = db.core_chcregistration
        franchise_investigation_collection    = db.core_investigation
        franchise_overall_approval_collection = db.overallApproval
        franchise_company_collection          = db.core_company

        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

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
        def get_employee_signature_data(emp_id):
            if not emp_id:
                return None
            try:
                profile = profile_collection.find_one({"employeeId": emp_id})
                if not profile:
                    return None
                employee_name     = profile.get("employeeName", "")
                designation       = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                signature_base64  = None
                if signature_file_id:
                    try:
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        signature_file   = fs.get(signature_file_id)
                        signature_bytes  = signature_file.read()
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {emp_id}: {str(e)}")
                return {"employeeName": employee_name, "designation": designation, "signatureBase64": signature_base64}
            except Exception as e:
                print(f"Error fetching employee data for {emp_id}: {str(e)}")
                return None

        franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
        if not franchise_billing:
            return JsonResponse({'error': 'Billing record not found'}, status=404)

        employee_id = franchise_billing.get('employee_id')
        if not employee_id:
            return JsonResponse({'error': 'Employee ID not found'}, status=404)

        franchise_patient = franchise_patient_collection.find_one({"employee_id": employee_id})
        if not franchise_patient:
            return JsonResponse({'error': 'Patient not found'}, status=404)

        # ── Resolve patient gender for reference range selection ──────────────
        # core_chcregistration has a 'gender' field
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

        company_data = None
        company_id   = franchise_patient.get("company_id")
        if company_id:
            company_data = franchise_company_collection.find_one({"company_id": company_id})

        franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})

        franchise_investigation = franchise_investigation_collection.find_one({
            "barcode": barcode, "status": "approved"
        })

        franchise_overall_approval = franchise_overall_approval_collection.find_one({
            "barcode": barcode, "status": "approved"
        })

        test_values = TestValue.objects.filter(barcode=barcode)

        # Parse vitals from investigation
        vitals_data = {}
        if franchise_investigation:
            try:
                vitals_raw = franchise_investigation.get('vitals', {})
                if isinstance(vitals_raw, str):
                    vitals_raw = json.loads(vitals_raw)
                for key, value in vitals_raw.items():
                    if value and str(value).strip() and str(value).strip() != "0":
                        vitals_data[key] = value
            except (json.JSONDecodeError, AttributeError):
                vitals_data = {}

        # ── Dynamic fields from investigation ────────────────────────────────────
        dynamic_fields_data = []
        if franchise_investigation:
            raw_dynamic = franchise_investigation.get("dynamic_fields", [])
            if isinstance(raw_dynamic, list):
                for field in raw_dynamic:
                    field_name = field.get("field_name", "")
                    field_values = field.get("field_values", [])
                    if field_name and isinstance(field_values, list) and field_values:
                        # Only include entries with non-empty values
                        valid_pairs = [
                            {"key": fv.get("key", ""), "value": fv.get("value", "")}
                            for fv in field_values
                            if isinstance(fv, dict) and (fv.get("key") or fv.get("value"))
                        ]
                        if valid_pairs:
                            dynamic_fields_data.append({
                                "field_name": field_name,
                                "field_values": valid_pairs,
                            })

        investigation_file_ids = {}
        investigation_notes    = {}

        if franchise_investigation:
            for file_field in ["ecg_file", "pft_file", "audiometric_file", "xrayfilm_file"]:
                file_id = franchise_investigation.get(file_field)
                if file_id:
                    investigation_file_ids[file_field] = file_id

            for note_field, key in [
                ("ecg_notes",        "ecg_notes"),
                ("pft_notes",        "pft_notes"),
                ("audiometry_notes", "audiometry_notes"),
                ("xray_notes",       "xray_notes"),
                ("xray_report",      "xray_report"),
            ]:
                note_value = franchise_investigation.get(note_field)
                if note_value and note_value.strip():
                    investigation_notes[key] = note_value

        medical_history_data = {}
        if franchise_investigation:
            patient_history = franchise_investigation.get("patient_history")
            if patient_history and patient_history.strip():
                if patient_history.lower() not in ["nil", "nil significant", "no previous history", "none"]:
                    medical_history_data["patient_history"] = patient_history

        clinical_examination_data = {}
        if franchise_investigation:
            for field in ["cardiovascular_system", "respiratory_system", "central_nervous_system",
                          "locomotor_system", "skin"]:
                value = franchise_investigation.get(field)
                if value and value.strip() and value.lower() not in ["normal", "nil", "nil significant"]:
                    clinical_examination_data[field] = value

        ophthalmology_data = None
        if franchise_investigation:
            chc_ophthalmology = franchise_investigation.get("CHCT001")
            if chc_ophthalmology:
                
                # ── Helper: check if a nested dict has any non-empty value ────────────
                def has_data(d):
                    if not d:
                        return False
                    return any(str(v).strip() for v in d.values() if v is not None)

                distance       = chc_ophthalmology.get("distance", {})
                near_vision    = chc_ophthalmology.get("nearVision", {})
                colour_vision  = chc_ophthalmology.get("colourVision", {})
                ocular_movement = chc_ophthalmology.get("ocularmovement", {})
                complaints     = (chc_ophthalmology.get("complaints") or "").strip()
                remarks        = (chc_ophthalmology.get("remarks") or "").strip()

                # Only include ophthalmology if at least one field has real data
                any_data = (
                    has_data(distance)
                    or has_data(near_vision)
                    or has_data(colour_vision)
                    or has_data(ocular_movement)
                    or bool(complaints)
                    or bool(remarks)
                )

                if any_data:
                    ophthalmology_data = {}
                    if has_data(distance):
                        ophthalmology_data["distance"] = {
                            "right": str(distance.get("right", "")),
                            "left":  str(distance.get("left", ""))
                        }
                    if has_data(near_vision):
                        ophthalmology_data["near_vision"] = {
                            "right": str(near_vision.get("right", "")),
                            "left":  str(near_vision.get("left", ""))
                        }
                    if has_data(colour_vision):
                        ophthalmology_data["color_vision"] = {
                            "right": str(colour_vision.get("right", "")),
                            "left":  str(colour_vision.get("left", ""))
                        }
                    if has_data(ocular_movement):
                        ophthalmology_data["ocularmovement"] = {
                            "right": str(ocular_movement.get("right", "")),
                            "left":  str(ocular_movement.get("left", ""))
                        }
                    if complaints:
                        ophthalmology_data["complaints"] = complaints
                    if remarks:
                        ophthalmology_data["remarks"] = remarks

        barcodes = []
        try:
            all_barcodes = franchise_billing_collection.find(
                {"employee_id": employee_id}, {"barcode": 1, "_id": 0}
            )
            barcodes = [bc.get("barcode") for bc in all_barcodes if bc.get("barcode")]
            barcodes = list(dict.fromkeys(barcodes))
        except Exception:
            barcodes = []

        sample_testdetails = []
        if franchise_sample:
            try:
                sample_testdetails = json.loads(franchise_sample.get('testdetails', '[]'))
            except json.JSONDecodeError:
                sample_testdetails = []

        patient_details = {
            "patient_id":  employee_id,
            "patientname": franchise_patient.get("employee_name"),
            "age":         franchise_patient.get("age"),
            "gender":      franchise_patient.get("gender"),
            "doj":      franchise_patient.get("doj"),
            "designation":      franchise_patient.get("designation"),
            "employee_type":      franchise_patient.get("employee_type"),
            "date":        franchise_billing.get("created_date"),
            "barcode":     barcode,
            "barcodes":    barcodes,
            "testdetails": [],
        }

        if company_data and company_data.get("company_id"):
            patient_details["company_id"] = company_data.get("company_id")
        if company_data and company_data.get("company_name"):
            patient_details["company_name"] = company_data.get("company_name")
        if franchise_patient.get("department"):
            patient_details["department"] = franchise_patient.get("department")
        if franchise_patient.get("dob"):
            patient_details["dob"] = franchise_patient.get("dob")
        if vitals_data:
            patient_details["vitals"] = {}
            for key in ["height_cm", "weight_kg", "bmi", "blood_pressure", "pulse", "spo2", "BP_status", "bmi_status", "spo2_status"]:
                if vitals_data.get(key):
                    display_key = key.replace("_cm", "").replace("_kg", "")
                    patient_details["vitals"][display_key] = vitals_data.get(key)
        if dynamic_fields_data:
            patient_details["dynamic_fields"] = dynamic_fields_data
        if medical_history_data:
            patient_details["medical_history"] = medical_history_data
        if clinical_examination_data:
            patient_details["clinical_examination"] = clinical_examination_data
        if ophthalmology_data:
            patient_details["ophthalmology"] = ophthalmology_data
        if investigation_notes:
            patient_details["investigation_notes"] = investigation_notes
        if investigation_file_ids:
            patient_details["investigation_file_ids"] = investigation_file_ids
        if franchise_overall_approval:
            final_assessment = {}
            impression = franchise_overall_approval.get("impression", "")
            if impression and impression.strip():
                final_assessment["impression"] = impression
            remarks = franchise_overall_approval.get("remarks", "")
            if remarks and remarks.strip():
                final_assessment["remarks"] = remarks
            approved_date = franchise_overall_approval.get("approved_date", "")
            if approved_date:
                final_assessment["approved_date"] = approved_date
            if final_assessment:
                patient_details["final_assessment"] = final_assessment

        all_approvers = set()

        if test_values.exists():
            for test_value in test_values:
                try:
                    testvalue_details = (
                        json.loads(test_value.testdetails)
                        if isinstance(test_value.testdetails, str)
                        else test_value.testdetails
                    )
                    if not isinstance(testvalue_details, list):
                        continue

                    for test_detail in testvalue_details:
                        if test_detail.get("approve") is not True:
                            continue

                        test_id   = test_detail.get("test_id")
                        testname  = test_detail.get("testname")
                        device_id = test_detail.get("device_id", "N/A")
                        if not test_id:
                            continue

                        core_test = core_testdetails_collection.find_one({"test_id": test_id})

                        if core_test:
                            testname      = core_test.get("test_name", testname)
                            specimen_type = core_test.get("specimen_type", "N/A")
                            department    = core_test.get("department", test_detail.get("department", ""))
                            NABL    = core_test.get("NABL", "")
                        else:
                            specimen_type = test_detail.get("specimen_type", "")
                            department    = test_detail.get("department", "")
                            NABL    = test_detail.get("NABL", "")

                        if not testname:
                            continue

                        approve_by = test_detail.get("approve_by", "")
                        if approve_by:
                            all_approvers.add(approve_by)

                        sample_status = None
                        for sample_test in sample_testdetails:
                            if sample_test.get("test_id") == test_id or sample_test.get("testname") == testname:
                                sample_status = sample_test
                                break

                        test_response = {"testname": testname}
                        if department:
                            test_response["department"] = department
                        if NABL:
                            test_response["NABL"] = NABL
                        if test_detail.get("verified_by"):
                            test_response["verified_by"] = test_detail.get("verified_by")
                        if test_detail.get("approve_by"):
                            test_response["approve_by"] = test_detail.get("approve_by")
                        if test_detail.get("approve_time"):
                            test_response["approve_time"] = test_detail.get("approve_time")
                        if test_detail.get("status"):
                            test_response["status"] = test_detail.get("status")
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

                        if test_detail.get("parameters"):
                            # ── Parameterised test ────────────────────────────
                            processed_parameters = []
                            for param_index, param in enumerate(test_detail.get("parameters", [])):
                                test_code     = param.get("test_code")
                                value         = param.get("value", "")
                                param_comment = param.get("comment", "")

                                param_def = get_parameter_from_core(
                                    core_test, device_id,
                                    test_code=test_code,
                                    param_index=param_index,
                                ) if core_test else None

                                processed_param = {}
                                if param_def:
                                    # Gender-resolved reference_range
                                    ref_range = resolve_reference_range(param_def, patient_gender)

                                    processed_param["name"]            = param_def.get("test_name", param.get("name", ""))
                                    processed_param["value"]           = value
                                    processed_param["unit"]            = param_def.get("unit", "")
                                    processed_param["specimen_type"]   = specimen_type
                                    processed_param["reference_range"] = ref_range   # gender-resolved
                                    processed_param["method"]          = param_def.get("method", "")
                                    if param_def.get("sub_title"):
                                        processed_param["sub_title"] = param_def.get("sub_title")
                                    if param_comment:
                                        processed_param["comment"] = param_comment
                                else:
                                    # Fallback: no core definition found
                                    if param.get("name"):
                                        processed_param["name"]            = param.get("name")
                                    if value:
                                        processed_param["value"]           = value
                                    if param.get("unit"):
                                        processed_param["unit"]            = param.get("unit")
                                    if param.get("specimen_type"):
                                        processed_param["specimen_type"]   = param.get("specimen_type")
                                    if param.get("reference_range"):
                                        processed_param["reference_range"] = param.get("reference_range")
                                    if param.get("method"):
                                        processed_param["method"]          = param.get("method")
                                    if param.get("sub_title"):
                                        processed_param["sub_title"]       = param.get("sub_title")
                                    if param_comment:
                                        processed_param["comment"]         = param_comment

                                if processed_param:
                                    processed_parameters.append(processed_param)

                            if processed_parameters:
                                test_response["parameters"] = processed_parameters

                        else:
                            # ── Single-value test ─────────────────────────────
                            if core_test:
                                # Gender-resolved reference_range
                                ref_range = resolve_reference_range(core_test, patient_gender)

                                if core_test.get("method"):
                                    test_response["method"]          = core_test.get("method")
                                if specimen_type:
                                    test_response["specimen_type"]   = specimen_type
                                if test_detail.get("value"):
                                    test_response["value"]           = test_detail.get("value")
                                if core_test.get("unit"):
                                    test_response["unit"]            = core_test.get("unit")
                                if ref_range:
                                    test_response["reference_range"] = ref_range   # gender-resolved
                                if test_detail.get("sub_title"):
                                    test_response["sub_title"]       = test_detail.get("sub_title")
                            else:
                                # No core_test — use raw values from test_detail
                                if test_detail.get("method"):
                                    test_response["method"]          = test_detail.get("method")
                                if test_detail.get("specimen_type"):
                                    test_response["specimen_type"]   = test_detail.get("specimen_type")
                                if test_detail.get("value"):
                                    test_response["value"]           = test_detail.get("value")
                                if test_detail.get("unit"):
                                    test_response["unit"]            = test_detail.get("unit")
                                if test_detail.get("reference_range"):
                                    test_response["reference_range"] = test_detail.get("reference_range")
                                if test_detail.get("sub_title"):
                                    test_response["sub_title"]       = test_detail.get("sub_title")

                        patient_details["testdetails"].append(test_response)

                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"Error processing test: {str(e)}")
                    continue

        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)

        client.close()
        response_data = {"patient_data": patient_details, "signatures": signatures_data}
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


def get_investigation_status(request):
    """
    Returns lab approval + CHC investigation status + vitals + patient history
    CHCT001 is checked directly from core_investigation (not test_results)
    """

    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)

    def parse_json(value, default):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return default
        return value if value else default

    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup

        investigation_collection = db.core_investigation
        sample_collection = db.core_sample
        billing_collection = db.core_billing

        investigation = investigation_collection.find_one({"barcode": barcode}) or {}
        inv_status = investigation.get("status", "").strip().lower()

        # -----------------------------
        # VITALS
        # -----------------------------
        vitals_data = {}
        raw_vitals = parse_json(investigation.get("vitals", {}), {})

        for key, value in raw_vitals.items():
            if value and str(value).strip() not in ["", "0"]:
                vitals_data[key] = value

        # -----------------------------
        # PATIENT HISTORY
        # -----------------------------
        patient_history = investigation.get("patient_history", "") or ""

        # -----------------------------
        # LAB APPROVAL
        # -----------------------------
        total_sample_tests = 0

        try:
            franchise_sample = sample_collection.find_one({"barcode": barcode})

            if franchise_sample and franchise_sample.get("testdetails"):

                sample_tests = parse_json(franchise_sample.get("testdetails"), [])

                invalid_status = {"Rejected", "Outsource"}

                valid_tests = [
                    t for t in sample_tests
                    if t.get("samplestatus", "").strip() not in invalid_status
                ]

                total_sample_tests = len(valid_tests)

        except Exception as e:
            print(f"Error calculating total_sample_tests: {e}")

        approved_tests_count = 0

        try:
            for record in TestValue.objects.filter(barcode=barcode):

                tests_list = parse_json(record.testdetails, [])

                for t in tests_list:
                    if isinstance(t, dict) and t.get("approve"):
                        approved_tests_count += 1

        except Exception as e:
            print(f"Error calculating approved_tests_count: {e}")

        lab_approval = (
            "approved"
            if total_sample_tests > 0 and approved_tests_count >= total_sample_tests
            else "pending"
        )

        # -----------------------------
        # CHC TEST STATUS FROM test_results
        # -----------------------------
        chc_test_status = {}

        test_results = parse_json(investigation.get("test_results", []), [])

        for tr in test_results:

            tid = str(tr.get("test_id", "")).strip()
            if not tid:
                continue

            files = tr.get("files", []) or []
            report = (tr.get("report") or "").strip()
            notes = tr.get("notes") or ""

            has_report = bool(report)
            has_file = bool(files)

            chc_test_status[tid] = {
                "test_name": tr.get("test_name", ""),
                "report": report,
                "notes": notes,
                "files": files,
                "has_report": has_report,
                "has_file": has_file,
                "status": "approved" if (has_report or has_file) else "pending",
            }

        # -----------------------------
        # SPECIAL CASE → CHCT001
        # -----------------------------
        ophthal_data = investigation.get("CHCT001", {})

        if ophthal_data:
            chc_test_status["CHCT001"] = {
                "test_name": "Ophthalmology",
                "report": "",
                "notes": ophthal_data.get("remarks", ""),
                "files": [],
                "has_report": True,
                "has_file": False,
                "status": "approved",
            }

        # -----------------------------
        # GET BILLING TESTS
        # -----------------------------
        billing = billing_collection.find_one({"barcode": barcode}) or {}
        chc_tests_list = parse_json(billing.get("chctestdetails", []), [])

        # -----------------------------
        # MERGE BILLING + STATUS
        # -----------------------------
        chc_tests_enriched = []

        for ct in chc_tests_list:

            tid = str(ct.get("test_id", "")).strip()

            info = chc_test_status.get(tid, {})

            chc_tests_enriched.append({
                "test_id": tid,
                "testname": ct.get("testname", ""),
                "status": info.get("status", "pending"),
                "has_report": info.get("has_report", False),
                "has_file": info.get("has_file", False),
                "report": info.get("report", ""),
                "notes": info.get("notes", ""),
                "files": info.get("files", []),
            })

        # -----------------------------
        # CHC OVERALL STATUS
        # -----------------------------
        chc_overall_status = "approved" if inv_status == "approved" else "pending"

        client.close()

        return JsonResponse({
            "success": True,
            "investigation": {},
            "lab_approval": lab_approval,
            "chc_tests": chc_tests_enriched,
            "chc_investigation_status": chc_overall_status,
            "vitals": vitals_data,
            "patient_history": patient_history,
        })

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)



@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_batch_investigation_status(request):
    """
    Batch version of get_investigation_status.
    Accepts: {"barcodes": ["300010", "300011", ...]}
    Now also returns vitals and patient_history per barcode.
    """
    barcodes = request.data.get('barcodes', [])
    if not barcodes or not isinstance(barcodes, list):
        return JsonResponse({'error': 'Barcodes array is required'}, status=400)
 
    barcodes = [str(bc) for bc in barcodes]
 
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
 
        investigation_collection = db.core_investigation
        sample_collection        = db.core_sample
        billing_collection       = db.core_billing
 
        # ── Bulk fetch everything in 3 queries total ──────────────────────────
        investigations = list(investigation_collection.find({"barcode": {"$in": barcodes}}))
        samples        = list(sample_collection.find({"barcode": {"$in": barcodes}}))
        billings       = list(billing_collection.find({"barcode": {"$in": barcodes}}))
        test_value_records = TestValue.objects.filter(barcode__in=barcodes)
 
        # Build lookup maps
        investigation_map = {str(inv['barcode']): inv for inv in investigations}
        sample_map        = {str(s['barcode']): s   for s   in samples}
        billing_map       = {str(b['barcode']): b   for b   in billings}
 
        # Approved test count per barcode
        approved_tests_map = {}
        for record in test_value_records:
            bc = str(record.barcode)
            td = record.testdetails
            tests_list = json.loads(td) if isinstance(td, str) else (td or [])
            count = sum(1 for t in tests_list if isinstance(t, dict) and t.get("approve"))
            approved_tests_map[bc] = approved_tests_map.get(bc, 0) + count
 
        results = {}
 
        for barcode in barcodes:
            investigation = investigation_map.get(barcode)
            sample        = sample_map.get(barcode)
            billing       = billing_map.get(barcode)
 
            inv_status = investigation.get("status", "").strip().lower() if investigation else ""
 
            # ── Vitals ────────────────────────────────────────────────────────
            vitals_data = {}
            if investigation:
                raw_vitals = investigation.get("vitals", {})
                if isinstance(raw_vitals, str):
                    try: raw_vitals = json.loads(raw_vitals)
                    except Exception: raw_vitals = {}
                for k, v in raw_vitals.items():
                    if v and str(v).strip() not in ("", "0"):
                        vitals_data[k] = v
 
            # ── Patient history ────────────────────────────────────────────────
            patient_history = ""
            if investigation:
                patient_history = investigation.get("patient_history", "") or ""
 
            # ── Lab approval ──────────────────────────────────────────────────
            total_sample_tests = 0
            if sample and sample.get('testdetails'):
                raw = sample.get('testdetails')
                sample_tests_list = json.loads(raw) if isinstance(raw, str) else (raw or [])
                invalid = {"Rejected", "Outsource"}
                total_sample_tests = sum(
                    1 for t in sample_tests_list
                    if t.get("samplestatus", "").strip() not in invalid
                )
            approved_count = approved_tests_map.get(barcode, 0)
            lab_approval = "approved" if (total_sample_tests > 0 and approved_count >= total_sample_tests) else "pending"
 
            # ── Per-CHC-test status from investigation.test_results ───────────
            chc_test_status = {}
            if investigation:
                test_results = investigation.get("test_results", [])
                if isinstance(test_results, str):
                    try: test_results = json.loads(test_results)
                    except Exception: test_results = []
                for tr in (test_results or []):
                    tid    = str(tr.get("test_id", ""))
                    if not tid: continue
                    files  = tr.get("files", []) or []
                    report = (tr.get("report") or "").strip()
                    notes  = tr.get("notes") or ""
                    chc_test_status[tid] = {
                        "test_name":  tr.get("test_name", ""),
                        "report":     report,
                        "notes":      notes,
                        "files":      files,
                        "has_report": bool(report),
                        "has_file":   bool(files),
                        "status":     "approved" if (bool(report) or bool(files)) else "pending",
                    }
 
            # CHCT001 from core_investigation directly
            ophthal_data = investigation.get("CHCT001", {}) if investigation else {}
            if ophthal_data:
                chc_test_status["CHCT001"] = {
                    "test_name":  "Ophthalmology",
                    "report":     "",
                    "notes":      ophthal_data.get("remarks", ""),
                    "files":      [],
                    "has_report": True,
                    "has_file":   False,
                    "status":     "approved",
                }
 
            # Enrich with billing chctestdetails
            chc_tests_list = []
            if billing:
                chc_raw = billing.get("chctestdetails", "[]")
                if isinstance(chc_raw, str):
                    try: chc_tests_list = json.loads(chc_raw)
                    except Exception: chc_tests_list = []
                elif isinstance(chc_raw, list):
                    chc_tests_list = chc_raw
 
            chc_tests_enriched = []
            for ct in chc_tests_list:
                tid  = str(ct.get("test_id", ""))
                info = chc_test_status.get(tid, {})
                chc_tests_enriched.append({
                    "test_id":    tid,
                    "testname":   ct.get("testname", ""),
                    "status":     info.get("status", "pending"),
                    "has_report": info.get("has_report", False),
                    "has_file":   info.get("has_file",   False),
                    "report":     info.get("report",     ""),
                    "notes":      info.get("notes",      ""),
                    "files":      info.get("files",      []),
                })
 
            results[barcode] = {
                'lab_approval':             lab_approval,
                'chc_tests':                chc_tests_enriched,
                'chc_investigation_status': "approved" if inv_status == "approved" else "pending",
                'vitals':                   vitals_data,       # ← NEW
                'patient_history':          patient_history,   # ← NEW
                # legacy field kept for backward compat
                'investigation': {
                    "xray_report": "pending", "xrayfilm_file": "pending",
                    "ecg_file": "pending", "pft_file": "pending",
                    "audiometric_file": "pending",
                },
            }
 
        client.close()
        return JsonResponse({'success': True, 'results': results})
 
    except Exception as e:
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
        overall_approval_collection = db.overallApproval

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
@csrf_exempt
def get_batch_corporate_health_reports(request):
    """
    Get multiple corporate health reports in one call for batch PDF generation.
    Ophthalmology is now read from CHCT001 in core_investigation (same as
    corporate_health_report) instead of the separate core_ophthalmology collection.
    """
    barcodes = request.data.get('barcodes', [])

    if not barcodes or not isinstance(barcodes, list):
        return JsonResponse({'error': 'Barcodes array is required'}, status=400)

    if len(barcodes) > 100:
        return JsonResponse({'error': 'Maximum 100 barcodes allowed per batch'}, status=400)

    barcodes = [str(bc) for bc in barcodes]
    print(f"Processing batch of {len(barcodes)} barcodes")

    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup

        franchise_billing_collection          = db.core_billing
        franchise_sample_collection           = db.core_sample
        franchise_patient_collection          = db.core_chcregistration
        franchise_investigation_collection    = db.core_investigation
        franchise_overall_approval_collection = db.overallApproval
        franchise_company_collection          = db.core_company

        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

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
        def get_employee_signature_data(emp_id):
            if not emp_id:
                return None
            try:
                profile = profile_collection.find_one({"employeeId": emp_id})
                if not profile:
                    return None
                employee_name     = profile.get("employeeName", "")
                designation       = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                signature_base64  = None
                if signature_file_id:
                    try:
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        signature_file   = fs.get(signature_file_id)
                        signature_bytes  = signature_file.read()
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {emp_id}: {str(e)}")
                return {"employeeName": employee_name, "designation": designation, "signatureBase64": signature_base64}
            except Exception as e:
                print(f"Error fetching employee data for {emp_id}: {str(e)}")
                return None

        # ── Gender-aware reference_range resolver ─────────────────────────────
        # Defined once at batch level — reused for every barcode in the loop.
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

        results = {}

        for barcode in barcodes:
            try:
                franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
                if not franchise_billing:
                    results[barcode] = {'error': 'Billing record not found'}
                    continue

                employee_id = franchise_billing.get('employee_id')
                if not employee_id:
                    results[barcode] = {'error': 'Employee ID not found'}
                    continue

                franchise_patient = franchise_patient_collection.find_one({"employee_id": employee_id})
                if not franchise_patient:
                    results[barcode] = {'error': 'Patient not found'}
                    continue

                # Resolve patient gender for this barcode's patient
                patient_gender = (franchise_patient.get('gender') or '').strip()

                company_data = None
                company_id   = franchise_patient.get("company_id")
                if company_id:
                    company_data = franchise_company_collection.find_one({"company_id": company_id})

                franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})

                franchise_investigation = franchise_investigation_collection.find_one({
                    "barcode": barcode,
                })

                franchise_overall_approval = franchise_overall_approval_collection.find_one({
                    "barcode": barcode, "status": "approved"
                })

                test_values = TestValue.objects.filter(barcode=barcode)

                # Vitals
                vitals_data = {}
                if franchise_investigation:
                    try:
                        vitals_raw = franchise_investigation.get('vitals', {})
                        if isinstance(vitals_raw, str):
                            vitals_raw = json.loads(vitals_raw)
                        for key, value in vitals_raw.items():
                            if value and str(value).strip() and str(value).strip() != "0":
                                vitals_data[key] = value
                    except (json.JSONDecodeError, AttributeError):
                        vitals_data = {}

                # Investigation notes
                investigation_notes = {}
                if franchise_investigation:
                    for note_field, key in [
                        ("ecg_notes",        "ecg_notes"),
                        ("pft_notes",        "pft_notes"),
                        ("audiometry_notes", "audiometry_notes"),
                        ("xray_notes",       "xray_notes"),
                        ("xray_report",      "xray_report"),
                    ]:
                        note_value = franchise_investigation.get(note_field)
                        if note_value and note_value.strip():
                            investigation_notes[key] = note_value

                # Medical history
                medical_history_data = {}
                if franchise_investigation:
                    patient_history = franchise_investigation.get("patient_history")
                    if patient_history and patient_history.strip():
                        if patient_history.lower() not in ["nil", "nil significant", "no previous history", "none"]:
                            medical_history_data["patient_history"] = patient_history

                # ── NEW: dynamic fields ──────────────────────────────────────────
                dynamic_fields_data = []
                if franchise_investigation:
                    raw_dynamic = franchise_investigation.get("dynamic_fields", [])
                    if isinstance(raw_dynamic, list):
                        for field in raw_dynamic:
                            field_name = field.get("field_name", "")
                            field_values = field.get("field_values", [])
                            if field_name and isinstance(field_values, list) and field_values:
                                valid_pairs = [
                                    {"key": fv.get("key", ""), "value": fv.get("value", "")}
                                    for fv in field_values
                                    if isinstance(fv, dict) and (fv.get("key") or fv.get("value"))
                                ]
                                if valid_pairs:
                                    dynamic_fields_data.append({
                                        "field_name": field_name,
                                        "field_values": valid_pairs,
                                    })

                # Clinical examination
                clinical_examination_data = {}
                if franchise_investigation:
                    for field in ["cardiovascular_system", "respiratory_system", "central_nervous_system",
                                  "locomotor_system", "skin"]:
                        value = franchise_investigation.get(field)
                        if value and value.strip() and value.lower() not in ["normal", "nil", "nil significant"]:
                            clinical_examination_data[field] = value

                # Ophthalmology — read from CHCT001 in core_investigation
                ophthalmology_data = None
                if franchise_investigation:
                    chc_ophthalmology = franchise_investigation.get("CHCT001")
                    if chc_ophthalmology:
                        
                        # ── Helper: check if a nested dict has any non-empty value ────────────
                        def has_data(d):
                            if not d:
                                return False
                            return any(str(v).strip() for v in d.values() if v is not None)

                        distance       = chc_ophthalmology.get("distance", {})
                        near_vision    = chc_ophthalmology.get("nearVision", {})
                        colour_vision  = chc_ophthalmology.get("colourVision", {})
                        ocular_movement = chc_ophthalmology.get("ocularmovement", {})
                        complaints     = (chc_ophthalmology.get("complaints") or "").strip()
                        remarks        = (chc_ophthalmology.get("remarks") or "").strip()

                        # Only include ophthalmology if at least one field has real data
                        any_data = (
                            has_data(distance)
                            or has_data(near_vision)
                            or has_data(colour_vision)
                            or has_data(ocular_movement)
                            or bool(complaints)
                            or bool(remarks)
                        )

                        if any_data:
                            ophthalmology_data = {}
                            if has_data(distance):
                                ophthalmology_data["distance"] = {
                                    "right": str(distance.get("right", "")),
                                    "left":  str(distance.get("left", ""))
                                }
                            if has_data(near_vision):
                                ophthalmology_data["near_vision"] = {
                                    "right": str(near_vision.get("right", "")),
                                    "left":  str(near_vision.get("left", ""))
                                }
                            if has_data(colour_vision):
                                ophthalmology_data["color_vision"] = {
                                    "right": str(colour_vision.get("right", "")),
                                    "left":  str(colour_vision.get("left", ""))
                                }
                            if has_data(ocular_movement):
                                ophthalmology_data["ocularmovement"] = {
                                    "right": str(ocular_movement.get("right", "")),
                                    "left":  str(ocular_movement.get("left", ""))
                                }
                            if complaints:
                                ophthalmology_data["complaints"] = complaints
                            if remarks:
                                ophthalmology_data["remarks"] = remarks

                # Sample testdetails
                sample_testdetails = []
                if franchise_sample:
                    try:
                        sample_testdetails = json.loads(franchise_sample.get('testdetails', '[]'))
                    except json.JSONDecodeError:
                        sample_testdetails = []

                patient_details = {
                    "patient_id":  employee_id,
                    "patientname": franchise_patient.get("employee_name"),
                    "age":         franchise_patient.get("age"),
                    "gender":      franchise_patient.get("gender"),
                    "doj":      franchise_patient.get("doj"),
                    "employee_type":      franchise_patient.get("employee_type"),
                    "date":        franchise_billing.get("created_date"),
                    "barcode":     barcode,
                    "testdetails": [],
                }

                if company_data and company_data.get("company_id"):
                    patient_details["company_id"] = company_data.get("company_id")
                if company_data and company_data.get("company_name"):
                    patient_details["company_name"] = company_data.get("company_name")
                if franchise_patient.get("department"):
                    patient_details["department"] = franchise_patient.get("department")
                if franchise_patient.get("dob"):
                    patient_details["dob"] = franchise_patient.get("dob")
                if vitals_data:
                    patient_details["vitals"] = {}
                    for key in ["height_cm", "weight_kg", "bmi", "blood_pressure", "pulse", "spo2", "BP_status", "bmi_status", "spo2_status"]:
                        if vitals_data.get(key):
                            display_key = key.replace("_cm", "").replace("_kg", "")
                            patient_details["vitals"][display_key] = vitals_data.get(key)
                if medical_history_data:
                    patient_details["medical_history"] = medical_history_data
                if dynamic_fields_data:
                    patient_details["dynamic_fields"] = dynamic_fields_data
                if clinical_examination_data:
                    patient_details["clinical_examination"] = clinical_examination_data
                if ophthalmology_data:
                    patient_details["ophthalmology"] = ophthalmology_data
                if investigation_notes:
                    patient_details["investigation_notes"] = investigation_notes
                if franchise_overall_approval:
                    final_assessment = {}
                    impression = franchise_overall_approval.get("impression", "")
                    if impression and impression.strip():
                        final_assessment["impression"] = impression
                    remarks = franchise_overall_approval.get("remarks", "")
                    if remarks and remarks.strip():
                        final_assessment["remarks"] = remarks
                    approved_date = franchise_overall_approval.get("approved_date", "")
                    if approved_date:
                        final_assessment["approved_date"] = approved_date
                    if final_assessment:
                        patient_details["final_assessment"] = final_assessment

                all_approvers = set()

                if test_values.exists():
                    for test_value in test_values:
                        try:
                            testvalue_details = (
                                json.loads(test_value.testdetails)
                                if isinstance(test_value.testdetails, str)
                                else test_value.testdetails
                            )
                            if not isinstance(testvalue_details, list):
                                continue

                            for test_detail in testvalue_details:
                                if test_detail.get("approve") is not True:
                                    continue

                                test_id   = test_detail.get("test_id")
                                testname  = test_detail.get("testname")
                                device_id = test_detail.get("device_id", "N/A")
                                if not test_id:
                                    continue

                                core_test = core_testdetails_collection.find_one({"test_id": test_id})

                                if core_test:
                                    testname      = core_test.get("test_name", testname)
                                    specimen_type = core_test.get("specimen_type", "N/A")
                                    department    = core_test.get("department", test_detail.get("department", ""))
                                    NABL    = core_test.get("NABL", test_detail.get("NABL", ""))
                                else:
                                    specimen_type = test_detail.get("specimen_type", "")
                                    department    = test_detail.get("department", "")
                                    NABL    = test_detail.get("NABL", "")

                                if not testname:
                                    continue

                                approve_by = test_detail.get("approve_by", "")
                                if approve_by:
                                    all_approvers.add(approve_by)

                                sample_status = None
                                for sample_test in sample_testdetails:
                                    if sample_test.get("test_id") == test_id or sample_test.get("testname") == testname:
                                        sample_status = sample_test
                                        break

                                test_response = {"testname": testname}
                                if department:
                                    test_response["department"] = department
                                if NABL:
                                    test_response["NABL"] = NABL
                                if test_detail.get("verified_by"):
                                    test_response["verified_by"] = test_detail.get("verified_by")
                                if test_detail.get("approve_by"):
                                    test_response["approve_by"] = test_detail.get("approve_by")
                                if test_detail.get("approve_time"):
                                    test_response["approve_time"] = test_detail.get("approve_time")
                                if test_detail.get("status"):
                                    test_response["status"] = test_detail.get("status")
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

                                if test_detail.get("parameters"):
                                    # ── Parameterised test ────────────────────
                                    processed_parameters = []
                                    for param_index, param in enumerate(test_detail.get("parameters", [])):
                                        test_code     = param.get("test_code")
                                        value         = param.get("value", "")
                                        param_comment = param.get("comment", "")

                                        param_def = get_parameter_from_core(
                                            core_test, device_id,
                                            test_code=test_code,
                                            param_index=param_index,
                                        ) if core_test else None

                                        processed_param = {}
                                        if param_def:
                                            # Gender-resolved reference_range
                                            ref_range = resolve_reference_range(param_def, patient_gender)

                                            processed_param["name"]            = param_def.get("test_name", param.get("name", ""))
                                            processed_param["value"]           = value
                                            processed_param["unit"]            = param_def.get("unit", "")
                                            processed_param["specimen_type"]   = specimen_type
                                            processed_param["reference_range"] = ref_range   # gender-resolved
                                            processed_param["method"]          = param_def.get("method", "")
                                            if param_def.get("sub_title"):
                                                processed_param["sub_title"] = param_def.get("sub_title")
                                            if param_comment:
                                                processed_param["comment"] = param_comment
                                        else:
                                            # Fallback: no core definition found
                                            if param.get("name"):
                                                processed_param["name"]            = param.get("name")
                                            if value:
                                                processed_param["value"]           = value
                                            if param.get("unit"):
                                                processed_param["unit"]            = param.get("unit")
                                            if param.get("specimen_type"):
                                                processed_param["specimen_type"]   = param.get("specimen_type")
                                            if param.get("reference_range"):
                                                processed_param["reference_range"] = param.get("reference_range")
                                            if param.get("method"):
                                                processed_param["method"]          = param.get("method")
                                            if param.get("sub_title"):
                                                processed_param["sub_title"]       = param.get("sub_title")
                                            if param_comment:
                                                processed_param["comment"]         = param_comment

                                        if processed_param:
                                            processed_parameters.append(processed_param)

                                    if processed_parameters:
                                        test_response["parameters"] = processed_parameters

                                else:
                                    # ── Single-value test ─────────────────────
                                    if core_test:
                                        # Gender-resolved reference_range
                                        ref_range = resolve_reference_range(core_test, patient_gender)

                                        if core_test.get("method"):
                                            test_response["method"]          = core_test.get("method")
                                        if specimen_type:
                                            test_response["specimen_type"]   = specimen_type
                                        if test_detail.get("value"):
                                            test_response["value"]           = test_detail.get("value")
                                        if core_test.get("unit"):
                                            test_response["unit"]            = core_test.get("unit")
                                        if ref_range:
                                            test_response["reference_range"] = ref_range   # gender-resolved
                                        if test_detail.get("sub_title"):
                                            test_response["sub_title"]       = test_detail.get("sub_title")
                                    else:
                                        # No core_test — use raw values from test_detail
                                        if test_detail.get("method"):
                                            test_response["method"]          = test_detail.get("method")
                                        if test_detail.get("specimen_type"):
                                            test_response["specimen_type"]   = test_detail.get("specimen_type")
                                        if test_detail.get("value"):
                                            test_response["value"]           = test_detail.get("value")
                                        if test_detail.get("unit"):
                                            test_response["unit"]            = test_detail.get("unit")
                                        if test_detail.get("reference_range"):
                                            test_response["reference_range"] = test_detail.get("reference_range")
                                        if test_detail.get("sub_title"):
                                            test_response["sub_title"]       = test_detail.get("sub_title")

                                patient_details["testdetails"].append(test_response)

                        except (json.JSONDecodeError, AttributeError) as e:
                            print(f"Error processing test: {str(e)}")
                            continue

                signatures_data = []
                for approver_id in all_approvers:
                    sig_data = get_employee_signature_data(approver_id)
                    if sig_data:
                        signatures_data.append(sig_data)

                results[barcode] = {
                    "patient_data": patient_details,
                    "signatures":   signatures_data,
                }

            except Exception as e:
                print(f"Error processing barcode {barcode}: {str(e)}")
                print(traceback.format_exc())
                results[barcode] = {'error': str(e)}

        client.close()

        return JsonResponse({
            'success':   True,
            'results':   results,
            'total':     len(barcodes),
            'processed': len(results),
        })

    except Exception as e:
        print(f"Batch processing error: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def corporate_credit_billing(request):
    """
    Get all corporate billing records where paymentMode is 'Credit'
    and map the company_id to company_name from core_company.
    """
    if request.method == "GET":
        client = None
        try:
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Corporatehealthcheckup
            
            billing_collection = db.core_billing
            company_collection = db.core_company
            
            # 1. Fetch all companies and create a map of company_id -> company_name
            companies = list(company_collection.find({}, {"_id": 0, "company_id": 1, "company_name": 1}))
            company_map = {str(comp.get("company_id", "")).strip(): comp.get("company_name", "") for comp in companies if comp.get("company_id")}
            
            # 2. Fetch billing records where paymentMode is Credit
            query = {"paymentMode": "Credit"}
            
            req_company_id = request.GET.get('company_id')
            if req_company_id:
                query['company_id'] = req_company_id
                
            from_date = request.GET.get('from_date')
            to_date = request.GET.get('to_date')
            
            if from_date or to_date:
                date_query = {}
                if from_date:
                    from_datetime = datetime.strptime(from_date, '%Y-%m-%d')
                    from_datetime = from_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
                    date_query['$gte'] = from_datetime
                if to_date:
                    to_datetime = datetime.strptime(to_date, '%Y-%m-%d')
                    to_datetime = to_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
                    date_query['$lte'] = to_datetime
                
                if date_query:
                    query['date'] = date_query
            
            # Fetch all invoiced bill IDs to exclude them
            invoice_collection = db.core_corporate_invoices
            invoiced_bill_ids = []
            all_invoices = list(invoice_collection.find({}, {"bill_items.bill_id": 1}))
            for inv in all_invoices:
                if "bill_items" in inv:
                    for item in inv["bill_items"]:
                        if "bill_id" in item:
                            invoiced_bill_ids.append(item["bill_id"])
            
            if invoiced_bill_ids:
                from bson import ObjectId
                # Convert string IDs to ObjectIds if they are stored as such in core_billing
                oid_list = []
                for bid in invoiced_bill_ids:
                    try:
                        oid_list.append(ObjectId(bid))
                    except:
                        pass
                query["_id"] = {"$nin": oid_list}

            billing_records = list(billing_collection.find(query).sort("date", -1))
            
            # 3. Format the response
            processed_data = []
            for bill in billing_records:
                bill['_id'] = str(bill['_id'])
                
                # Get the company name from the map
                company_id = str(bill.get('company_id', '')).strip()
                bill['company_name'] = company_map.get(company_id, "Unknown Company")
                
                # Format dates
                if 'created_date' in bill and bill['created_date']:
                    bill['created_date'] = bill['created_date'].isoformat() if hasattr(bill['created_date'], 'isoformat') else str(bill['created_date'])
                if 'date' in bill and bill['date']:
                    bill['date'] = bill['date'].isoformat() if hasattr(bill['date'], 'isoformat') else str(bill['date'])
                
                # Handle test details JSON strings
                if 'testdetails' in bill and isinstance(bill['testdetails'], str):
                    try:
                        bill['testdetails'] = json.loads(bill['testdetails'])
                    except json.JSONDecodeError:
                        bill['testdetails'] = []
                        
                if 'chctestdetails' in bill and isinstance(bill['chctestdetails'], str):
                    try:
                        bill['chctestdetails'] = json.loads(bill['chctestdetails'])
                    except json.JSONDecodeError:
                        bill['chctestdetails'] = []
                
                # Handle Decimal values (like netAmount)
                if 'netAmount' in bill:
                    bill['netAmount'] = float(str(bill['netAmount'])) if bill['netAmount'] else 0.0
                    
                processed_data.append(bill)
            
            return JsonResponse({
                "status": "success",
                "data": processed_data,
                "companies": companies,
                "count": len(processed_data)
            }, safe=False)
            
        except Exception as e:
            logger.error(f"Error fetching corporate credit billing: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": f"Database error: {str(e)}"
            }, status=500)
            
        finally:
            if client:
                client.close()



@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def generate_corporate_invoice(request):
    """
    Generate and save a corporate invoice.
    """
    client = None
    try:
        data = request.data
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        invoice_collection = db.core_corporate_invoices
        
        # Generate invoice number
        today = datetime.now().strftime("%Y%m%d")
        last_invoice = invoice_collection.find_one(sort=[("invoice_number", -1)])
        
        sequence = 1
        if last_invoice and last_invoice.get("invoice_number", "").startswith(f"CINV{today}"):
            try:
                sequence = int(last_invoice["invoice_number"][-3:]) + 1
            except:
                sequence = 1
        
        invoice_number = f"CINV{today}{sequence:03d}"
        
        total_amount = float(data.get("total_amount", 0))
        invoice_data = {
            "invoice_number": invoice_number,
            "company_id": data.get("company_id"),
            "company_name": data.get("company_name"),
            "from_date": data.get("from_date"),
            "to_date": data.get("to_date"),
            "total_amount": total_amount,
            "paid_amount": 0.0,
            "remaining_amount": total_amount,
            "bill_items": data.get("bill_items"),
            "payment_method": data.get("payment_method"),
            "payment_history": [],
            "status": "Generated",
            "created_at": datetime.now().isoformat(),
            "created_by": data.get("auth-user-id") or "system"
        }
        
        result = invoice_collection.insert_one(invoice_data)

        # Update original billing records to 'Paid' so they don't appear in the pending list
        billing_collection = db.core_billing
        from bson import ObjectId
        for item in invoice_data["bill_items"]:
            bill_id = item.get("bill_id")
            if bill_id:
                try:
                    billing_collection.update_one(
                        {"_id": ObjectId(str(bill_id))},
                        {"$set": {
                            "paymentMode": "Paid",
                            "invoice_number": invoice_number,
                            "invoiced_at": datetime.now().isoformat()
                        }}
                    )
                except:
                    pass

        return JsonResponse({
            "status": "success",
            "message": "Invoice generated successfully",
            "invoice_number": invoice_number,
            "id": str(result.inserted_id)
        })
        
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        if client:
            client.close()

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_corporate_invoices(request):
    """
    Fetch all generated corporate invoices.
    """
    client = None
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        invoice_collection = db.core_corporate_invoices
        
        query = {}
        company_id = request.GET.get('company_id')
        if company_id:
            query['company_id'] = company_id
            
        invoices = list(invoice_collection.find(query, {"_id": 0}).sort("created_at", -1))
        
        return JsonResponse({
            "status": "success",
            "data": invoices
        })
        
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        if client:
            client.close()

@api_view(['POST', 'PUT'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def update_corporate_invoice(request):
    """
    Update an existing corporate invoice.
    """
    client = None
    try:
        data = request.data
        invoice_number = data.get("invoice_number")
        if not invoice_number:
            return JsonResponse({"status": "error", "message": "Invoice number is required"}, status=400)
            
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        invoice_collection = db.core_corporate_invoices
        
        existing_invoice = invoice_collection.find_one({"invoice_number": invoice_number})
        if not existing_invoice:
            return JsonResponse({"status": "error", "message": "Invoice not found"}, status=404)
            
        total_amount = float(data.get("total_amount", existing_invoice.get("total_amount", 0)))
        new_payment = float(data.get("new_payment", 0))
        
        current_paid = float(existing_invoice.get("paid_amount", 0))
        updated_paid = current_paid + new_payment
        remaining_amount = total_amount - updated_paid
        
        payment_history = existing_invoice.get("payment_history", [])
        if new_payment > 0:
            payment_history.append({
                "amount": new_payment,
                "date": data.get("payment_date", datetime.now().isoformat()),
                "method": data.get("payment_method", existing_invoice.get("payment_method")),
                "note": data.get("note", ""),
                "received_by": data.get("auth-user-id") or "system"
            })
            
        update_data = {
            "total_amount": total_amount,
            "paid_amount": updated_paid,
            "remaining_amount": remaining_amount,
            "payment_method": data.get("payment_method", existing_invoice.get("payment_method")),
            "payment_history": payment_history,
            "status": "Paid" if remaining_amount <= 0 else "Partially Paid",
            "last_modified_at": datetime.now().isoformat(),
            "last_modified_by": data.get("auth-user-id") or "system"
        }
        
        # Optional fields
        if "bill_items" in data:
            update_data["bill_items"] = data.get("bill_items")
            
        result = invoice_collection.update_one(
            {"invoice_number": invoice_number},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            return JsonResponse({"status": "error", "message": "Invoice not found"}, status=404)

        # If fully paid, update original billing records to reflect settlement
        if remaining_amount <= 0.01:
            billing_collection = db.core_billing
            bill_items = existing_invoice.get("bill_items", [])
            from bson import ObjectId
            for item in bill_items:
                bill_id = item.get("bill_id")
                if bill_id:
                    try:
                        billing_collection.update_one(
                            {"_id": ObjectId(str(bill_id))},
                            {"$set": {
                                "paymentMode": "Paid",
                                "settled_via_invoice": invoice_number,
                                "settled_at": datetime.now().isoformat()
                            }}
                        )
                        billing_collection.update_one(
                            {"_id": str(bill_id)},
                            {"$set": {
                                "paymentMode": "Paid",
                                "settled_via_invoice": invoice_number,
                                "settled_at": datetime.now().isoformat()
                            }}
                        )
                    except:
                        pass
            
        return JsonResponse({
            "status": "success",
            "message": "Invoice updated successfully"
        })
        
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        if client:
            client.close()

@api_view(['POST', 'DELETE'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def delete_corporate_invoice(request):
    """
    Delete a corporate invoice.
    """
    client = None
    try:
        data = request.data
        invoice_number = data.get("invoice_number")
        if not invoice_number:
            return JsonResponse({"status": "error", "message": "Invoice number is required"}, status=400)
            
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        invoice_collection = db.core_corporate_invoices
        
        result = invoice_collection.delete_one({"invoice_number": invoice_number})
        
        if result.deleted_count == 0:
            return JsonResponse({"status": "error", "message": "Invoice not found"}, status=404)
            
        return JsonResponse({
            "status": "success",
            "message": "Invoice deleted successfully"
        })
        
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        if client:
            client.close()

@api_view(['GET'])
def export_corporate_invoice_pdf(request):
    """
    Generate a PDF for a corporate invoice.
    """
    client = None
    try:
        invoice_number = request.GET.get('invoice_number')
        if not invoice_number:
            return JsonResponse({"status": "error", "message": "Invoice number is required"}, status=400)
            
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        invoice = db.core_corporate_invoices.find_one({"invoice_number": invoice_number})
        
        if not invoice:
            return JsonResponse({"status": "error", "message": "Invoice not found"}, status=404)
            
        # For a professional PDF, we'd typically use reportlab or a template.
        # For now, let's provide a structured JSON or HTML that can be printed,
        # OR implement a basic PDF if the user has the libraries.
        # Let's assume we want a real PDF. I'll use reportlab if available.
        
        from django.http import HttpResponse
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        
        # Define paths to header/footer images from frontend assets
        frontend_img_dir = "/Users/parthibanmurugan/Desktop/Live Projects/LIS/shanmuga_diagnostics_frontend/src/Components/Images"
        header_path = os.path.join(frontend_img_dir, "Header.png")
        footer_path = os.path.join(frontend_img_dir, "Footer.png")
        
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="Invoice_{invoice_number}.pdf"'
        
        # Use a custom PageTemplate or draw on Canvas to handle header/footer on every page
        def add_header_footer(canvas, doc):
            canvas.saveState()
            # Draw Header
            if os.path.exists(header_path):
                canvas.drawImage(header_path, 0, A4[1]-100, width=A4[0], height=100, preserveAspectRatio=True, mask='auto')
            
            # Draw Footer
            if os.path.exists(footer_path):
                canvas.drawImage(footer_path, 0, 0, width=A4[0], height=60, preserveAspectRatio=True, mask='auto')
            
            # Page Number
            canvas.setFont('Helvetica', 8)
            canvas.drawRightString(A4[0]-40, 20, f"Page {doc.page}")
            canvas.restoreState()

        doc = SimpleDocTemplate(response, pagesize=A4, topMargin=110, bottomMargin=70)
        styles = getSampleStyleSheet()
        elements = []
        
        # Header / Title
        title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], alignment=1, fontSize=16, spaceAfter=20)
        elements.append(Paragraph("CORPORATE HEALTH CHECKUP INVOICE", title_style))
        
        # Info Table
        info_data = [
            [f"Invoice No: {invoice_number}", f"Date: {invoice.get('created_at', '')[:10]}"],
            [f"Company: {invoice.get('company_name')}", f"Period: {invoice.get('from_date')} to {invoice.get('to_date')}"],
            [f"Payment Method: {invoice.get('payment_method')}", f"Status: {invoice.get('status')}"]
        ]
        info_table = Table(info_data, colWidths=[250, 250])
        info_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 20))
        
        # Patient Details Table
        p_header = ["S.No", "Date", "Employee ID", "Barcode", "Amount (₹)"]
        p_data = [p_header]
        
        for idx, item in enumerate(invoice.get('bill_items', []), 1):
            p_data.append([
                str(idx),
                item.get('date', '')[:10],
                item.get('employee_id'),
                item.get('barcode'),
                f"{float(item.get('amount', 0)):.2f}"
            ])
            
        p_table = Table(p_data, colWidths=[40, 100, 120, 120, 100])
        p_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('ALIGN', (-1,0), (-1,-1), 'RIGHT'),
        ]))
        elements.append(p_table)
        elements.append(Spacer(1, 20))
        
        # Summary
        summary_data = [
            ["", "Total Amount:", f"INR {float(invoice.get('total_amount', 0)):.2f}"],
            ["", "Paid Amount:", f"INR {float(invoice.get('paid_amount', 0)):.2f}"],
            ["", "Remaining Pending:", f"INR {float(invoice.get('remaining_amount', 0)):.2f}"]
        ]
        summary_table = Table(summary_data, colWidths=[280, 100, 100])
        summary_table.setStyle(TableStyle([
            ('FONTNAME', (1,0), (-1,-1), 'Helvetica-Bold'),
            ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
            ('FONTSIZE', (1,0), (-1,-1), 10),
            ('TEXTCOLOR', (1,2), (-1,2), colors.red if float(invoice.get('remaining_amount', 0)) > 0 else colors.green),
        ]))
        elements.append(summary_table)
        
        # Build document with header/footer
        doc.build(elements, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
        return response
        
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        if client:
            client.close()
