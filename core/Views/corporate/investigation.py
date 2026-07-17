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


load_dotenv()
logger = logging.getLogger(__name__)


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
        client = get_client()
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
                invalid = {"Rejected", "Outsource", "Pending"}
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

        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        return JsonResponse({'success': True, 'results': results})

    except Exception as e:
        logger.error(traceback.format_exc())
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

        client = get_client()
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

        # Note: `client` is the shared, pooled MongoClient — do not close it here.

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
                            logger.error(f"Error processing test: {str(e)}")
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
                logger.error(f"Error processing barcode {barcode}: {str(e)}")
                logger.error(traceback.format_exc())
                results[barcode] = {'error': str(e)}

        # Note: `client` is the shared, pooled MongoClient — do not close it here.

        return JsonResponse({
            'success':   True,
            'results':   results,
            'total':     len(barcodes),
            'processed': len(results),
        })

    except Exception as e:
        logger.error(f"Batch processing error: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
