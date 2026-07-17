"""
Patient test sorting + patient test detail views.

Moved out of the original monolithic core/Views/report.py during the
report.py -> report/ package split. Contains:
  - patient_test_sorting
  - get_patient_test_details
"""
import logging
from rest_framework.response import Response
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework import status
from urllib.parse import quote_plus
from core.mongo_client import get_client
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta
import re
from django.core.mail import EmailMessage
from django.conf import settings
from django.utils.timezone import make_aware
from core.utils import get_employee_name
from pyauth.auth import HasRoleAndDataPermission
from core.models import Patient, SampleStatus, Billing, TestValue, MBTestValue, BarcodeTestDetails
import os, json, traceback
import pytz
from django.utils.dateparse import parse_datetime
import gridfs
from core.pagination import paginate_queryset
from dotenv import load_dotenv

load_dotenv()

# Define IST timezone
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)

logger = logging.getLogger(__name__)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def patient_test_sorting(request):
    try:
        # Get barcode from request
        barcode = request.GET.get('barcode')
        date = request.GET.get('date', datetime.now().strftime("%Y-%m-%d"))

        if not barcode:
            return JsonResponse({'error': 'Missing barcode'}, status=400)

        # Ensure the date is in YYYY-MM-DD format
        try:
            formatted_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

        # MongoDB connection
        client = get_client()
        db = client.Diagnostics
        core_testdetails_collection = db["core_testdetails"]

        # Filter test values by barcode and include created_date
        tests = TestValue.objects.filter(barcode=barcode, date=formatted_date).values("testdetails", "created_date")
        test_list = []

        for test in tests:
            test_created_date = test.get("created_date")

            testdetails_data = test["testdetails"]
            if isinstance(testdetails_data, str):
                try:
                    testdetails_list = json.loads(testdetails_data)
                except json.JSONDecodeError:
                    continue  # Skip invalid JSON
            elif isinstance(testdetails_data, list):
                testdetails_list = testdetails_data
            else:
                continue

            # Filter only approved tests and enrich with test_code and test_name from core_testdetails
            for test_item in testdetails_list:
                if test_item.get('approve') is True:
                    test_id = test_item.get('test_id')
                    test_code = test_item.get('test_code')

                    # Fetch test_code and test_name from core_testdetails collection
                    if test_id:
                        # Build query to match both test_id and test_code if available
                        query = {"test_id": test_id}
                        if test_code:
                            query["test_code"] = test_code

                        core_test = core_testdetails_collection.find_one(
                            query,
                            {"test_code": 1, "test_name": 1,"NABL": 1, "department": 1, "_id": 0}
                        )

                        if core_test:
                            # Update test_code and test_name from core_testdetails
                            test_item['test_code'] = core_test.get('test_code', 'N/A')
                            test_item['test_name'] = core_test.get('test_name', test_item.get('test_name', 'N/A'))
                            test_item['NABL'] = core_test.get('NABL', 'N/A')
                            test_item['department'] = core_test.get('department', 'N/A')
                        else:
                            # If no match found, try with just test_id
                            core_test = core_testdetails_collection.find_one(
                                {"test_id": test_id},
                                {"test_code": 1, "test_name": 1, "NABL": 1, "department": 1, "_id": 0}
                            )

                            if core_test:
                                test_item['test_code'] = core_test.get('test_code', 'N/A')
                                test_item['test_name'] = core_test.get('test_name', test_item.get('test_name', 'N/A'))
                                test_item['NABL'] = core_test.get('NABL', 'N/A')
                                test_item['department'] = core_test.get('department', 'N/A')
                            else:
                                test_item['test_code'] = test_item.get('test_code', 'N/A')
                                test_item['test_name'] = test_item.get('test_name', 'N/A')
                                test_item['NABL'] = test_item.get('NABL', 'N/A')
                                test_item['department'] = test_item.get('department', 'N/A')
                    else:
                        test_item['test_code'] = test_item.get('test_code', 'N/A')
                        test_item['test_name'] = test_item.get('test_name', 'N/A')
                        test_item['NABL'] = test_item.get('NABL', 'N/A')
                        test_item['department'] = test_item.get('department', 'N/A')

                    # Add created_date to each test item
                    test_item['created_date'] = test_created_date.isoformat() if test_created_date else None

                    test_list.append(test_item)

        # Return barcode as key
        if test_list:
            return JsonResponse({barcode: {"testdetails": test_list}})
        else:
            return JsonResponse({'error': 'No records found for this barcode'}, status=404)

    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_patient_test_details(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)

    try:
        barcode_details = BarcodeTestDetails.objects.filter(barcode=barcode).first()
        if not barcode_details:
            return JsonResponse({'error': 'No barcode details found for the given barcode'}, status=404)

        patient_id = barcode_details.patient_id
        bill_no    = barcode_details.bill_no

        test_values = TestValue.objects.filter(barcode=barcode)
        if not test_values.exists():
            return JsonResponse({'error': 'No test records found for the given barcode'}, status=404)

        patient = Patient.objects.filter(patient_id=patient_id).first()
        billing = Billing.objects.filter(bill_no=bill_no).first()
        sample_status = SampleStatus.objects.filter(patient_id=patient_id)

        barcodes = []
        try:
            tests    = json.loads(barcode_details.testdetails) if isinstance(barcode_details.testdetails, str) else barcode_details.testdetails
            barcodes = [test.get("barcode") for test in tests if test.get("barcode")]
        except (json.JSONDecodeError, AttributeError):
            barcodes = []

        # ── Resolve patient gender for reference range selection ──────────────
        # gender is stored on the Patient model; normalise to lowercase for matching
        patient_gender = (patient.gender or '') if patient else ''

        mongo_client             = get_client()
        mongo_db                 = mongo_client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = mongo_client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

        # ── Gender-aware reference_range / low / high resolver ────────────────
        def resolve_gender_fields(meta, gender):
            """
            Pick reference_range from male/female fields based on patient gender.
            Falls back to generic reference_range when gender-specific value absent.
            Returns (reference_range, _, _) — low/high not used in this view.
            """
            gender_key = (gender or '').strip().lower()   # 'male', 'female', or ''

            if gender_key == 'male' and meta.get('male'):
                reference_range = meta['male']
            elif gender_key == 'female' and meta.get('female'):
                reference_range = meta['female']
            else:
                reference_range = meta.get('reference_range', '') or ''

            return reference_range, '', ''

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

                employee_name      = profile.get("employeeName", "")
                designation        = profile.get("designation", "")
                signature_file_id  = profile.get("signatureFileId")
                signature_base64   = None

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
                        logger.error(f"Error fetching signature for employee {employee_id}: {str(e)}")

                return {
                    "employeeName":    employee_name,
                    "designation":     designation,
                    "signatureBase64": signature_base64,
                }
            except Exception as e:
                logger.error(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None

        # ── Process each TestValue record ─────────────────────────────────────
        all_results   = []
        all_approvers = set()

        for test_value_record in test_values:
            approved_tests = []

            test_details_list = test_value_record.testdetails
            if isinstance(test_details_list, str):
                try:
                    test_details_list = json.loads(test_details_list)
                except Exception:
                    test_details_list = []

            if not isinstance(test_details_list, list):
                test_details_list = []

            for test in test_details_list:
                if test.get("approve") is not True:
                    continue

                test_id     = test.get("test_id")
                device_id   = test.get("device_id")
                parameters  = test.get("parameters", [])
                approve_by  = test.get("approve_by", "")
                dispatch_time = test.get("dispatch_time", "")

                if approve_by:
                    all_approvers.add(approve_by)

                core_test = core_testdetails_collection.find_one({"test_id": test_id})

                if not core_test:
                    testname     = test.get("testname")
                    department   = test.get("department", "N/A")
                    NABL         = test.get("NABL", "N/A")
                    specimen_type = test.get("specimen_type") or core_test.get("specimen_type", "N/A")
                else:
                    testname      = core_test.get("test_name")
                    department    = core_test.get("department", "N/A")
                    NABL          = core_test.get("NABL", False)
                    critical_range = core_test.get("critical_range", "")
                    interpretation = core_test.get("interpretation", "")
                    labels         = core_test.get("labels", "")
                    lod         = core_test.get("lod", "")
                    specimen_type = test.get("specimen_type") or core_test.get("specimen_type", "N/A")

                outsourced  = test.get("outsourced", False)
                comment     = test.get("comment", "")
                verified_by = test.get("verified_by", "N/A")
                approve_time = test.get("approve_time", "N/A")

                status = None
                if sample_status.exists():
                    for sample_status_record in sample_status:
                        status_details = sample_status_record.testdetails
                        if isinstance(status_details, str):
                            try:
                                status_details = json.loads(status_details)
                            except Exception:
                                status_details = []
                        if isinstance(status_details, list):
                            status = next(
                                (s for s in status_details
                                 if s.get("test_id") == test_id or s.get("testname") == testname),
                                None,
                            )
                        if status:
                            break

                samplecollected_time = status.get("samplecollected_time") if status else None
                received_time        = status.get("received_time")         if status else None

                test_detail = {
                    "test_id":             test_id,
                    "department":          department,
                    "NABL":                NABL,
                    "critical_range":      critical_range,
                    "interpretation":      interpretation,
                    "labels":              labels,
                    "lod":                 lod,
                    "outsourced":          outsourced,
                    "comment":             comment,
                    "testname":            testname,
                    "verified_by":         verified_by,
                    "approve_by":          approve_by,
                    "approve_time":        approve_time,
                    "dispatch_time":       dispatch_time,
                    "samplecollected_time": samplecollected_time,
                    "received_time":       received_time,
                    "notes":               core_test.get("notes", "") if core_test else "",
                }

                # ── Parameterised test ────────────────────────────────────────
                if parameters and len(parameters) > 0 and core_test:
                    enriched_parameters = []

                    for param_index, param_value in enumerate(parameters):
                        test_code   = param_value.get("test_code")
                        value       = param_value.get("value", "")
                        param_comment = param_value.get("comment", "")

                        param_def = get_parameter_from_core(
                            core_test, device_id,
                            test_code=test_code,
                            param_index=param_index,
                        )

                        if param_def:
                            # Resolve gender-based reference_range only
                            ref_range, _, _ = resolve_gender_fields(param_def, patient_gender)

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
                            # Fallback: no core definition found for this param
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

                    test_detail["parameters"] = enriched_parameters

                elif parameters and len(parameters) > 0:
                    # No core_test found — pass raw parameters through unchanged
                    test_detail["parameters"] = parameters

                else:
                    # ── Single-value test (no parameters array) ───────────────
                    if core_test:
                        # Resolve gender-based reference_range only
                        ref_range, _, _ = resolve_gender_fields(core_test, patient_gender)

                        test_detail.update({
                            "method":          core_test.get("method", ""),
                            "specimen_type":   specimen_type,
                            "value":           test.get("value", ""),
                            "unit":            core_test.get("unit", ""),
                            "reference_range": ref_range,   # gender-resolved
                            "sub_title":       test.get("sub_title", ""),
                        })
                    else:
                        test_detail.update({
                            "method":          test.get("method", ""),
                            "specimen_type":   test.get("specimen_type", ""),
                            "value":           test.get("value", ""),
                            "unit":            test.get("unit", ""),
                            "reference_range": test.get("reference_range", ""),
                            "sub_title":       test.get("sub_title", ""),
                        })

                approved_tests.append(test_detail)

            if approved_tests:
                patient_details = {
                    "patient_id":  patient_id,
                    "patientname": patient.patientname if patient else "N/A",
                    "age":         patient.age         if patient else "N/A",
                    "age_type":    patient.age_type    if patient else "Years",
                    "gender":      patient.gender      if patient else "N/A",   # already present — used for H/L in frontend
                    "date":        test_value_record.date,
                    "barcode":     test_value_record.barcode,
                    "bill_no":     bill_no,
                    "barcodes":    barcodes,
                    "testdetails": approved_tests,
                    "refby":       billing.refby   if billing else "N/A",
                    "B2B":         billing.B2B     if billing else False,
                    "branch":      billing.branch  if billing else "N/A",
                }
                all_results.append(patient_details)

        if not all_results:
            return JsonResponse({'error': 'No approved test records found'}, status=404)

        # Fetch signature data for all approvers
        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)

        response_data = {
            "patient_data": all_results[0] if len(all_results) == 1 else all_results,
            "signatures":   signatures_data,
        }

        return JsonResponse(response_data, safe=False)

    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
