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
from urllib.parse import unquote_plus
import re


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_test_values(request):
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    corp = client.Corporatehealthcheckup
    billing_collection = corp.core_billing
    patient_collection = corp.core_employeeregistration

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    patient_id_filter = request.GET.get('patient_id')
    testname_filter = request.GET.get('testname')

    # 🟢 1️⃣ Pre-filter Django data in one query
    patients = TestValue.objects.all().only('barcode', 'testdetails', 'date', 'created_date')
    if from_date and to_date:
        try:
            parsed_from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
            parsed_to_date = datetime.strptime(to_date, '%Y-%m-%d').date()
            patients = patients.filter(date__gte=parsed_from_date, date__lte=parsed_to_date)
        except ValueError:
            return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)
    patients = patients.order_by('-date')

    # 🟢 2️⃣ Collect all barcodes to batch MongoDB lookups
    barcode_list = [str(p.barcode).zfill(5) for p in patients if p.barcode]
    barcode_to_patient = {}

    if barcode_list:
        mongo_docs = list(billing_collection.find({"barcode": {"$in": barcode_list}}, {"barcode": 1, "employee_id": 1}))
        emp_ids = [doc.get("employee_id") for doc in mongo_docs if doc.get("employee_id")]
        emp_docs = list(patient_collection.find({"employee_id": {"$in": emp_ids}}, {"employee_id": 1, "employee_name": 1, "age": 1}))

        emp_map = {emp["employee_id"]: emp for emp in emp_docs}
        for doc in mongo_docs:
            emp_id = doc.get("employee_id")
            if emp_id in emp_map:
                barcode_to_patient[str(doc["barcode"])] = {
                    "patient_id": emp_id,
                    "patientname": emp_map[emp_id].get("employee_name", "N/A"),
                    "age": emp_map[emp_id].get("age", "N/A")
                }

    # 🟢 3️⃣ Process testname filter once
    def normalize_testname(name):
        if not name:
            return ""
        normalized = re.sub(r'\s+', ' ', name.strip().lower())
        normalized = normalized.replace('&', 'and').replace('/', ' ')
        return normalized

    filter_normalized = normalize_testname(testname_filter) if testname_filter and testname_filter != 'undefined' else None

    patient_data = []

    # 🟢 4️⃣ Iterate with pre-fetched Mongo data
    for patient in patients:
        barcode_val = str(patient.barcode).zfill(5)
        lookup = barcode_to_patient.get(barcode_val, {})
        current_patient_id = lookup.get("patient_id") or getattr(patient, 'patient_id', None)

        # Skip if patient_id filter doesn’t match
        if patient_id_filter and current_patient_id != patient_id_filter:
            continue

        patient_name = lookup.get("patientname", "N/A")
        patient_age = lookup.get("age", "N/A")

        # Parse testdetails once
        try:
            test_details = json.loads(patient.testdetails) if isinstance(patient.testdetails, str) else patient.testdetails
        except (json.JSONDecodeError, TypeError):
            test_details = []

        # Testname filter
        if filter_normalized:
            filtered_test_details = []
            for test in test_details:
                test_name = normalize_testname(test.get('testname', ''))
                if (
                    test_name == filter_normalized or
                    filter_normalized in test_name or
                    all(word in test_name for word in filter_normalized.split() if len(word) > 2)
                ):
                    filtered_test_details.append(test)
            test_details = filtered_test_details

        # Only pending tests
        filtered_tests = [t for t in test_details if not t.get('approve', False) and not t.get('rerun', False)]
        if not filtered_tests:
            continue

        patient_data.append({
            "patient_id": current_patient_id,
            "patientname": patient_name,
            "age": patient_age,
            "barcode": barcode_val,
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
