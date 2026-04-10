from datetime import datetime
from rest_framework.decorators import api_view
from urllib.parse import quote_plus
from pymongo import MongoClient
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from django.utils import timezone
from django.conf import settings
from django.utils.timezone import make_aware
from datetime import datetime
from django.conf import settings
import json
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ..models import TestValue,Patient,Hmsbarcode
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime
import os, json
from django.utils.timezone import make_aware
from dotenv import load_dotenv
load_dotenv()

from urllib.parse import unquote_plus
import re

# 🟢 3️⃣ Process testname filter once
def normalize_testname(name):
    if not name:
        return ""
    normalized = re.sub(r'\s+', ' ', name.strip().lower())
    normalized = normalized.replace('&', 'and').replace('/', ' ')
    return normalized


@api_view(["GET"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_test_values(request):
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    db = client.franchise
    billing_col = db.franchise_billing
    patient_col = db.franchise_patient

    corp = client.Corporatehealthcheckup
    billing_collection = corp.core_billing
    patient_collection = corp.core_employeeregistration

    # BarcodeTestDetails collection
    diagnostics = client.Diagnostics
    barcode_test_col = diagnostics.core_barcodetestdetails
    # core_testdetails collection for test metadata
    test_details_col = diagnostics.core_testdetails

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    patient_id_filter = request.GET.get('patient_id')
    testname_filter = request.GET.get('testname')
    emergency_filter = request.GET.get('emergency')

    # Date filters
    patients = TestValue.objects.all()
    if from_date and to_date:
        try:
            parsed_from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
            parsed_to_date = datetime.strptime(to_date, '%Y-%m-%d').date()
            patients = patients.filter(date__gte=parsed_from_date, date__lte=parsed_to_date)
        except ValueError:
            return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

    patients = patients.order_by('-date')
    patients_list = list(patients)

    if not patients_list:
        return JsonResponse([], safe=False)

    # Extract all barcodes upfront
    barcodes = [str(p.barcode).zfill(0) for p in patients_list]

    # ============================================
    # BULK FETCH ALL DATA SOURCES AT ONCE
    # ============================================

    # 1. Fetch all BarcodeTestDetails
    #    Fields: barcode, patient_id, is_emergency, patient_history,
    #            patientname, age, gender (core_barcodetestdetails has these)
    barcode_details_docs = list(barcode_test_col.find(
        {"barcode": {"$in": barcodes}},
        {
            "barcode": 1,
            "patient_id": 1,
            "is_emergency": 1,
            "patient_history": 1,
            "patientname": 1,
            "age": 1,
            "gender": 1,       # <-- gender from core_barcodetestdetails
            "_id": 0
        }
    ))
    barcode_details_dict = {
        doc['barcode']: {
            'patient_id':     doc.get('patient_id'),
            'is_emergency':   doc.get('is_emergency', False),
            'patient_history': doc.get('patient_history', ''),
            'patientname':    doc.get('patientname'),
            'age':            doc.get('age'),
            'gender':         doc.get('gender'),     # gender from barcode doc
        }
        for doc in barcode_details_docs
    }

    # Get all patient IDs from barcode details
    barcode_patient_ids = [info['patient_id'] for info in barcode_details_dict.values() if info['patient_id']]

    # Fetch all Patient records (Django ORM)
    patients_dict = {
        p.patient_id: {'name': p.patientname, 'age': p.age}
        for p in Patient.objects.filter(patient_id__in=barcode_patient_ids).only('patient_id', 'patientname', 'age')
    }

    # 2. Fetch patient details from Hmsbarcode (PRIMARY SOURCE)
    #    Hmsbarcode has: patient_id, patientname, age, age_type, billnumber,
    #                    gender, phone, ref_doctor, testdetails
    hms_barcodes_dict = {
        b.barcode: {
            'patient_id':  b.patient_id,
            'name':        b.patientname,
            'age':         b.age,
            'age_type':    b.age_type,          # <-- age_type
            'billnumber':  b.billnumber,
            'gender':      b.gender,            # <-- gender
            'phone':       b.phone,             # <-- phone
            'ref_doctor':  b.ref_doctor,        # <-- ref_doctor
            'testdetails': b.testdetails,
        }
        for b in Hmsbarcode.objects.filter(barcode__in=barcodes).only(
            'barcode', 'patient_id', 'patientname', 'age', 'age_type',
            'billnumber', 'gender', 'phone', 'ref_doctor', 'testdetails'
        )
    }

    # 3. MongoDB franchise_patient - bulk fetch (fallback)
    #    franchise_patient has: patient_id, patientname, age, gender, phoneNumber
    mongo_billing_docs = list(billing_col.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "_id": 0}
    ))
    mongo_billing_dict = {doc['barcode']: doc.get('patient_id') for doc in mongo_billing_docs}

    mongo_patient_ids = list(mongo_billing_dict.values())
    mongo_patient_docs = list(patient_col.find(
        {"patient_id": {"$in": mongo_patient_ids}},
        {"patient_id": 1, "patientname": 1, "age": 1, "gender": 1, "phoneNumber": 1, "_id": 0}
    ))
    mongo_patient_dict = {
        doc['patient_id']: {
            'name':   doc.get('patientname', 'N/A'),
            'age':    doc.get('age', 'N/A'),
            'gender': doc.get('gender'),            # <-- gender from franchise_patient
            'phone':  doc.get('phoneNumber'),       # <-- phoneNumber from franchise_patient
        }
        for doc in mongo_patient_docs
    }

    # 4. Corporate core_employeeregistration - bulk fetch (fallback)
    #    core_employeeregistration has: employee_id, employee_name, age, gender, mobile
    corp_billing_docs = list(billing_collection.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "employee_id": 1, "_id": 0}
    ))
    corp_billing_dict = {doc['barcode']: doc.get('employee_id') for doc in corp_billing_docs}

    corp_employee_ids = list(corp_billing_dict.values())
    corp_employee_docs = list(patient_collection.find(
        {"employee_id": {"$in": corp_employee_ids}},
        {"employee_id": 1, "employee_name": 1, "age": 1, "gender": 1, "mobile": 1, "_id": 0}
    ))
    corp_employee_dict = {
        doc['employee_id']: {
            'name':   doc.get('employee_name', 'N/A'),
            'age':    doc.get('age', 'N/A'),
            'gender': doc.get('gender'),            # <-- gender from corp
            'phone':  doc.get('mobile'),            # <-- mobile from corp
        }
        for doc in corp_employee_docs
    }

    # Collect all unique test_ids
    all_test_ids = set()
    for patient in patients_list:
        try:
            test_details = json.loads(patient.testdetails) if isinstance(patient.testdetails, str) else patient.testdetails
            for test in test_details:
                if test.get('test_id'):
                    all_test_ids.add(test['test_id'])
        except (json.JSONDecodeError, TypeError):
            continue

    # Bulk fetch test details from MongoDB core_testdetails
    test_metadata_docs = list(test_details_col.find(
        {"test_id": {"$in": list(all_test_ids)}},
        {
            "test_id": 1,
            "test_name": 1,
            "specimen_type": 1,
            "collection_container": 1,
            "department": 1,
            "device_id": 1,
            "parameters": 1,
            "unit": 1,
            "method": 1,
            "reference_range": 1,
            "_id": 0
        }
    ))

    test_metadata_dict = {doc['test_id']: doc for doc in test_metadata_docs}

    def get_parameter_details(test_id, test_code, device_id=None, param_index=None, gender=None):
        test_meta = test_metadata_dict.get(test_id)
        if not test_meta:
            return None

        params_data = test_meta.get('parameters', [])

        if isinstance(params_data, dict):
            if device_id and device_id in params_data:
                params_list = params_data[device_id]
            else:
                device_ids = test_meta.get('device_id', [])
                if device_ids and len(device_ids) > 0:
                    first_device = device_ids[0]
                    params_list = params_data.get(first_device, [])
                else:
                    params_list = next(iter(params_data.values())) if params_data else []
        elif isinstance(params_data, list):
            params_list = params_data
        else:
            return None

        if not isinstance(params_list, list):
            params_list = []

        def resolve_reference_range(param, gender):
            """
            Pick reference_range based on gender.
            Parameters may store gender-specific ranges as 'male' / 'female' fields.
            Falls back to the generic 'reference_range' field if gender-specific is absent.
            """
            gender_key = (gender or '').strip().lower()  # 'male', 'female', or ''
            if gender_key == 'male' and param.get('male'):
                return param['male']
            if gender_key == 'female' and param.get('female'):
                return param['female']
            # Fallback: generic reference_range field (older / non-gendered parameters)
            return param.get('reference_range') or ''

        if param_index is not None and 0 <= param_index < len(params_list):
            param = params_list[param_index]
            return {
                'parameter_name': param.get('test_name'),
                'unit':            param.get('unit'),
                'reference_range': resolve_reference_range(param, gender),
                'method':          param.get('method'),
                'department':      param.get('department') or test_meta.get('department'),
                'sub_title':       param.get('sub_title', ''),
                'value_option':    param.get('value_option', [])
            }

        matching_params = [p for p in params_list if isinstance(p, dict) and p.get('test_code') == test_code]
        if matching_params:
            param = matching_params[0]
            return {
                'parameter_name': param.get('test_name'),
                'unit':            param.get('unit'),
                'reference_range': resolve_reference_range(param, gender),
                'method':          param.get('method'),
                'department':      param.get('department') or test_meta.get('department'),
                'sub_title':       param.get('sub_title', ''),
                'value_option':    param.get('value_option', [])
            }

        return None

    # ============================================
    # PROCESS PATIENTS
    # ============================================

    filter_normalized = None
    if testname_filter and testname_filter != 'undefined':
        testname_filter = unquote_plus(testname_filter)
        filter_normalized = normalize_testname(testname_filter)

    patient_data = []

    for patient in patients_list:
        barcode_val = str(patient.barcode).zfill(0)

        try:
            test_details = json.loads(patient.testdetails) if isinstance(patient.testdetails, str) else patient.testdetails
        except (json.JSONDecodeError, TypeError):
            test_details = []

        # Initialize all fields
        patient_name       = "N/A"
        patient_age        = "N/A"
        current_patient_id = None
        is_emergency       = False
        patient_history    = ""
        age_type           = ""
        gender             = ""
        phone              = ""
        ref_doctor         = ""

        # ── PRIORITY 1: Hmsbarcode (richest source — has age_type, phone, ref_doctor, gender) ──
        if barcode_val in hms_barcodes_dict:
            hms_info           = hms_barcodes_dict[barcode_val]
            current_patient_id = hms_info['patient_id']
            patient_name       = hms_info['name']
            patient_age        = hms_info['age']
            age_type           = hms_info.get('age_type', '')
            gender             = hms_info.get('gender', '')
            phone              = hms_info.get('phone', '')
            ref_doctor         = hms_info.get('ref_doctor', '')

        # ── PRIORITY 2: core_barcodetestdetails (has is_emergency, patient_history, gender fallback) ──
        if barcode_val in barcode_details_dict:
            barcode_info    = barcode_details_dict[barcode_val]
            is_emergency    = barcode_info['is_emergency']
            patient_history = barcode_info['patient_history']

            if not current_patient_id:
                current_patient_id = barcode_info['patient_id']

            # Fill patient name/age from Django ORM Patient model if not set yet
            if patient_name == "N/A" and current_patient_id in patients_dict:
                patient_info = patients_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age  = patient_info['age']

            # gender fallback from barcode doc
            if not gender and barcode_info.get('gender'):
                gender = barcode_info['gender']

        # ── PRIORITY 3: franchise_patient (fallback — has gender, phoneNumber) ──
        if not current_patient_id and barcode_val in mongo_billing_dict:
            current_patient_id = mongo_billing_dict[barcode_val]
            if current_patient_id in mongo_patient_dict:
                patient_info = mongo_patient_dict[current_patient_id]
                if patient_name == "N/A":
                    patient_name = patient_info['name']
                    patient_age  = patient_info['age']
                if not gender and patient_info.get('gender'):
                    gender = patient_info['gender']
                if not phone and patient_info.get('phone'):
                    phone = patient_info['phone']

        # ── PRIORITY 4: core_employeeregistration (fallback — has gender, mobile) ──
        if not current_patient_id and barcode_val in corp_billing_dict:
            current_patient_id = corp_billing_dict[barcode_val]
            if current_patient_id in corp_employee_dict:
                corp_info = corp_employee_dict[current_patient_id]
                if patient_name == "N/A":
                    patient_name = corp_info['name']
                    patient_age  = corp_info['age']
                if not gender and corp_info.get('gender'):
                    gender = corp_info['gender']
                if not phone and corp_info.get('phone'):
                    phone = corp_info['phone']

        # Final fallback for patient_id
        if not current_patient_id:
            current_patient_id = getattr(patient, 'patient_id', None)

        # Patient ID filter
        if patient_id_filter and current_patient_id != patient_id_filter:
            continue

        # Emergency filter
        if emergency_filter:
            if emergency_filter == 'emergency' and not is_emergency:
                continue
            elif emergency_filter == 'normal' and is_emergency:
                continue

        # Enrich test details with metadata from MongoDB
        enriched_test_details = []
        for test in test_details:
            test_id    = test.get('test_id')
            test_code  = test.get('test_code')
            device_id  = test.get('device_id')
            parameters = test.get('parameters', [])

            test_meta = test_metadata_dict.get(test_id, {})

            if parameters and isinstance(parameters, list) and len(parameters) > 0:
                enriched_parameters = []
                for param_index, param in enumerate(parameters):
                    param_test_code = param.get('test_code')
                    param_value     = param.get('value')
                    param_comment   = param.get('comment', '')
                    param_status   = param.get('status')

                    param_details = get_parameter_details(test_id, param_test_code, device_id, param_index=param_index, gender=gender)

                    if param_details:
                        enriched_param = {
                            'test_code':       param_test_code,
                            'parameter_name':  param_details.get('parameter_name'),
                            'value':           param_value,
                            'unit':            param_details.get('unit', 'N/A'),
                            'reference_range': param_details.get('reference_range', 'N/A'),
                            'method':          param_details.get('method', 'N/A'),
                            'department':      param_details.get('department', 'N/A'),
                            'sub_title':       param_details.get('sub_title', ''),
                            'value_option':    param_details.get('value_option', []),
                            'comment':         param_comment,
                            'status':          param_status,
                        }
                    else:
                        enriched_param = {
                            'test_code':       param_test_code,
                            'parameter_name':  None,
                            'value':           param_value,
                            'unit':            'N/A',
                            'reference_range': 'N/A',
                            'method':          'N/A',
                            'department':      'N/A',
                            'sub_title':       '',
                            'value_option':    [],
                            'comment':         param_comment,
                            'status':          param_status,
                        }

                    enriched_parameters.append(enriched_param)

                enriched_test = {
                    **test,
                    'test_name':            test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type':        test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department':           test_meta.get('department', test.get('department', 'N/A')),
                    'parameters':           enriched_parameters,
                }

            else:
                enriched_test = {
                    **test,
                    'test_name':            test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type':        test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department':           test_meta.get('department', test.get('department', 'N/A')),
                    'unit':                 test_meta.get('unit', test.get('unit', 'N/A')),
                    'method':               test_meta.get('method', test.get('method', 'N/A')),
                    'reference_range':      test_meta.get('reference_range', test.get('reference_range', 'N/A')),
                }

                if test_code and test_code != 'N/A' and test_meta.get('parameters'):
                    param_details = get_parameter_details(test_id, test_code, device_id, gender=gender)
                    if param_details:
                        enriched_test.update({
                            'parameter_name':  param_details.get('parameter_name'),
                            'unit':            param_details.get('unit', enriched_test['unit']),
                            'reference_range': param_details.get('reference_range', enriched_test['reference_range']),
                            'method':          param_details.get('method', enriched_test['method']),
                            'department':      param_details.get('department', enriched_test['department']),
                        })

            enriched_test_details.append(enriched_test)

        test_details = enriched_test_details

        # Testname filtering
        if filter_normalized:
            filtered_test_details = []
            for test in test_details:
                test_name       = test.get('test_name', '')
                test_normalized = normalize_testname(test_name)
                match_found = (
                    test_normalized == filter_normalized or
                    filter_normalized in test_normalized or
                    test_normalized in filter_normalized or
                    all(word in test_normalized for word in filter_normalized.split() if len(word) > 2)
                )
                if match_found:
                    filtered_test_details.append(test)
            test_details = filtered_test_details

        # Only pending tests
        filtered_tests = [
            test for test in test_details
            if not test.get('approve', False) and not test.get('rerun', False)
        ]

        if not filtered_tests:
            continue

        patient_data.append({
            "patient_id":      current_patient_id,
            "patientname":     patient_name,
            "age":             patient_age,
            "age_type":        age_type,       # NEW
            "gender":          gender,         # NEW
            "phone":           phone,          # NEW
            "ref_doctor":      ref_doctor,     # NEW
            "locationId":      patient.locationId,
            "barcode":         barcode_val,
            "date":            patient.date,
            "created_date":    patient.created_date,
            "is_emergency":    is_emergency,
            "patient_history": patient_history,
            "testdetails":     filtered_tests,
        })

    return JsonResponse(patient_data, safe=False)



