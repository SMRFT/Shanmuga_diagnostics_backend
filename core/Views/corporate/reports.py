from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
import json
from core.mongo_client import get_client
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from core.models import TestValue
from bson import json_util
import gridfs
import base64
from bson.objectid import ObjectId
import re
from datetime import timezone, timedelta
from core.pagination import paginate_queryset

from .common import (
    _build_chc_status_from_investigation,
    _chc_approval_status,
    get_department_status_corporate,
    _compute_corporate_approval_status,
)

load_dotenv()
logger = logging.getLogger(__name__)


@api_view(['GET', 'PATCH'])
@csrf_exempt
# @permission_classes([HasRoleAndDataPermission])
def corporate_overall_report(request):
    try:
        client = get_client()
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


        if not sample_records:
            return JsonResponse([], safe=False)

        # ── STEP 2: Extract barcodes from sample records ───────────────────────
        barcodes = [r.get("barcode") for r in sample_records if r.get("barcode")]
        barcodes = list(set(barcodes))  # deduplicate

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
                "mobile": patient_detail.get("mobile", "N/A"),
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

        # Note: `client` is the shared, pooled MongoClient — do not close it here.

        # NOTE: response shape changed from a bare JSON array to a
        # paginated object ({"data": [...], total_count, total_pages,
        # current_page}) to bound the payload for wide date ranges;
        # update any frontend caller that expected a raw array here.
        total_count = len(formatted_data)
        page_obj, page_meta = paginate_queryset(formatted_data, request)
        return JsonResponse({
            "data": list(page_obj),
            "total_count": total_count,
            **page_meta
        }, safe=False)

    except Exception as e:
        logger.error("Critical Error: %s", str(e))
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)


@api_view(['GET'])
# @permission_classes([HasRoleAndDataPermission])
def corporate_patient_test_details(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)

    try:
        client = get_client()
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
                        logger.error(f"Error fetching signature for employee {emp_id}: {str(e)}")
                return {
                    "employeeName":    employee_name,
                    "designation":     designation,
                    "signatureBase64": signature_base64,
                }
            except Exception as e:
                logger.error(f"Error fetching employee data for {emp_id}: {str(e)}")
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
                        "notes":                core_test.get("notes", "") if core_test else "",
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
                                    "notes":           param_def.get("notes", "") or "",
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
                                    "notes":           "",
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

        # Note: `client` is the shared, pooled MongoClient — do not close it here.

        if not patient_details["testdetails"]:
            return JsonResponse({'error': 'No approved test records found'}, status=404)

        response_data = {"patient_data": patient_details, "signatures": signatures_data}
        return JsonResponse(response_data, safe=False)

    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)


