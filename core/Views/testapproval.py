from datetime import datetime
from rest_framework.decorators import api_view
from urllib.parse import quote_plus
from pymongo import MongoClient
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from django.utils import timezone  # Import Django's timezone module
from django.conf import settings
from django.utils.timezone import make_aware
from datetime import datetime
from django.conf import settings  # To access the settings for DEFAULT_FROM_EMAIL
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
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_test_values(request):
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    db = client.franchise
    billing_col = db.franchise_billing
    patient_col = db.franchise_patient

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    patient_id_filter = request.GET.get('patient_id')
    testname_filter = request.GET.get('testname')

    patients = TestValue.objects.all()

    # Date filters
    if from_date and to_date:
        try:
            parsed_from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
            parsed_to_date = datetime.strptime(to_date, '%Y-%m-%d').date()
            patients = patients.filter(date__gte=parsed_from_date, date__lte=parsed_to_date)
        except ValueError:
            return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

    patients = patients.order_by('-date')

    # Process testname filter
    if testname_filter and testname_filter != 'undefined':
        testname_filter = unquote_plus(testname_filter)

    patient_data = []

    for patient in patients:
        try:
            test_details = json.loads(patient.testdetails) if isinstance(patient.testdetails, str) else patient.testdetails
        except (json.JSONDecodeError, TypeError):
            test_details = []

        # defaults
        patient_name, patient_age, current_patient_id = "N/A", "N/A", None

        # 1️⃣ BarcodeTestDetails (local model)
        barcode_details = BarcodeTestDetails.objects.filter(barcode=str(patient.barcode).zfill(5)).first()
        if barcode_details:
            current_patient_id = barcode_details.patient_id
            patient_record = Patient.objects.filter(patient_id=current_patient_id).first()
            if patient_record:
                patient_name = patient_record.patientname
                patient_age = patient_record.age

        # 2️⃣ MongoDB franchise_billing → franchise_patient
        if not current_patient_id:
            barcode_val = str(patient.barcode).zfill(5)
            billing_doc = billing_col.find_one({"barcode": barcode_val})
            if billing_doc:
                current_patient_id = billing_doc.get("patient_id")
                patient_doc = patient_col.find_one({"patient_id": current_patient_id})
                if patient_doc:
                    patient_name = patient_doc.get("patientname", "N/A")
                    patient_age = patient_doc.get("age", "N/A")

        # 3️⃣ HMS Billing (Django models)
        if not current_patient_id:
            barcode_val = str(patient.barcode).zfill(5)
            # Try barcode mapping
            hms_barcode = Hmsbarcode.objects.filter(barcode=barcode_val).first()
            if hms_barcode:
                hms_billing = HmspatientBilling.objects.filter(billnumber=hms_barcode.billnumber).first()
                if hms_billing:
                    current_patient_id = hms_billing.patient_id
                    patient_name = hms_billing.patientname
                    patient_age = hms_billing.age

            # If not found, try samplestatus
            if not current_patient_id:
                hms_sample = Hmssamplestatus.objects.filter(barcode=barcode_val).first()
                if hms_sample:
                    hms_billing = HmspatientBilling.objects.filter(billnumber=hms_sample.barcode).first()
                    if hms_billing:
                        current_patient_id = hms_billing.patient_id
                        patient_name = hms_billing.patientname
                        patient_age = hms_billing.age

        # 4️⃣ Final fallback
        if not current_patient_id:
            current_patient_id = getattr(patient, 'patient_id', None)

        # Patient ID filter
        if patient_id_filter and current_patient_id != patient_id_filter:
            continue

        # Testname filtering
        if testname_filter and testname_filter != 'undefined':
            def normalize_testname(name):
                if not name:
                    return ""
                normalized = re.sub(r'\s+', ' ', name.strip().lower())
                normalized = normalized.replace('&', 'and')
                normalized = normalized.replace('/', ' ')
                return normalized

            filter_normalized = normalize_testname(testname_filter)
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
            "barcode": str(patient.barcode).zfill(5),
            "date": patient.date,
            "created_date": patient.created_date,
            "testdetails": filtered_tests
        })

    return JsonResponse(patient_data, safe=False)



@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def approve_test_detail(request, patient_id, test_index):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_testvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode")
        created_date_str = update_data.get("created_date")
        if not (barcode and created_date_str):
            return JsonResponse({"error": "barcode and created_date required"}, status=400)
        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
    except Exception:
        return JsonResponse({"error": "Invalid request format."}, status=400)
    # Query with patient_id, barcode, created_date
    query = { "barcode": barcode, "created_date": created_date}
    test_value = collection.find_one(query)
    if not test_value:
        return JsonResponse({"error": "Patient record not found for given barcode & created_date."}, status=404)
    try:
        test_details = json.loads(test_value.get("testdetails", "[]"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to decode test details."}, status=500)
    if 0 <= test_index < len(test_details):
        test_details[test_index]["approve"] = update_data.get("approve", False)
        if test_details[test_index]["approve"]:
            approve_time = timezone.localtime(timezone.now())
            test_details[test_index]["approve_time"] = approve_time.strftime("%Y-%m-%d %H:%M:%S")
            if "approve_by" in update_data:
                test_details[test_index]["approve_by"] = update_data["approve_by"]
        result = collection.update_one(query, {"$set": {"testdetails": json.dumps(test_details)}})
        if result.modified_count > 0:
            return JsonResponse({"message": "Test detail approved successfully."})
        return JsonResponse({"error": "Failed to update test detail."}, status=500)
    return JsonResponse({"error": "Invalid test index."}, status=400)
@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def rerun_test_detail(request, patient_id, test_index):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_testvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode")
        created_date_str = update_data.get("created_date")
        if not (barcode and created_date_str):
            return JsonResponse({"error": "barcode and created_date required"}, status=400)
        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
    except Exception:
        return JsonResponse({"error": "Invalid request format."}, status=400)
    query = { "barcode": barcode, "created_date": created_date}
    test_value = collection.find_one(query)
    if not test_value:
        return JsonResponse({"error": "Patient record not found for given barcode & created_date."}, status=404)
    try:
        test_details = json.loads(test_value.get("testdetails", "[]"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to decode test details."}, status=500)
    if 0 <= test_index < len(test_details):
        test_details[test_index]["rerun"] = update_data.get("rerun", False)
        if test_details[test_index]["rerun"]:
            rerun_time = timezone.localtime(timezone.now())
            test_details[test_index]["rerun_time"] = rerun_time.strftime("%Y-%m-%d %H:%M:%S")
        result = collection.update_one(query, {"$set": {"testdetails": json.dumps(test_details)}})
        if result.modified_count > 0:
            return JsonResponse({"message": "Test detail rerun status updated successfully."})
        return JsonResponse({"error": "Failed to update rerun status."}, status=500)
    return JsonResponse({"error": "Invalid test index."}, status=400)