@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def approve_test_detail(request, barcode):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_testvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode")
        created_date_str = update_data.get("created_date")
        test_id = update_data.get("test_id")
        approve_time = update_data.get("approve_time")
        status = update_data.get("status")  # "Normal" or "Abnormal" — sent only for CHC locations

        if not (barcode and created_date_str and test_id):
            return JsonResponse({
                "error": "barcode, created_date, and test_id required"
            }, status=400)

        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
    except Exception as e:
        return JsonResponse({"error": f"Invalid request format: {str(e)}"}, status=400)

    # Query with barcode and created_date
    query = {"barcode": barcode, "created_date": created_date}
    test_value = collection.find_one(query)
    if not test_value:
        return JsonResponse({
            "error": "Patient record not found for given barcode & created_date."
        }, status=404)

    try:
        test_details = json.loads(test_value.get("testdetails", "[]"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to decode test details."}, status=500)

    # Find the test by test_id and update
    test_found = False
    for test_detail in test_details:
        if test_detail.get("test_id") == test_id:
            test_detail["approve"] = update_data.get("approve", False)
            if test_detail["approve"]:
                if approve_time:
                    test_detail["approve_time"] = approve_time
                if "approve_by" in update_data:
                    test_detail["approve_by"] = update_data["approve_by"]
                # Save Normal/Abnormal status when provided (CHC locations)
                if status in ("Normal", "Abnormal"):
                    test_detail["status"] = status
            test_found = True
            break

    if not test_found:
        return JsonResponse({"error": "Test not found with given test_id."}, status=404)

    # Persist the updated test details
    result = collection.update_one(
        query,
        {"$set": {"testdetails": json.dumps(test_details)}}
    )
    if result.modified_count > 0:
        return JsonResponse({"message": "Test detail approved successfully."})
    return JsonResponse({"error": "Failed to update test detail."}, status=500)


@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def rerun_test_detail(request, barcode):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_testvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode")
        created_date_str = update_data.get("created_date")
        test_id = update_data.get("test_id")
        rerun_time = update_data.get("rerun_time")  # Get from frontend
        if not (barcode and created_date_str and test_id):
            return JsonResponse({
                "error": "barcode, created_date, and test_id required"
            }, status=400)
        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
    except Exception as e:
        return JsonResponse({"error": f"Invalid request format: {str(e)}"}, status=400)
    # Query with barcode and created_date
    query = {"barcode": barcode, "created_date": created_date}
    test_value = collection.find_one(query)
    if not test_value:
        return JsonResponse({
            "error": "Patient record not found for given barcode & created_date."
        }, status=404)
    try:
        test_details = json.loads(test_value.get("testdetails", "[]"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to decode test details."}, status=500)
    # Find the test by test_id
    test_found = False
    for test_detail in test_details:
        if test_detail.get("test_id") == test_id:
            test_detail["rerun"] = update_data.get("rerun", False)
            if test_detail["rerun"]:
                # Use rureun_time from frontend if provided
                if rerun_time:
                    test_detail["rerun_time"] = rerun_time
                if "rerun_by" in update_data:
                    test_detail["rerun_by"] = update_data["rerun_by"]
            test_found = True
            break
    if not test_found:
        return JsonResponse({"error": "Test not found with given test_id."}, status=404)
    # Update the document
    result = collection.update_one(
        query,
        {"$set": {"testdetails": json.dumps(test_details)}}
    )
    if result.modified_count > 0:
        return JsonResponse({"message": "Test detail rerun Initiated."})
    return JsonResponse({"error": "Failed to update test detail."}, status=500)



@api_view(["GET"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_approved_values(request):
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    db = client.franchise
    billing_col = db.franchise_billing
    patient_col = db.franchise_patient

    corp = client.Corporatehealthcheckup
    billing_collection = corp.core_billing
    patient_collection = corp.core_employeeregistration

    # BarcodeTestDetails collection
    diagnostics = client.Diagnostics
    barcode_test_col = diagnostics.core_barcodetestdetails
    # core_testdetails collection for test metadata
    test_details_col = diagnostics.core_testdetails

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    patient_id_filter = request.GET.get('patient_id')
    testname_filter = request.GET.get('testname')
    emergency_filter = request.GET.get('emergency')

    # Date filters
    patients = TestValue.objects.all()
    if from_date and to_date:
        try:
            parsed_from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
            parsed_to_date = datetime.strptime(to_date, '%Y-%m-%d').date()
            patients = patients.filter(date__gte=parsed_from_date, date__lte=parsed_to_date)
        except ValueError:
            return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

    patients = patients.order_by('-date')
    patients_list = list(patients)

    if not patients_list:
        return JsonResponse([], safe=False)

    # Extract all barcodes upfront
    barcodes = [str(p.barcode).zfill(0) for p in patients_list]

    # ============================================
    # BULK FETCH ALL DATA SOURCES AT ONCE
    # ============================================

    # 1. Fetch all BarcodeTestDetails
    #    Fields: barcode, patient_id, is_emergency, patient_history,
    #            patientname, age, gender (core_barcodetestdetails has these)
    barcode_details_docs = list(barcode_test_col.find(
        {"barcode": {"$in": barcodes}},
        {
            "barcode": 1,
            "patient_id": 1,
            "is_emergency": 1,
            "patient_history": 1,
            "patientname": 1,
            "age": 1,
            "gender": 1,       # <-- gender from core_barcodetestdetails
            "_id": 0
        }
    ))
    barcode_details_dict = {
        doc['barcode']: {
            'patient_id':     doc.get('patient_id'),
            'is_emergency':   doc.get('is_emergency', False),
            'patient_history': doc.get('patient_history', ''),
            'patientname':    doc.get('patientname'),
            'age':            doc.get('age'),
            'gender':         doc.get('gender'),     # gender from barcode doc
        }
        for doc in barcode_details_docs
    }

    # Get all patient IDs from barcode details
    barcode_patient_ids = [info['patient_id'] for info in barcode_details_dict.values() if info['patient_id']]

    # Fetch all Patient records (Django ORM)
    patients_dict = {
        p.patient_id: {'name': p.patientname, 'age': p.age}
        for p in Patient.objects.filter(patient_id__in=barcode_patient_ids).only('patient_id', 'patientname', 'age')
    }

    # 2. Fetch patient details from Hmsbarcode (PRIMARY SOURCE)
    #    Hmsbarcode has: patient_id, patientname, age, age_type, billnumber,
    #                    gender, phone, ref_doctor, testdetails
    hms_barcodes_dict = {
        b.barcode: {
            'patient_id':  b.patient_id,
            'name':        b.patientname,
            'age':         b.age,
            'age_type':    b.age_type,          # <-- age_type
            'billnumber':  b.billnumber,
            'gender':      b.gender,            # <-- gender
            'phone':       b.phone,             # <-- phone
            'ref_doctor':  b.ref_doctor,        # <-- ref_doctor
            'testdetails': b.testdetails,
        }
        for b in Hmsbarcode.objects.filter(barcode__in=barcodes).only(
            'barcode', 'patient_id', 'patientname', 'age', 'age_type',
            'billnumber', 'gender', 'phone', 'ref_doctor', 'testdetails'
        )
    }

    # 3. MongoDB franchise_patient - bulk fetch (fallback)
    #    franchise_patient has: patient_id, patientname, age, gender, phoneNumber
    mongo_billing_docs = list(billing_col.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "_id": 0}
    ))
    mongo_billing_dict = {doc['barcode']: doc.get('patient_id') for doc in mongo_billing_docs}

    mongo_patient_ids = list(mongo_billing_dict.values())
    mongo_patient_docs = list(patient_col.find(
        {"patient_id": {"$in": mongo_patient_ids}},
        {"patient_id": 1, "patientname": 1, "age": 1, "gender": 1, "phoneNumber": 1, "_id": 0}
    ))
    mongo_patient_dict = {
        doc['patient_id']: {
            'name':   doc.get('patientname', 'N/A'),
            'age':    doc.get('age', 'N/A'),
            'gender': doc.get('gender'),            # <-- gender from franchise_patient
            'phone':  doc.get('phoneNumber'),       # <-- phoneNumber from franchise_patient
        }
        for doc in mongo_patient_docs
    }

    # 4. Corporate core_employeeregistration - bulk fetch (fallback)
    #    core_employeeregistration has: employee_id, employee_name, age, gender, mobile
    corp_billing_docs = list(billing_collection.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "employee_id": 1, "_id": 0}
    ))
    corp_billing_dict = {doc['barcode']: doc.get('employee_id') for doc in corp_billing_docs}

    corp_employee_ids = list(corp_billing_dict.values())
    corp_employee_docs = list(patient_collection.find(
        {"employee_id": {"$in": corp_employee_ids}},
        {"employee_id": 1, "employee_name": 1, "age": 1, "gender": 1, "mobile": 1, "_id": 0}
    ))
    corp_employee_dict = {
        doc['employee_id']: {
            'name':   doc.get('employee_name', 'N/A'),
            'age':    doc.get('age', 'N/A'),
            'gender': doc.get('gender'),            # <-- gender from corp
            'phone':  doc.get('mobile'),            # <-- mobile from corp
        }
        for doc in corp_employee_docs
    }

    # Collect all unique test_ids
    all_test_ids = set()
    for patient in patients_list:
        try:
            test_details = json.loads(patient.testdetails) if isinstance(patient.testdetails, str) else patient.testdetails
            for test in test_details:
                if test.get('test_id'):
                    all_test_ids.add(test['test_id'])
        except (json.JSONDecodeError, TypeError):
            continue

    # Bulk fetch test details from MongoDB core_testdetails
    test_metadata_docs = list(test_details_col.find(
        {"test_id": {"$in": list(all_test_ids)}},
        {
            "test_id": 1,
            "test_name": 1,
            "specimen_type": 1,
            "collection_container": 1,
            "department": 1,
            "device_id": 1,
            "parameters": 1,
            "unit": 1,
            "method": 1,
            "reference_range": 1,
            "_id": 0
        }
    ))

    test_metadata_dict = {doc['test_id']: doc for doc in test_metadata_docs}

    def get_parameter_details(test_id, test_code, device_id=None, param_index=None, gender=None):
        test_meta = test_metadata_dict.get(test_id)
        if not test_meta:
            return None

        params_data = test_meta.get('parameters', [])

        if isinstance(params_data, dict):
            if device_id and device_id in params_data:
                params_list = params_data[device_id]
            else:
                device_ids = test_meta.get('device_id', [])
                if device_ids and len(device_ids) > 0:
                    first_device = device_ids[0]
                    params_list = params_data.get(first_device, [])
                else:
                    params_list = next(iter(params_data.values())) if params_data else []
        elif isinstance(params_data, list):
            params_list = params_data
        else:
            return None

        if not isinstance(params_list, list):
            params_list = []

        def resolve_reference_range(param, gender):
            """
            Pick reference_range based on gender.
            Parameters may store gender-specific ranges as 'male' / 'female' fields.
            Falls back to the generic 'reference_range' field if gender-specific is absent.
            """
            gender_key = (gender or '').strip().lower()  # 'male', 'female', or ''
            if gender_key == 'male' and param.get('male'):
                return param['male']
            if gender_key == 'female' and param.get('female'):
                return param['female']
            # Fallback: generic reference_range field (older / non-gendered parameters)
            return param.get('reference_range') or ''

        if param_index is not None and 0 <= param_index < len(params_list):
            param = params_list[param_index]
            return {
                'parameter_name': param.get('test_name'),
                'unit':            param.get('unit'),
                'reference_range': resolve_reference_range(param, gender),
                'method':          param.get('method'),
                'department':      param.get('department') or test_meta.get('department'),
                'sub_title':       param.get('sub_title', ''),
                'value_option':    param.get('value_option', [])
            }

        matching_params = [p for p in params_list if isinstance(p, dict) and p.get('test_code') == test_code]
        if matching_params:
            param = matching_params[0]
            return {
                'parameter_name': param.get('test_name'),
                'unit':            param.get('unit'),
                'reference_range': resolve_reference_range(param, gender),
                'method':          param.get('method'),
                'department':      param.get('department') or test_meta.get('department'),
                'sub_title':       param.get('sub_title', ''),
                'value_option':    param.get('value_option', [])
            }

        return None

    # ============================================
    # PROCESS PATIENTS
    # ============================================

    filter_normalized = None
    if testname_filter and testname_filter != 'undefined':
        testname_filter = unquote_plus(testname_filter)
        filter_normalized = normalize_testname(testname_filter)

    patient_data = []

    for patient in patients_list:
        barcode_val = str(patient.barcode).zfill(0)

        try:
            test_details = json.loads(patient.testdetails) if isinstance(patient.testdetails, str) else patient.testdetails
        except (json.JSONDecodeError, TypeError):
            test_details = []

        # Initialize all fields
        patient_name       = "N/A"
        patient_age        = "N/A"
        current_patient_id = None
        is_emergency       = False
        patient_history    = ""
        age_type           = ""
        gender             = ""
        phone              = ""
        ref_doctor         = ""

        # ── PRIORITY 1: Hmsbarcode (richest source — has age_type, phone, ref_doctor, gender) ──
        if barcode_val in hms_barcodes_dict:
            hms_info           = hms_barcodes_dict[barcode_val]
            current_patient_id = hms_info['patient_id']
            patient_name       = hms_info['name']
            patient_age        = hms_info['age']
            age_type           = hms_info.get('age_type', '')
            gender             = hms_info.get('gender', '')
            phone              = hms_info.get('phone', '')
            ref_doctor         = hms_info.get('ref_doctor', '')

        # ── PRIORITY 2: core_barcodetestdetails (has is_emergency, patient_history, gender fallback) ──
        if barcode_val in barcode_details_dict:
            barcode_info    = barcode_details_dict[barcode_val]
            is_emergency    = barcode_info['is_emergency']
            patient_history = barcode_info['patient_history']

            if not current_patient_id:
                current_patient_id = barcode_info['patient_id']

            # Fill patient name/age from Django ORM Patient model if not set yet
            if patient_name == "N/A" and current_patient_id in patients_dict:
                patient_info = patients_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age  = patient_info['age']

            # gender fallback from barcode doc
            if not gender and barcode_info.get('gender'):
                gender = barcode_info['gender']

        # ── PRIORITY 3: franchise_patient (fallback — has gender, phoneNumber) ──
        if not current_patient_id and barcode_val in mongo_billing_dict:
            current_patient_id = mongo_billing_dict[barcode_val]
            if current_patient_id in mongo_patient_dict:
                patient_info = mongo_patient_dict[current_patient_id]
                if patient_name == "N/A":
                    patient_name = patient_info['name']
                    patient_age  = patient_info['age']
                if not gender and patient_info.get('gender'):
                    gender = patient_info['gender']
                if not phone and patient_info.get('phone'):
                    phone = patient_info['phone']

        # ── PRIORITY 4: core_employeeregistration (fallback — has gender, mobile) ──
        if not current_patient_id and barcode_val in corp_billing_dict:
            current_patient_id = corp_billing_dict[barcode_val]
            if current_patient_id in corp_employee_dict:
                corp_info = corp_employee_dict[current_patient_id]
                if patient_name == "N/A":
                    patient_name = corp_info['name']
                    patient_age  = corp_info['age']
                if not gender and corp_info.get('gender'):
                    gender = corp_info['gender']
                if not phone and corp_info.get('phone'):
                    phone = corp_info['phone']

        # Final fallback for patient_id
        if not current_patient_id:
            current_patient_id = getattr(patient, 'patient_id', None)

        # Patient ID filter
        if patient_id_filter and current_patient_id != patient_id_filter:
            continue

        # Emergency filter
        if emergency_filter:
            if emergency_filter == 'emergency' and not is_emergency:
                continue
            elif emergency_filter == 'normal' and is_emergency:
                continue

        # Enrich test details with metadata from MongoDB
        enriched_test_details = []
        for test in test_details:
            test_id    = test.get('test_id')
            test_code  = test.get('test_code')
            device_id  = test.get('device_id')
            parameters = test.get('parameters', [])

            test_meta = test_metadata_dict.get(test_id, {})

            if parameters and isinstance(parameters, list) and len(parameters) > 0:
                enriched_parameters = []
                for param_index, param in enumerate(parameters):
                    param_test_code = param.get('test_code')
                    param_value     = param.get('value')
                    param_history   = param.get('history')
                    param_comment   = param.get('comment', '')
                    param_status   = param.get('status')

                    param_details = get_parameter_details(test_id, param_test_code, device_id, param_index=param_index, gender=gender)

                    if param_details:
                        enriched_param = {
                            'test_code':       param_test_code,
                            'parameter_name':  param_details.get('parameter_name'),
                            'value':           param_value,
                            'history':         param_history,
                            'unit':            param_details.get('unit', 'N/A'),
                            'reference_range': param_details.get('reference_range', 'N/A'),
                            'method':          param_details.get('method', 'N/A'),
                            'department':      param_details.get('department', 'N/A'),
                            'sub_title':       param_details.get('sub_title', ''),
                            'value_option':    param_details.get('value_option', []),
                            'comment':         param_comment,
                            'status':          param_status,
                        }
                    else:
                        enriched_param = {
                            'test_code':       param_test_code,
                            'parameter_name':  None,
                            'value':           param_value,
                            'history':         param_history,
                            'unit':            'N/A',
                            'reference_range': 'N/A',
                            'method':          'N/A',
                            'department':      'N/A',
                            'sub_title':       '',
                            'value_option':    [],
                            'comment':         param_comment,
                            'status':          param_status,
                        }

                    enriched_parameters.append(enriched_param)

                enriched_test = {
                    **test,
                    'test_name':            test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type':        test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department':           test_meta.get('department', test.get('department', 'N/A')),
                    'parameters':           enriched_parameters,
                }

            else:
                enriched_test = {
                    **test,
                    'test_name':            test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type':        test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department':           test_meta.get('department', test.get('department', 'N/A')),
                    'unit':                 test_meta.get('unit', test.get('unit', 'N/A')),
                    'method':               test_meta.get('method', test.get('method', 'N/A')),
                    'reference_range':      test_meta.get('reference_range', test.get('reference_range', 'N/A')),
                }

                if test_code and test_code != 'N/A' and test_meta.get('parameters'):
                    param_details = get_parameter_details(test_id, test_code, device_id, gender=gender)
                    if param_details:
                        enriched_test.update({
                            'parameter_name':  param_details.get('parameter_name'),
                            'unit':            param_details.get('unit', enriched_test['unit']),
                            'reference_range': param_details.get('reference_range', enriched_test['reference_range']),
                            'method':          param_details.get('method', enriched_test['method']),
                            'department':      param_details.get('department', enriched_test['department']),
                        })

            enriched_test_details.append(enriched_test)

        test_details = enriched_test_details

        # Testname filtering
        if filter_normalized:
            filtered_test_details = []
            for test in test_details:
                test_name       = test.get('test_name', '')
                test_normalized = normalize_testname(test_name)
                match_found = (
                    test_normalized == filter_normalized or
                    filter_normalized in test_normalized or
                    test_normalized in filter_normalized or
                    all(word in test_normalized for word in filter_normalized.split() if len(word) > 2)
                )
                if match_found:
                    filtered_test_details.append(test)
            test_details = filtered_test_details

        # Only pending tests
        filtered_tests = [
    test for test in test_details
    if test.get('approve', False)
]

        if not filtered_tests:
            continue

        patient_data.append({
            "patient_id":      current_patient_id,
            "patientname":     patient_name,
            "age":             patient_age,
            "age_type":        age_type,       # NEW
            "gender":          gender,         # NEW
            "phone":           phone,          # NEW
            "ref_doctor":      ref_doctor,     # NEW
            "locationId":      patient.locationId,
            "barcode":         barcode_val,
            "date":            patient.date,
            "created_date":    patient.created_date,
            "is_emergency":    is_emergency,
            "patient_history": patient_history,
            "testdetails":     filtered_tests,
        })

    return JsonResponse(patient_data, safe=False)


@api_view(['PATCH'])
def edit_test_value(request, barcode):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    collection = client.Diagnostics.core_testvalue

    try:
        data = request.data
        barcode = data.get("barcode")
        created_date_str = data.get("created_date")
        test_id = data.get("test_id")
        new_value = data.get("new_value")
        history_entry = data.get("history_entry")  # {old_value, edited_by, reason, edited_at}
        param_index = data.get("param_index")       # None for test-level, int for parameter

        if not (barcode and created_date_str and test_id and new_value and history_entry):
            return JsonResponse({"error": "barcode, created_date, test_id, new_value, history_entry required"}, status=400)

        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
    except Exception as e:
        return JsonResponse({"error": f"Invalid request: {str(e)}"}, status=400)

    query = {"barcode": barcode, "created_date": created_date}
    record = collection.find_one(query)
    if not record:
        return JsonResponse({"error": "Record not found."}, status=404)

    try:
        test_details = json.loads(record.get("testdetails", "[]"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to decode testdetails."}, status=500)

    test_found = False
    for detail in test_details:
        if detail.get("test_id") == test_id:
            if param_index is not None:
                # Edit a specific parameter value
                params = detail.get("parameters", [])
                if not (0 <= param_index < len(params)):
                    return JsonResponse({"error": "Invalid param_index."}, status=400)
                if "history" not in params[param_index]:
                    params[param_index]["history"] = []
                params[param_index]["history"].insert(0, history_entry)
                params[param_index]["value"] = new_value
                detail["parameters"] = params
            else:
                # Edit the test-level value
                if "history" not in detail:
                    detail["history"] = []
                detail["history"].insert(0, history_entry)
                detail["value"] = new_value
            test_found = True
            break

    if not test_found:
        return JsonResponse({"error": "Test not found."}, status=404)

    result = collection.update_one(
        query,
        {"$set": {
            "testdetails": json.dumps(test_details),
            "lastmodified_by": history_entry.get("edited_by"),
            "lastmodified_date": datetime.utcnow(),
        }}
    )

    if result.modified_count > 0:
        return JsonResponse({"message": "Test value updated successfully."})
    return JsonResponse({"error": "Failed to update."}, status=500)
