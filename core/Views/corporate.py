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
    chc_status_by_barcode = {}

    for inv in investigation_records:
        barcode = str(inv.get("barcode", ""))
        if not barcode:
            continue

        chc_status_by_barcode.setdefault(barcode, {})

        # ── Normal CHC tests from test_results ─────────────────
        test_results = inv.get("test_results", [])
        if isinstance(test_results, str):
            try:
                test_results = json.loads(test_results)
            except Exception:
                test_results = []

        for tr in test_results:
            tid = str(tr.get("test_id", ""))
            if not tid:
                continue

            files = tr.get("files", [])
            report = tr.get("report", "") or ""
            notes = tr.get("notes", "") or ""

            chc_status_by_barcode[barcode][tid] = {
                "test_name": tr.get("test_name", ""),
                "report": report,
                "notes": notes,
                "files": files,
                "has_report": bool(report.strip()),
                "has_file": bool(files),
            }

        # ── NEW: Ophthalmology (CHCT001) directly from investigation ─────
        oph_data = inv.get("CHCT001")

        if isinstance(oph_data, dict) and oph_data:
            has_data = any(
                v for v in oph_data.values()
                if v and (not isinstance(v, dict) or any(v.values()))
            )

            if has_data:
                chc_status_by_barcode[barcode]["CHCT001"] = {
                    "test_name": "Ophthalmology",
                    "report": "Available",
                    "notes": oph_data.get("remarks", ""),
                    "files": [],
                    "has_report": True,
                    "has_file": False,
                }

    return chc_status_by_barcode


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
                    barcode_key = str(barcode).strip()
                    employee_id_value = str(employee_id).strip()
                    billing_lookup[barcode_key] = employee_id_value

            logger.info(f"Sample billing lookup entries: {dict(list(billing_lookup.items())[:5])}")

            # Step 2: employee_id -> employee_data from core_employeeregistration
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
                    batch['created_date'] = batch['created_date'].isoformat() if hasattr(batch['created_date'], 'isoformat') else str(batch['created_date'])
                if 'lastmodified_date' in batch and batch['lastmodified_date']:
                    batch['lastmodified_date'] = batch['lastmodified_date'].isoformat() if hasattr(batch['lastmodified_date'], 'isoformat') else str(batch['lastmodified_date'])
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
        sample_status_colletion = db.core_sample
        franchise_patient_collection = db.core_employeeregistration
        investigation_collection = db.core_investigation  # NEW

        diagnostics_db = client.Diagnostics
        test_details_collection = diagnostics_db.core_testdetails

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

        # Status data - fetch from core_sample using barcode
        sample_status_records = []
        if barcodes:
            sample_status_records = list(sample_status_colletion.find({"barcode": {"$in": barcodes}}))

        sample_status_list = []
        for record in sample_status_records:
            if record and isinstance(record, dict):
                barcode = record.get("barcode")
                testdetails = record.get("testdetails")
                if barcode and testdetails:
                    sample_status_list.append({"barcode": barcode, "testdetails": testdetails})

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

        # Organize sample status data using barcode mapping
        sample_status_map = {}
        for record in sample_status_list:
            if record and isinstance(record, dict):
                barcode = record.get("barcode")
                testdetails = record.get("testdetails")
                if barcode and barcode in barcode_to_patient_map and testdetails:
                    employee_id_key = barcode_to_patient_map[barcode]
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
            if not isinstance(record, dict):
                continue
            barcode = record.get("barcode")
            created_date = record.get("created_date")
            testdetails = record.get("testdetails")
            if not barcode or barcode not in barcode_to_patient_map:
                continue
            employee_id = barcode_to_patient_map[barcode]
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails.strip('"'))
                except json.JSONDecodeError:
                    testdetails = []
            if employee_id not in test_value_map:
                test_value_map[employee_id] = {"barcode": barcode, "testdetails": [], "created_date": created_date}
            if isinstance(testdetails, list):
                test_value_map[employee_id]["testdetails"].extend(testdetails)
            if created_date and test_value_map[employee_id]["created_date"]:
                if created_date > test_value_map[employee_id]["created_date"]:
                    test_value_map[employee_id]["created_date"] = created_date
            elif created_date:
                test_value_map[employee_id]["created_date"] = created_date

        print(f"Processed test value map with {len(test_value_map)} unique employee IDs")

        # ── NEW: Fetch investigation records and build CHC status map ────────
        investigation_records = []
        if barcodes:
            investigation_records = list(investigation_collection.find({"barcode": {"$in": barcodes}}))

        chc_status_by_barcode = _build_chc_status_from_investigation(investigation_records)

        # Final result
        formatted_data = []
        for patient in patients:
            if not isinstance(patient, dict):
                print(f"Warning: Patient record is not a dict: {type(patient)}")
                continue

            pid = patient.get("employee_id", "N/A")
            patient_detail = patient_details_map.get(pid, {})
            if not isinstance(patient_detail, dict):
                patient_detail = {}

            sample_tests = sample_status_map.get(pid, [])

            test_list = []
            test_ids = []
            departments_set = set()

            if isinstance(sample_tests, list):
                test_ids = [test.get("test_id") for test in sample_tests if isinstance(test, dict) and test.get("test_id")]

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
                test.get("testname", test.get("test_name", "")) if isinstance(test, dict) else str(test)
                for test in test_list
            ])
            no_of_tests = len(test_list)
            department = ", ".join(sorted(departments_set)) if departments_set else "N/A"

            age_value = patient_detail.get("age", "N/A")
            age_type = patient_detail.get("age_type", "")
            age = f"{age_value} {age_type}" if age_type else str(age_value)

            barcode = patient.get("barcode")

            # ── NEW: Parse chctestdetails from billing ───────────────────────
            chc_raw = patient.get("chctestdetails", "[]")
            chc_tests = []
            if isinstance(chc_raw, str):
                try:
                    chc_tests = json.loads(chc_raw)
                except (json.JSONDecodeError, TypeError):
                    chc_tests = []
            elif isinstance(chc_raw, list):
                chc_tests = chc_raw

            # ── NEW: Get per-test CHC status for this barcode ────────────────
            chc_status_map_for_barcode = chc_status_by_barcode.get(str(barcode), {})

            chc_tests_with_status = []
            for ct in chc_tests:
                tid = str(ct.get("test_id", ""))
                info = chc_status_map_for_barcode.get(tid, {})
                chc_tests_with_status.append({
                    "test_id": tid,
                    "testname": ct.get("testname", ""),
                    "has_report": info.get("has_report", False),
                    "has_file": info.get("has_file", False),
                    "report": info.get("report", ""),
                    "notes": info.get("notes", ""),
                    "files": info.get("files", []),
                    "status": "Approved" if (info.get("has_report") or info.get("has_file")) else "Pending",
                })

            chc_overall, chc_pending, chc_approved = _chc_approval_status(
                chc_tests, chc_status_map_for_barcode
            )

            latest_test_data = test_value_map.get(pid, {})
            all_test_values = latest_test_data.get("testdetails", [])
            test_created_date = latest_test_data.get("created_date", None)

            if not barcode and latest_test_data.get("barcode"):
                barcode = latest_test_data.get("barcode")

            valid_test_values = []
            unapproved_tests = []
            if all_test_values:
                for test_record in all_test_values:
                    if not test_record.get("rerun", False):
                        valid_test_values.append(test_record)
                        if not test_record.get("approve", False):
                            unapproved_tests.append(test_record)
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

            individual_test_statuses = []
            if pid and test_list:
                for test in test_list:
                    test_id = test.get('test_id')
                    test_name = test.get('testname') or test.get('test_name', 'N/A')
                    sample_info = next((t for t in sample_tests if t.get('test_id') == test_id), {})
                    test_value_info = next((t for t in valid_test_values if t.get('test_id') == test_id), {})
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
                        if sample_info.get('samplestatus') == 'Outsourced':
                            test_status = "Outsourced"
                    if test_value_info:
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

            if valid_test_values:
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

                all_ordered_test_ids = {
                    str(test.get("test_id", "")).strip()
                    for test in test_list
                    if isinstance(test, dict) and test.get("test_id")
                }
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

                all_approved = False
                partially_approved = False
                if len(all_ordered_test_ids) > 0:
                    if all_ordered_test_ids.issubset(approved_test_ids) and len(approved_test_ids) == len(all_ordered_test_ids):
                        all_approved = True
                    elif len(approved_test_ids) > 0:
                        partially_approved = True
                    if not all_approved and valid_test_values:
                        approved_count = sum(1 for t in valid_test_values if t.get("approve", False))
                        total_expected = no_of_tests
                        if approved_count == total_expected and approved_count > 0:
                            all_approved = True
                            partially_approved = False
                        elif approved_count > 0:
                            partially_approved = True

                all_dispatched = False
                partially_dispatched = False
                if len(all_ordered_test_ids) > 0:
                    if all_ordered_test_ids.issubset(dispatch_test_ids) and len(dispatch_test_ids) == len(all_ordered_test_ids):
                        all_dispatched = True
                    elif len(dispatch_test_ids) > 0:
                        partially_dispatched = True
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

            department_statuses = {}
            if pid and test_list:
                department_statuses = get_department_status_corporate(
                    test_list, pid, sample_status_map, test_value_map
                )

            created_date = patient.get("created_date")
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
                if isinstance(test_created_date, datetime):
                    test_created_date_formatted = test_created_date.isoformat()
                else:
                    test_created_date_formatted = str(test_created_date)

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
                "department": department,
                "department_statuses": department_statuses,
                "test_statuses": individual_test_statuses,
                "no_of_tests": no_of_tests,
                "barcode": barcode,
                "status": status,
                "test_created_date": test_created_date_formatted,
                "collection_time": collection_time_val,
                "collected_date": collected_date_val,
                # ── NEW CHC fields ─────────────────────────────────────────────
                "chc_tests": chc_tests_with_status,
                "chc_investigation_status": chc_overall,   # "All Approved" | "Partial" | "Pending" | "No CHC Tests"
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
        franchise_patient_collection = db.core_employeeregistration

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
        # core_employeeregistration has a 'gender' field
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
            "branch":      franchise_billing.get("franchise_id", ""),
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
                        "specimen_type":        specimen_type,
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
                            })
                        else:
                            test_response.update({
                                "method":          test_detail.get("method", ""),
                                "value":           test_detail.get("value", ""),
                                "unit":            test_detail.get("unit", ""),
                                "reference_range": test_detail.get("reference_range", ""),
                                "sub_title":       test_detail.get("sub_title", ""),
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


@api_view(['GET', 'PATCH'])
@csrf_exempt
# @permission_classes([HasRoleAndDataPermission])
def corporate_approval_report(request):
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup
        patients_collection = db.core_billing
        sample_status_colletion = db.core_sample
        franchise_patient_collection = db.core_employeeregistration
        overall_approval_collection = db.overallApproval
        investigation_collection = db.core_investigation  # NEW

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

        # Employee detail map
        patient_details_map = {}
        if employee_ids:
            patient_details = franchise_patient_collection.find({"employee_id": {"$in": employee_ids}})
            for patient_detail in patient_details:
                patient_details_map[patient_detail.get("employee_id")] = patient_detail

        # Overall approval map
        approval_status_map = {}
        if barcodes:
            approval_records = overall_approval_collection.find({"barcode": {"$in": barcodes}})
            for approval in approval_records:
                bc = approval.get("barcode")
                if bc:
                    approval_status_map[bc] = {
                        "status": approval.get("status", "approved"),
                        "approved_date": approval.get("approved_date"),
                        "impression": approval.get("impression"),
                        "remarks": approval.get("remarks")
                    }

        # Sample status records
        sample_status_records = []
        if barcodes:
            sample_status_records = list(sample_status_colletion.find({"barcode": {"$in": barcodes}}))

        sample_status_list = []
        for record in sample_status_records:
            if record and isinstance(record, dict):
                bc = record.get("barcode")
                testdetails = record.get("testdetails")
                if bc and testdetails:
                    sample_status_list.append({"barcode": bc, "testdetails": testdetails})

        # barcode → employee_id
        barcode_to_patient_map = {}
        for patient in patients:
            if patient.get("barcode") and patient.get("employee_id"):
                barcode_to_patient_map[patient.get("barcode")] = patient.get("employee_id")

        # Build sample_status_map with enriched test names
        sample_status_map = {}
        for record in sample_status_list:
            if record and isinstance(record, dict):
                bc = record.get("barcode")
                testdetails = record.get("testdetails")
                if bc and bc in barcode_to_patient_map and testdetails:
                    employee_id_key = barcode_to_patient_map[bc]
                    parsed_testdetails = []
                    if isinstance(testdetails, str):
                        try:
                            parsed_testdetails = json.loads(testdetails)
                        except json.JSONDecodeError:
                            parsed_testdetails = []
                    elif isinstance(testdetails, list):
                        parsed_testdetails = testdetails

                    enriched_testdetails = []
                    for test in parsed_testdetails:
                        if isinstance(test, dict):
                            test_id = test.get("test_id")
                            if test_id:
                                core_test = core_testdetails_collection.find_one({"test_id": test_id})
                                if core_test:
                                    test["testname"] = core_test.get("test_name", test.get("testname", ""))
                            enriched_testdetails.append(test)

                    if enriched_testdetails:
                        sample_status_map.setdefault(employee_id_key, []).extend(enriched_testdetails)

        # ── NEW: Fetch investigation records and build CHC status map ────────
        investigation_records = []
        if barcodes:
            investigation_records = list(investigation_collection.find({"barcode": {"$in": barcodes}}))

        chc_status_by_barcode = _build_chc_status_from_investigation(investigation_records)

        # Build formatted_data
        formatted_data = []
        for patient in patients:
            if not isinstance(patient, dict):
                print(f"Warning: Patient record is not a dict: {type(patient)}")
                continue

            pid = patient.get("employee_id", "N/A")
            bc = patient.get("barcode")
            patient_detail = patient_details_map.get(pid, {})
            if not isinstance(patient_detail, dict):
                patient_detail = {}

            sample_tests = sample_status_map.get(pid, [])
            testnames = ", ".join([
                test.get("testname", "") if isinstance(test, dict) else str(test)
                for test in sample_tests
            ])
            no_of_tests = len(sample_tests)

            age_value = patient_detail.get("age", "N/A")
            age_type = patient_detail.get("age_type", "")
            age = f"{age_value} {age_type}" if age_type else str(age_value)

            # ── NEW: Parse chctestdetails from billing ───────────────────────
            chc_raw = patient.get("chctestdetails", "[]")
            chc_tests = []
            if isinstance(chc_raw, str):
                try:
                    chc_tests = json.loads(chc_raw)
                except (json.JSONDecodeError, TypeError):
                    chc_tests = []
            elif isinstance(chc_raw, list):
                chc_tests = chc_raw

            # ── NEW: Get per-test CHC status for this barcode ────────────────
            chc_status_map_for_barcode = chc_status_by_barcode.get(str(bc), {})

            chc_tests_with_status = []
            for ct in chc_tests:
                tid = str(ct.get("test_id", ""))
                info = chc_status_map_for_barcode.get(tid, {})
                chc_tests_with_status.append({
                    "test_id": tid,
                    "testname": ct.get("testname", ""),
                    "has_report": info.get("has_report", False),
                    "has_file": info.get("has_file", False),
                    "report": info.get("report", ""),
                    "notes": info.get("notes", ""),
                    "files": info.get("files", []),
                    "status": "Approved" if (info.get("has_report") or info.get("has_file")) else "Pending",
                })

            chc_overall, chc_pending, chc_approved = _chc_approval_status(
                chc_tests, chc_status_map_for_barcode
            )

            # Overall approval status
            if bc and bc in approval_status_map:
                status = "Approved"
            else:
                status = "Pending"

            created_date = patient.get("created_date")
            if created_date:
                if isinstance(created_date, datetime):
                    formatted_date = created_date.strftime("%Y-%m-%d")
                else:
                    try:
                        parsed_date = datetime.strptime(str(created_date), "%Y-%m-%d")
                        formatted_date = parsed_date.strftime("%Y-%m-%d")
                    except Exception:
                        formatted_date = str(created_date)
            else:
                formatted_date = "N/A"

            formatted_data.append({
                "date": formatted_date,
                "patient_id": pid,
                "patient_name": patient_detail.get("employee_name", "N/A"),
                "gender": patient_detail.get("gender", "N/A"),
                "age": age,
                "email": patient_detail.get("email", "N/A"),
                "branch": patient_detail.get("company_id", "N/A"),
                "test_names": testnames,
                "no_of_tests": no_of_tests,
                "barcode": bc,
                "status": status,
                # ── NEW CHC fields ─────────────────────────────────────────────
                "chc_tests": chc_tests_with_status,
                "chc_investigation_status": chc_overall,   # "All Approved" | "Partial" | "Pending" | "No CHC Tests"
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
def corporate_health_report(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)

    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup

        franchise_billing_collection          = db.core_billing
        franchise_sample_collection           = db.core_sample
        franchise_patient_collection          = db.core_employeeregistration
        franchise_investigation_collection    = db.core_investigation
        franchise_overall_approval_collection = db.overallApproval
        franchise_company_collection          = db.core_company

        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

        # ── Parameter helper ──────────────────────────────────────────────────
        def get_parameter_from_core(core_test, device_id, test_code):
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
        # core_employeeregistration has a 'gender' field
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
                ophthalmology_data = {}
                distance = chc_ophthalmology.get("distance", {})
                if distance:
                    ophthalmology_data["distance"] = {"right": str(distance.get("right", "")), "left": str(distance.get("left", ""))}
                near_vision = chc_ophthalmology.get("nearVision", {})
                if near_vision:
                    ophthalmology_data["near_vision"] = {"right": str(near_vision.get("right", "")), "left": str(near_vision.get("left", ""))}
                colour_vision = chc_ophthalmology.get("colourVision", {})
                if colour_vision:
                    ophthalmology_data["color_vision"] = {"right": str(colour_vision.get("right", "")), "left": str(colour_vision.get("left", ""))}
                ocular_movement = chc_ophthalmology.get("ocularmovement", {})
                if ocular_movement:
                    ophthalmology_data["ocularmovement"] = {"right": str(ocular_movement.get("right", "")), "left": str(ocular_movement.get("left", ""))}
                complaints = chc_ophthalmology.get("complaints")
                if complaints:
                    ophthalmology_data["complaints"] = complaints
                remarks = chc_ophthalmology.get("remarks")
                if remarks:
                    ophthalmology_data["remarks"] = remarks
                if not ophthalmology_data:
                    ophthalmology_data = None

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
            "date":        franchise_billing.get("created_date"),
            "barcode":     barcode,
            "barcodes":    barcodes,
            "testdetails": [],
        }

        if company_data and company_data.get("company_name"):
            patient_details["company_name"] = company_data.get("company_name")
        if franchise_patient.get("department"):
            patient_details["department"] = franchise_patient.get("department")
        if franchise_patient.get("dob"):
            patient_details["dob"] = franchise_patient.get("dob")
        if vitals_data:
            patient_details["vitals"] = {}
            for key in ["height_cm", "weight_kg", "bmi", "blood_pressure", "pulse", "spo2"]:
                if vitals_data.get(key):
                    display_key = key.replace("_cm", "").replace("_kg", "")
                    patient_details["vitals"][display_key] = vitals_data.get(key)
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
                        else:
                            specimen_type = test_detail.get("specimen_type", "")
                            department    = test_detail.get("department", "")

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
                        if test_detail.get("verified_by"):
                            test_response["verified_by"] = test_detail.get("verified_by")
                        if test_detail.get("approve_by"):
                            test_response["approve_by"] = test_detail.get("approve_by")
                        if test_detail.get("approve_time"):
                            test_response["approve_time"] = test_detail.get("approve_time")
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
                            for param in test_detail.get("parameters", []):
                                test_code     = param.get("test_code")
                                value         = param.get("value", "")
                                param_comment = param.get("comment", "")

                                param_def = get_parameter_from_core(core_test, device_id, test_code) \
                                    if core_test and test_code else None

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
        all_chc_approved = (
            len(chc_tests_enriched) > 0 and
            all(ct["status"] == "approved" for ct in chc_tests_enriched)
        )

        chc_overall_status = "approved" if all_chc_approved else "pending"

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
    """
    barcodes = request.data.get('barcodes', [])
    if not barcodes or not isinstance(barcodes, list):
        return JsonResponse({'error': 'Barcodes array is required'}, status=400)

    barcodes = [str(bc) for bc in barcodes]
    print(f"Processing {len(barcodes)} barcodes: {barcodes[:5]}...")

    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Corporatehealthcheckup

        investigation_collection = db.core_investigation
        ophthalmology_collection = db.core_ophthalmology
        sample_collection = db.core_sample
        billing_collection = db.core_billing

        # Fetch all records in bulk
        investigations = list(investigation_collection.find({"barcode": {"$in": barcodes}}))
        ophthalmologies = list(ophthalmology_collection.find({"barcode": {"$in": barcodes}}))
        samples = list(sample_collection.find({"barcode": {"$in": barcodes}}))
        billings = list(billing_collection.find({"barcode": {"$in": barcodes}}))
        test_value_records = TestValue.objects.filter(barcode__in=barcodes)

        print(f"Found {len(investigations)} investigations, {len(ophthalmologies)} ophthalmology, "
              f"{len(samples)} samples, {len(billings)} billings, {len(test_value_records)} test values")

        # Build lookup maps
        investigation_map = {str(inv['barcode']): inv for inv in investigations}
        ophthalmology_map = {str(oph['barcode']): oph for oph in ophthalmologies}
        sample_map = {str(s['barcode']): s for s in samples}
        billing_map = {str(b['barcode']): b for b in billings}

        # Approved tests count per barcode
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
            ophthalmology = ophthalmology_map.get(barcode)
            sample = sample_map.get(barcode)
            billing = billing_map.get(barcode)

            # Lab approval
            total_sample_tests = 0
            if sample and sample.get('testdetails'):
                raw = sample.get('testdetails')
                sample_tests = json.loads(raw) if isinstance(raw, str) else (raw or [])
                total_sample_tests = len(sample_tests)
            approved_count = approved_tests_map.get(barcode, 0)
            lab_approval = "approved" if (total_sample_tests > 0 and approved_count >= total_sample_tests) else "pending"

            # Ophthalmology
            ophthalmology_status = ophthalmology.get("status", "pending") if ophthalmology else "pending"

            # NEW: Per-CHC-test status from investigation.test_results
            chc_test_status = {}
            if investigation:
                test_results = investigation.get("test_results", [])
                if isinstance(test_results, str):
                    try:
                        test_results = json.loads(test_results)
                    except (json.JSONDecodeError, TypeError):
                        test_results = []
                for tr in test_results:
                    tid = str(tr.get("test_id", ""))
                    if not tid:
                        continue
                    files = tr.get("files", [])
                    report = tr.get("report", "") or ""
                    notes = tr.get("notes", "") or ""
                    chc_test_status[tid] = {
                        "test_name": tr.get("test_name", ""),
                        "report": report,
                        "notes": notes,
                        "files": files,
                        "has_report": bool(report.strip()),
                        "has_file": bool(files),
                        "status": "approved" if (bool(report.strip()) or bool(files)) else "pending",
                    }

            # NEW: Get chctestdetails from billing
            chc_tests_list = []
            if billing:
                chc_raw = billing.get("chctestdetails", "[]")
                if isinstance(chc_raw, str):
                    try:
                        chc_tests_list = json.loads(chc_raw)
                    except (json.JSONDecodeError, TypeError):
                        chc_tests_list = []
                elif isinstance(chc_raw, list):
                    chc_tests_list = chc_raw

            chc_tests_enriched = []
            for ct in chc_tests_list:
                tid = str(ct.get("test_id", ""))
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

            all_chc_approved = (
                len(chc_tests_enriched) > 0
                and all(ct["status"] == "approved" for ct in chc_tests_enriched)
            )
            chc_overall_status = "approved" if all_chc_approved else "pending"

            results[barcode] = {
                # Legacy field — keep for backward-compat with existing frontend code
                'investigation': {
                    "xray_report": "pending",
                    "xrayfilm_file": "pending",
                    "ecg_file": "pending",
                    "pft_file": "pending",
                    "audiometric_file": "pending",
                },
                'ophthalmology': ophthalmology_status,
                'lab_approval': lab_approval,
                # NEW fields
                'chc_tests': chc_tests_enriched,
                'chc_investigation_status': chc_overall_status,
            }

        client.close()
        print(f"Returning results for {len(results)} barcodes")
        return JsonResponse({'success': True, 'results': results})

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
        franchise_patient_collection          = db.core_employeeregistration
        franchise_investigation_collection    = db.core_investigation
        franchise_overall_approval_collection = db.overallApproval
        franchise_company_collection          = db.core_company

        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

        # ── Parameter helper ──────────────────────────────────────────────────
        def get_parameter_from_core(core_test, device_id, test_code):
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
                        ophthalmology_data = {}
                        distance = chc_ophthalmology.get("distance", {})
                        if distance:
                            ophthalmology_data["distance"] = {"right": str(distance.get("right", "")), "left": str(distance.get("left", ""))}
                        near_vision = chc_ophthalmology.get("nearVision", {})
                        if near_vision:
                            ophthalmology_data["near_vision"] = {"right": str(near_vision.get("right", "")), "left": str(near_vision.get("left", ""))}
                        colour_vision = chc_ophthalmology.get("colourVision", {})
                        if colour_vision:
                            ophthalmology_data["color_vision"] = {"right": str(colour_vision.get("right", "")), "left": str(colour_vision.get("left", ""))}
                        ocular_movement = chc_ophthalmology.get("ocularmovement", {})
                        if ocular_movement:
                            ophthalmology_data["ocularmovement"] = {"right": str(ocular_movement.get("right", "")), "left": str(ocular_movement.get("left", ""))}
                        complaints = chc_ophthalmology.get("complaints")
                        if complaints:
                            ophthalmology_data["complaints"] = complaints
                        remarks = chc_ophthalmology.get("remarks")
                        if remarks:
                            ophthalmology_data["remarks"] = remarks
                        if not ophthalmology_data:
                            ophthalmology_data = None

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
                    "date":        franchise_billing.get("created_date"),
                    "barcode":     barcode,
                    "testdetails": [],
                }

                if company_data and company_data.get("company_name"):
                    patient_details["company_name"] = company_data.get("company_name")
                if franchise_patient.get("department"):
                    patient_details["department"] = franchise_patient.get("department")
                if franchise_patient.get("dob"):
                    patient_details["dob"] = franchise_patient.get("dob")
                if vitals_data:
                    patient_details["vitals"] = {}
                    for key in ["height_cm", "weight_kg", "bmi", "blood_pressure", "pulse", "spo2"]:
                        if vitals_data.get(key):
                            display_key = key.replace("_cm", "").replace("_kg", "")
                            patient_details["vitals"][display_key] = vitals_data.get(key)
                if medical_history_data:
                    patient_details["medical_history"] = medical_history_data
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
                                else:
                                    specimen_type = test_detail.get("specimen_type", "")
                                    department    = test_detail.get("department", "")

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
                                if test_detail.get("verified_by"):
                                    test_response["verified_by"] = test_detail.get("verified_by")
                                if test_detail.get("approve_by"):
                                    test_response["approve_by"] = test_detail.get("approve_by")
                                if test_detail.get("approve_time"):
                                    test_response["approve_time"] = test_detail.get("approve_time")
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
                                    for param in test_detail.get("parameters", []):
                                        test_code     = param.get("test_code")
                                        value         = param.get("value", "")
                                        param_comment = param.get("comment", "")

                                        param_def = get_parameter_from_core(core_test, device_id, test_code) \
                                            if core_test and test_code else None

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
    
    