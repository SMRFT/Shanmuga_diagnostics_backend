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
    # NEW: core_testdetails collection for test metadata
    test_details_col = diagnostics.core_testdetails

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    patient_id_filter = request.GET.get('patient_id')
    testname_filter = request.GET.get('testname')
    emergency_filter = request.GET.get('emergency')

    # Date filters - apply early
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
    
    # 1. Fetch all BarcodeTestDetails with is_emergency and patient_history
    barcode_details_docs = list(barcode_test_col.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "is_emergency": 1, "patient_history": 1, "_id": 0}
    ))
    barcode_details_dict = {
        doc['barcode']: {
            'patient_id': doc.get('patient_id'),
            'is_emergency': doc.get('is_emergency', False),
            'patient_history': doc.get('patient_history', '')
        }
        for doc in barcode_details_docs
    }
    
    # Get all patient IDs from barcode details
    barcode_patient_ids = [info['patient_id'] for info in barcode_details_dict.values() if info['patient_id']]
    
    # Fetch all Patient records
    patients_dict = {
        p.patient_id: {'name': p.patientname, 'age': p.age}
        for p in Patient.objects.filter(patient_id__in=barcode_patient_ids).only('patient_id', 'patientname', 'age')
    }
    
    # 2. NEW: Fetch patient details from Hmsbarcode (PRIMARY SOURCE)
    hms_barcodes_dict = {
        b.barcode: {
            'patient_id': b.patient_id,
            'name': b.patientname,
            'age': b.age,
            'billnumber': b.billnumber,
            'gender': b.gender,
            'ref_doctor': b.ref_doctor,
            'testdetails': b.testdetails
        }
        for b in Hmsbarcode.objects.filter(barcode__in=barcodes).only(
            'barcode', 'patient_id', 'patientname', 'age', 'billnumber', 'gender', 'ref_doctor', 'testdetails'
        )
    }
    
    # 3. MongoDB franchise - bulk fetch (fallback)
    mongo_billing_docs = list(billing_col.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "_id": 0}
    ))
    mongo_billing_dict = {doc['barcode']: doc.get('patient_id') for doc in mongo_billing_docs}
    
    mongo_patient_ids = list(mongo_billing_dict.values())
    mongo_patient_docs = list(patient_col.find(
        {"patient_id": {"$in": mongo_patient_ids}},
        {"patient_id": 1, "patientname": 1, "age": 1, "_id": 0}
    ))
    mongo_patient_dict = {
        doc['patient_id']: {'name': doc.get('patientname', 'N/A'), 'age': doc.get('age', 'N/A')}
        for doc in mongo_patient_docs
    }
    
    # 4. Corporate - bulk fetch (fallback)
    corp_billing_docs = list(billing_collection.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "employee_id": 1, "_id": 0}
    ))
    corp_billing_dict = {doc['barcode']: doc.get('employee_id') for doc in corp_billing_docs}
    
    corp_employee_ids = list(corp_billing_dict.values())
    corp_employee_docs = list(patient_collection.find(
        {"employee_id": {"$in": corp_employee_ids}},
        {"employee_id": 1, "employee_name": 1, "age": 1, "_id": 0}
    ))
    corp_employee_dict = {
        doc['employee_id']: {'name': doc.get('employee_name', 'N/A'), 'age': doc.get('age', 'N/A')}
        for doc in corp_employee_docs
    }
    
    # NEW: Collect all unique test_ids from all patients
    all_test_ids = set()
    for patient in patients_list:
        try:
            test_details = json.loads(patient.testdetails) if isinstance(patient.testdetails, str) else patient.testdetails
            for test in test_details:
                if test.get('test_id'):
                    all_test_ids.add(test['test_id'])
        except (json.JSONDecodeError, TypeError):
            continue
    
    # NEW: Bulk fetch test details from MongoDB core_testdetails
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
    
    # Create lookup dictionary: test_id -> test metadata
    test_metadata_dict = {doc['test_id']: doc for doc in test_metadata_docs}
    
    # Helper function to get parameter details from test metadata
    def get_parameter_details(test_id, test_code, device_id=None, param_index=None):
        """Get parameter details from MongoDB test metadata by test_code and index"""
        test_meta = test_metadata_dict.get(test_id)
        if not test_meta:
            return None
        
        # Get parameters - it can be either a dict or a list
        params_data = test_meta.get('parameters', [])
        
        # Case 1: parameters is a dictionary with device_id keys
        if isinstance(params_data, dict):
            # If device_id is provided and exists in parameters, use it
            if device_id and device_id in params_data:
                params_list = params_data[device_id]
            else:
                # Get first device's parameters
                device_ids = test_meta.get('device_id', [])
                if device_ids and len(device_ids) > 0:
                    first_device = device_ids[0]
                    params_list = params_data.get(first_device, [])
                else:
                    # No device_id found, try to get first value from dict
                    params_list = next(iter(params_data.values())) if params_data else []
        
        # Case 2: parameters is a list (current case for PT test)
        elif isinstance(params_data, list):
            params_list = params_data
        
        else:
            return None
        
        # Ensure params_list is actually a list
        if not isinstance(params_list, list):
            params_list = []
        
        # If param_index is provided, use it directly (faster and more accurate)
        if param_index is not None and 0 <= param_index < len(params_list):
            param = params_list[param_index]
            return {
                'parameter_name': param.get('test_name'),
                'unit': param.get('unit'),
                'reference_range': param.get('reference_range'),
                'method': param.get('method'),
                'department': param.get('department') or test_meta.get('department'),
                'sub_title': param.get('sub_title', ''),
                'value_option': param.get('value_option', [])
            }
        
        # Fallback: Find matching parameter by test_code (may not be unique!)
        matching_params = [p for p in params_list if isinstance(p, dict) and p.get('test_code') == test_code]
        
        if matching_params:
            # If multiple params have same test_code, return first one
            # (This is a limitation - ideally use param_index)
            param = matching_params[0]
            return {
                'parameter_name': param.get('test_name'),
                'unit': param.get('unit'),
                'reference_range': param.get('reference_range'),
                'method': param.get('method'),
                'department': param.get('department') or test_meta.get('department'),
                'sub_title': param.get('sub_title', ''),
                'value_option': param.get('value_option', [])
            }
        
        return None
    
    # ============================================
    # PROCESS PATIENTS USING PRE-FETCHED DATA
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
        
        # Initialize variables
        patient_name, patient_age, current_patient_id = "N/A", "N/A", None
        is_emergency = False
        patient_history = ""
        
        # PRIORITY 1: Get from Hmsbarcode (PRIMARY SOURCE)
        if barcode_val in hms_barcodes_dict:
            hms_info = hms_barcodes_dict[barcode_val]
            current_patient_id = hms_info['patient_id']
            patient_name = hms_info['name']
            patient_age = hms_info['age']
        
        # PRIORITY 2: BarcodeTestDetails lookup (includes emergency status and history)
        if barcode_val in barcode_details_dict:
            barcode_info = barcode_details_dict[barcode_val]
            if not current_patient_id:
                current_patient_id = barcode_info['patient_id']
            is_emergency = barcode_info['is_emergency']
            patient_history = barcode_info['patient_history']
            
            if current_patient_id in patients_dict and patient_name == "N/A":
                patient_info = patients_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age = patient_info['age']
        
        # PRIORITY 3: MongoDB franchise lookup (fallback)
        if not current_patient_id and barcode_val in mongo_billing_dict:
            current_patient_id = mongo_billing_dict[barcode_val]
            if current_patient_id in mongo_patient_dict:
                patient_info = mongo_patient_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age = patient_info['age']
        
        # PRIORITY 4: Corporate lookup (fallback)
        if not current_patient_id and barcode_val in corp_billing_dict:
            current_patient_id = corp_billing_dict[barcode_val]
            if current_patient_id in corp_employee_dict:
                corp_info = corp_employee_dict[current_patient_id]
                patient_name = corp_info['name']
                patient_age = corp_info['age']
        
        # Final fallback
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
        
        # NEW: Enrich test details with metadata from MongoDB
        enriched_test_details = []
        for test in test_details:
            test_id = test.get('test_id')
            test_code = test.get('test_code')
            device_id = test.get('device_id')
            parameters = test.get('parameters', [])
            
            # Get test metadata
            test_meta = test_metadata_dict.get(test_id, {})
            
            # Check if test has parameters array (like CBC with 20 parameters or PT with 4 parameters)
            if parameters and isinstance(parameters, list) and len(parameters) > 0:
                # This is a test with multiple parameters (e.g., CBC, PT)
                # Enrich each parameter with its metadata
                enriched_parameters = []
                
                for param_index, param in enumerate(parameters):
                    param_test_code = param.get('test_code')
                    param_value = param.get('value')
                    param_comment = param.get('comment', '')
                    
                    # Get parameter-specific details from MongoDB using INDEX
                    # This is more reliable than test_code when multiple params share the same test_code
                    param_details = get_parameter_details(test_id, param_test_code, device_id, param_index=param_index)
                    
                    if param_details:
                        enriched_param = {
                            'test_code': param_test_code,
                            'parameter_name': param_details.get('parameter_name'),
                            'value': param_value,
                            'unit': param_details.get('unit', 'N/A'),
                            'reference_range': param_details.get('reference_range', 'N/A'),
                            'method': param_details.get('method', 'N/A'),
                            'department': param_details.get('department', 'N/A'),
                            'sub_title': param_details.get('sub_title', ''),
                            'value_option': param_details.get('value_option', []),
                            'comment': param_comment
                        }
                    else:
                        # No matching parameter found in MongoDB
                        enriched_param = {
                            'test_code': param_test_code,
                            'parameter_name': None,
                            'value': param_value,
                            'unit': 'N/A',
                            'reference_range': 'N/A',
                            'method': 'N/A',
                            'department': 'N/A',
                            'sub_title': '',
                            'value_option': [],
                            'comment': param_comment
                        }
                    
                    enriched_parameters.append(enriched_param)
                
                # Create enriched test with parameters
                enriched_test = {
                    **test,  # Keep all existing fields
                    'test_name': test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type': test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department': test_meta.get('department', test.get('department', 'N/A')),
                    'parameters': enriched_parameters  # Replace with enriched parameters
                }
                
            else:
                # This is a single test WITHOUT parameters array
                # For these tests, unit/method/reference_range are at the TEST LEVEL, not in parameters
                enriched_test = {
                    **test,  # Keep all existing fields from core_testvalue
                    'test_name': test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type': test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department': test_meta.get('department', test.get('department', 'N/A')),
                    # FIXED: Get unit, method, reference_range from test-level fields
                    'unit': test_meta.get('unit', test.get('unit', 'N/A')),
                    'method': test_meta.get('method', test.get('method', 'N/A')),
                    'reference_range': test_meta.get('reference_range', test.get('reference_range', 'N/A')),
                }
                
                # Only if test_code exists AND parameters array exists in metadata, try to get parameter-specific details
                # This handles edge cases where a test might have optional parameters
                if test_code and test_code != 'N/A' and test_meta.get('parameters'):
                    param_details = get_parameter_details(test_id, test_code, device_id)
                    
                    if param_details:
                        # Override ONLY if parameter details are found
                        # This handles cases where parameters array exists but is optional
                        enriched_test.update({
                            'parameter_name': param_details.get('parameter_name'),
                            'unit': param_details.get('unit', enriched_test['unit']),
                            'reference_range': param_details.get('reference_range', enriched_test['reference_range']),
                            'method': param_details.get('method', enriched_test['method']),
                            'department': param_details.get('department', enriched_test['department']),
                        })
            
            enriched_test_details.append(enriched_test)
        
        # Replace test_details with enriched version
        test_details = enriched_test_details
        
        # Testname filtering
        if filter_normalized:
            filtered_test_details = []
            for test in test_details:
                test_name = test.get('test_name', '')
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
            "patient_id": current_patient_id,
            "patientname": patient_name,
            "age": patient_age,
            "locationId": patient.locationId,
            "barcode": barcode_val,
            "date": patient.date,
            "created_date": patient.created_date,
            "is_emergency": is_emergency,
            "patient_history": patient_history,
            "testdetails": filtered_tests
        })
    
    return JsonResponse(patient_data, safe=False)
def normalize_testname(name):
    """Helper function for test name normalization"""
    if not name:
        return ""
    normalized = re.sub(r'\s+', ' ', name.strip().lower())
    normalized = normalized.replace('&', 'and')
    normalized = normalized.replace('/', ' ')
    return normalized



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
        approve_time = update_data.get("approve_time")  # Get from frontend
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
            test_detail["approve"] = update_data.get("approve", False)
            if test_detail["approve"]:
                # Use approve_time from frontend if provided
                if approve_time:
                    test_detail["approve_time"] = approve_time
                if "approve_by" in update_data:
                    test_detail["approve_by"] = update_data["approve_by"]
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