@api_view(['GET', 'PATCH'])
@csrf_exempt
def corporate_approval_report(request):
    try:
        client = get_client()
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

        if not sample_records:
            return JsonResponse([], safe=False)

        # ── STEP 2: Extract barcodes from sample records ──────────────────────
        barcodes = list(set(r.get("barcode") for r in sample_records if r.get("barcode")))

        # Build sample date map, testdetails map, and valid test count map by barcode
        sample_date_by_barcode = {}
        sample_testdetails_by_barcode = {}
        valid_test_count_by_barcode = {}

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
            invalid_status = {"Rejected", "Outsource", "Pending"}
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

        if not billing_records:
            return JsonResponse([], safe=False)

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

        # Collect all company_ids
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

        # ── STEP 5: Overall approval map ──────────────────────────────────────
        approval_status_map = {}
        approval_records = list(overall_approval_collection.find({"barcode": {"$in": barcodes}}))

        approval_creator_ids = list(set(
            r.get("created_by") for r in approval_records if r.get("created_by")
        ))

        global_db = client.Global
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
        approved_count_by_barcode = {}

        for record in TestValue.objects.filter(barcode__in=barcodes):
            bc = str(record.barcode)
            td = record.testdetails
            parsed = json.loads(td) if isinstance(td, str) else (td or [])
            if isinstance(parsed, list):
                lab_tests_by_barcode.setdefault(bc, []).extend(parsed)
                approved = sum(1 for t in parsed if isinstance(t, dict) and t.get("approve"))
                approved_count_by_barcode[bc] = approved_count_by_barcode.get(bc, 0) + approved

        # ── STEP 8: Build response ─────────────────────────────────────────────
        formatted_data = []

        for bc in barcodes:
            billing = barcode_to_billing.get(bc)
            if not billing:
                continue

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

            # ── Get investigation_status early so per-test logic can use it ──
            investigation_status = investigation_status_by_barcode.get(str(bc), "")
            vitals = vitals_by_barcode.get(str(bc), {})

            chc_tests_with_status = []
            for ct in chc_tests:
                tid = str(ct.get("test_id", ""))
                info = chc_status_map_for_barcode.get(tid, {})

                inv_status = info.get("inv_status", "")
                has_content = info.get("has_report") or info.get("has_file")

                # ── FIX 1: If overall investigation is approved, all tests
                #           are approved regardless of file/report presence ──
                if investigation_status == "approved":
                    chc_status = "Approved"
                elif inv_status == "approved" and has_content:
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
                    "status": chc_status,
                })

            chc_overall, chc_pending, chc_approved = _chc_approval_status(
                chc_tests, chc_status_map_for_barcode
            )

            raw_lab_tests = lab_tests_by_barcode.get(str(bc), [])

            # Compute lab_approval per barcode
            total_sample = valid_test_count_by_barcode.get(str(bc), 0)
            approved_count = approved_count_by_barcode.get(str(bc), 0)
            lab_approval = (
                "Approved" if total_sample > 0 and approved_count >= total_sample
                else "Pending"
            )

            # ── FIX 2: If core_investigation.status == "approved" AND lab is
            #           approved → treat as All Approved without checking
            #           per-test file/report presence ──
            if investigation_status == "approved" and lab_approval == "Approved":
                combined_investigation_status = "All Approved"
            elif lab_approval == "Pending" or chc_overall != "All Approved":
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
                "chc_investigation_status": combined_investigation_status,
                "chc_pending_tests": chc_pending,
                "chc_approved_tests": chc_approved,
                "approval_type": approval_type,
                "approved_by_name": approved_by_name,
            })

        # Note: `client` is the shared, pooled MongoClient — do not close it here.

        # NOTE: response shape changed from a bare JSON array to a
        # paginated object ({"data": [...], total_count, total_pages,
        # current_page}) to bound the payload when no/wide date filters
        # are supplied; update any frontend caller that expected a raw
        # array here.
        total_count = len(formatted_data)
        page_obj, page_meta = paginate_queryset(formatted_data, request)
        return JsonResponse({
            "data": list(page_obj),
            "total_count": total_count,
            **page_meta
        }, safe=False)

    except Exception as e:
        logger.error("Critical Error: %s", str(e))
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)


@api_view(['GET'])
# @permission_classes([HasRoleAndDataPermission])
def corporate_health_report(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)

    try:
        client = get_client()
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
                        logger.error(f"Error fetching signature for employee {emp_id}: {str(e)}")
                return {"employeeName": employee_name, "designation": designation, "signatureBase64": signature_base64}
            except Exception as e:
                logger.error(f"Error fetching employee data for {emp_id}: {str(e)}")
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
                        notes = test_detail.get("notes", "")
                        if not notes and core_test:
                            notes = core_test.get("notes", "")
                        if notes:
                            test_response["notes"] = notes
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
                                    param_notes = param.get("notes", "")
                                    if not param_notes and param_def:
                                        param_notes = param_def.get("notes", "")
                                    if param_notes:
                                        processed_param["notes"] = param_notes
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
                                    if param.get("notes"):
                                        processed_param["notes"]           = param.get("notes")
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
                    logger.error(f"Error processing test: {str(e)}")
                    continue

        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)

        # Note: `client` is the shared, pooled MongoClient — do not close it here.
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
        client = get_client()
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

        # Note: `client` is the shared, pooled MongoClient — do not close it here.
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
        client = get_client()
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

                invalid_status = {"Rejected", "Outsource", "Pending"}

                valid_tests = [
                    t for t in sample_tests
                    if t.get("samplestatus", "").strip() not in invalid_status
                ]

                total_sample_tests = len(valid_tests)

        except Exception as e:
            logger.error(f"Error calculating total_sample_tests: {e}")

        approved_tests_count = 0

        try:
            for record in TestValue.objects.filter(barcode=barcode):

                tests_list = parse_json(record.testdetails, [])

                for t in tests_list:
                    if isinstance(t, dict) and t.get("approve"):
                        approved_tests_count += 1

        except Exception as e:
            logger.error(f"Error calculating approved_tests_count: {e}")

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

        # Note: `client` is the shared, pooled MongoClient — do not close it here.

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
