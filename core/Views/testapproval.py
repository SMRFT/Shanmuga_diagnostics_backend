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
from ..models import TestValue,Patient,BarcodeTestDetails,Hmssamplestatus,Hmsbarcode,HmspatientBilling
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

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    patient_id_filter = request.GET.get('patient_id')
    testname_filter = request.GET.get('testname')
    emergency_filter = request.GET.get('emergency')  # NEW: emergency filter

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
    
    # Convert to list to avoid repeated queries
    patients_list = list(patients)
    
    if not patients_list:
        return JsonResponse([], safe=False)

    # Extract all barcodes upfront
    barcodes = [str(p.barcode).zfill(0) for p in patients_list]
    
    # ============================================
    # BULK FETCH ALL DATA SOURCES AT ONCE
    # ============================================
    
    # 1. Fetch all BarcodeTestDetails in one query WITH is_emergency and patient_history
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
    
    # Fetch all Patient records in one query
    patients_dict = {
        p.patient_id: {'name': p.patientname, 'age': p.age}
        for p in Patient.objects.filter(patient_id__in=barcode_patient_ids).only('patient_id', 'patientname', 'age')
    }
    
    # 2. MongoDB franchise - bulk fetch
    mongo_billing_docs = list(billing_col.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "_id": 0}
    ))
    mongo_billing_dict = {doc['barcode']: doc.get('patient_id') for doc in mongo_billing_docs}
    
    # Get patient IDs for MongoDB patient lookup
    mongo_patient_ids = list(mongo_billing_dict.values())
    mongo_patient_docs = list(patient_col.find(
        {"patient_id": {"$in": mongo_patient_ids}},
        {"patient_id": 1, "patientname": 1, "age": 1, "_id": 0}
    ))
    mongo_patient_dict = {
        doc['patient_id']: {'name': doc.get('patientname', 'N/A'), 'age': doc.get('age', 'N/A')}
        for doc in mongo_patient_docs
    }
    
    # 3. HMS Billing - bulk fetch
    hms_barcodes_dict = {
        b.barcode: b.billnumber
        for b in Hmsbarcode.objects.filter(barcode__in=barcodes).only('barcode', 'billnumber')
    }
    
    hms_billnumbers = list(hms_barcodes_dict.values())
    hms_billing_dict = {
        hb.billnumber: {
            'patient_id': hb.patient_id,
            'name': hb.patientname,
            'age': hb.age
        }
        for hb in HmspatientBilling.objects.filter(billnumber__in=hms_billnumbers).only(
            'billnumber', 'patient_id', 'patientname', 'age'
        )
    }
    
    # HMS Sample status backup
    hms_samples_dict = {
        s.barcode: s.barcode
        for s in Hmssamplestatus.objects.filter(barcode__in=barcodes).only('barcode')
    }
    
    # Additional HMS billing lookup for samples
    sample_billnumbers = list(hms_samples_dict.values())
    hms_sample_billing_dict = {
        hb.billnumber: {
            'patient_id': hb.patient_id,
            'name': hb.patientname,
            'age': hb.age
        }
        for hb in HmspatientBilling.objects.filter(billnumber__in=sample_billnumbers).only(
            'billnumber', 'patient_id', 'patientname', 'age'
        )
    }
    
    # 4. Corporate - bulk fetch
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
    
    # ============================================
    # PROCESS PATIENTS USING PRE-FETCHED DATA
    # ============================================
    
    # Process testname filter once
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
        
        # Lookup patient info from pre-fetched data (no DB queries here!)
        patient_name, patient_age, current_patient_id = "N/A", "N/A", None
        is_emergency = False
        patient_history = ""
        
        # 1. BarcodeTestDetails lookup (includes emergency status and history)
        if barcode_val in barcode_details_dict:
            barcode_info = barcode_details_dict[barcode_val]
            current_patient_id = barcode_info['patient_id']
            is_emergency = barcode_info['is_emergency']
            patient_history = barcode_info['patient_history']
            
            if current_patient_id in patients_dict:
                patient_info = patients_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age = patient_info['age']
        
        # 2. MongoDB franchise lookup
        if not current_patient_id and barcode_val in mongo_billing_dict:
            current_patient_id = mongo_billing_dict[barcode_val]
            if current_patient_id in mongo_patient_dict:
                patient_info = mongo_patient_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age = patient_info['age']
        
        # 3. HMS Billing lookup
        if not current_patient_id and barcode_val in hms_barcodes_dict:
            billnumber = hms_barcodes_dict[barcode_val]
            if billnumber in hms_billing_dict:
                hms_info = hms_billing_dict[billnumber]
                current_patient_id = hms_info['patient_id']
                patient_name = hms_info['name']
                patient_age = hms_info['age']
        
        # HMS Sample fallback
        if not current_patient_id and barcode_val in hms_samples_dict:
            sample_barcode = hms_samples_dict[barcode_val]
            if sample_barcode in hms_sample_billing_dict:
                hms_info = hms_sample_billing_dict[sample_barcode]
                current_patient_id = hms_info['patient_id']
                patient_name = hms_info['name']
                patient_age = hms_info['age']
        
        # 4. Corporate lookup
        if not current_patient_id and barcode_val in corp_billing_dict:
            current_patient_id = corp_billing_dict[barcode_val]
            if current_patient_id in corp_employee_dict:
                corp_info = corp_employee_dict[current_patient_id]
                patient_name = corp_info['name']
                patient_age = corp_info['age']
        
        # 5. Final fallback
        if not current_patient_id:
            current_patient_id = getattr(patient, 'patient_id', None)
        
        # Patient ID filter
        if patient_id_filter and current_patient_id != patient_id_filter:
            continue
        
        # Emergency filter (NEW)
        if emergency_filter:
            if emergency_filter == 'emergency' and not is_emergency:
                continue
            elif emergency_filter == 'normal' and is_emergency:
                continue
        
        # Testname filtering
        if filter_normalized:
            filtered_test_details = []
            for test in test_details:
                test_name = test.get('testname', '')
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
def rerun_test_detail(request, patient_id):
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
                # Use rerun_time from frontend if provided
                if rerun_time:
                    test_detail["rerun_time"] = rerun_time
            test_found = True
            break
    if not test_found:
        return JsonResponse({"error": "Test not found with given test_id."}, status=404)
    result = collection.update_one(
        query,
        {"$set": {"testdetails": json.dumps(test_details)}}
    )
    if result.modified_count > 0:
        return JsonResponse({"message": "Test detail rerun status updated successfully."})
    return JsonResponse({"error": "Failed to update rerun status."}, status=500)
