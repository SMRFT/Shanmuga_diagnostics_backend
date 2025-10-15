from rest_framework.response import Response
from django.http import JsonResponse , HttpResponse
from datetime import datetime
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from urllib.parse import quote_plus
from pymongo import MongoClient
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from django.utils import timezone  # Import Django's timezone module
import re
from django.core.mail import EmailMessage
from django.conf import settings  
from django.utils.timezone import make_aware
from datetime import datetime, date  # Import `date` separately
import pytz
from rest_framework.views import APIView
import traceback
from django.conf import settings  # To access the settings for DEFAULT_FROM_EMAIL
import json
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ..models import Patient,Hmssamplestatus
from ..models import SampleStatus
from ..models import TestValue
from ..models import SampleStatus
from ..models import BarcodeTestDetails
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ..models import SampleStatus, TestValue, Hmssamplestatus, Hmsbarcode, HmspatientBilling
from ..serializers import SampleStatusSerializer
from ..serializers import TestValueSerializer
import os
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()





BATCH_SIZE = 5000  # split large $in queries


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_corporate_samplestatus(request):
    try:
        # ------------------------
        # Date range parsing
        # ------------------------
        from_date_str = request.query_params.get('from_date')
        to_date_str = request.query_params.get('to_date')
        date_str = request.query_params.get('date')

        if from_date_str and to_date_str:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d').date()
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()
            if from_date > to_date:
                return Response({"error": "From date cannot be after to date."}, status=status.HTTP_400_BAD_REQUEST)
            start_of_range = datetime.combine(from_date, datetime.min.time())
            end_of_range = datetime.combine(to_date, datetime.max.time())
        elif date_str:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_of_range = datetime.combine(selected_date, datetime.min.time())
            end_of_range = start_of_range + timedelta(days=1)
        else:
            return Response({"error": "Either 'date' or both 'from_date' and 'to_date' are required."},
                            status=status.HTTP_400_BAD_REQUEST)

        # ------------------------
        # Helper function
        # ------------------------
        def safe_datetime_to_string(dt_obj):
            if not dt_obj:
                return None
            if isinstance(dt_obj, str):
                return dt_obj
            if hasattr(dt_obj, "isoformat"):
                return dt_obj.isoformat()
            return str(dt_obj)

        combined_results = []

        # ------------------------
        # MongoDB query
        # ------------------------
        client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
        corp = client.Corporatehealthcheckup
        sample_collection = corp.core_sample
        billing_collection = corp.core_billing
        patient_collection = corp.core_employeeregistration

        # Fetch samples in range
        chc_query = {"created_date": {"$gte": start_of_range, "$lt": end_of_range}}
        chc_samples = list(sample_collection.find(chc_query, projection={
            "_id": 1, "barcode": 1, "testdetails": 1,
            "created_by": 1, "created_date": 1,
            "lastmodified_by": 1, "lastmodified_date": 1,
            "company_id": 1, "date": 1
        }).sort("created_date", -1))

        processed_barcodes = {}
        all_barcodes = []

        for record in chc_samples:
            barcode = record.get("barcode")
            if not barcode:
                continue
            if barcode in processed_barcodes and record.get("created_date") <= processed_barcodes[barcode]["created_date"]:
                continue
            try:
                testdetails = record["testdetails"] if isinstance(record["testdetails"], list) else json.loads(record["testdetails"])
            except Exception:
                continue
            filtered_tests = [t for t in testdetails if t.get("samplestatus") in ["Received", "Outsource"]]
            if not filtered_tests:
                continue
            processed_barcodes[barcode] = {
                "created_date": record.get("created_date"),
                "data": record,
                "filtered_tests": filtered_tests
            }
            all_barcodes.append(barcode)

        # ------------------------
        # Bulk fetch billings
        # ------------------------
        billings = {b["barcode"]: b for b in billing_collection.find({"barcode": {"$in": all_barcodes}})}

        # Collect all employee_ids and ensure string type
        all_employee_ids = [str(b.get("employee_id")) for b in billings.values() if b.get("employee_id")]

        # ------------------------
        # Batch fetch patients
        # ------------------------
        patients = {}
        for i in range(0, len(all_employee_ids), BATCH_SIZE):
            batch_ids = all_employee_ids[i:i + BATCH_SIZE]
            cursor = patient_collection.find({"employee_id": {"$in": batch_ids}})
            for p in cursor:
                patients[str(p["employee_id"])] = p

        # ------------------------
        # Combine results
        # ------------------------
        for barcode, info in processed_barcodes.items():
            record = info["data"]
            filtered_tests = info["filtered_tests"]
            billing = billings.get(barcode, {})
            patient_id = billing.get("employee_id")
            patient = patients.get(str(patient_id), {})

            patient_name = patient.get("employee_name", "Unknown Patient")
            age = patient.get("age", "Unknown")
            gender = patient.get("gender", "Unknown")

            # Fetch test values
            all_test_values = TestValue.objects.filter(barcode=barcode).order_by("-created_date", "-lastmodified_date")

            updated_tests = []
            for test in filtered_tests:
                test_name = test.get("testname", "").strip().lower()
                test_code = test.get("testcode", test_name).strip().lower()
                test.update({"rerun": False, "approve": False, "test_value_exists": False,
                             "approve_time": None, "rerun_time": None, "approve_by": None})
                matching_value = None
                for tv in all_test_values:
                    try:
                        tv_details = json.loads(tv.testdetails) if isinstance(tv.testdetails, str) else tv.testdetails
                    except Exception:
                        tv_details = []
                    for tv_test in tv_details:
                        tv_test_name = tv_test.get("testname", "").strip().lower()
                        tv_test_code = tv_test.get("testcode", tv_test_name).strip().lower()
                        if tv_test_name == test_name or tv_test_code == test_code:
                            matching_value = tv_test
                            break
                    if matching_value:
                        break
                if matching_value:
                    test.update({"test_value_exists": True,
                                 "approve": bool(matching_value.get("approve", False)),
                                 "rerun": bool(matching_value.get("rerun", False)),
                                 "approve_time": matching_value.get("approve_time"),
                                 "rerun_time": matching_value.get("rerun_time"),
                                 "approve_by": matching_value.get("approve_by")})
                updated_tests.append(test)

            sample_dict = {
                "id": str(record.get("_id")),
                "created_by": record.get("created_by", ""),
                "created_date": safe_datetime_to_string(record.get("created_date")),
                "lastmodified_by": record.get("lastmodified_by", ""),
                "lastmodified_date": safe_datetime_to_string(record.get("lastmodified_date")),
                "barcode": barcode,
                "company_id": record.get("company_id", ""),
                "patient_id": patient_id or "Unknown ID",
                "patientname": patient_name,
                "date": safe_datetime_to_string(record.get("date")),
                "age": age,
                "gender": gender,
                "testdetails": updated_tests,
                "data_source": "chc_mongodb",
            }

            combined_results.append(sample_dict)

        client.close()

        # ------------------------
        # Sort by date desc
        # ------------------------
        combined_results.sort(key=lambda x: x.get("date") or "", reverse=True)
        return Response(combined_results, status=status.HTTP_200_OK)

    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.http import JsonResponse
from pymongo import MongoClient
import os, json


from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from pymongo import MongoClient
from bson import ObjectId
import json
import os
from datetime import datetime

# 👇 Permission import (adjust if needed)



def process_corporate_test_data(
    test_list,
    sample_status_map,
    patient_id,
    patient_name,
    barcode,
    device_id,
    core_testdetails_collection,
    interface_testvalue_collection,
):
    """
    Process Corporate test data.
    """
    final_test_data = []
    processed_records = []

    def to_iso_or_str(dt):
        if not dt:
            return None
        if hasattr(dt, "isoformat"):
            try:
                return dt.isoformat()
            except Exception:
                return str(dt)
        if isinstance(dt, str):
            return dt
        return str(dt)

    def normalize_parameters(parameters):
        if parameters is None:
            return {}
        if isinstance(parameters, dict):
            normalized = {}
            for k, v in parameters.items():
                if v is None:
                    normalized[str(k)] = []
                elif isinstance(v, list):
                    normalized[str(k)] = v
                elif isinstance(v, dict):
                    normalized[str(k)] = [v]
                else:
                    normalized[str(k)] = [v] if v is not None else []
            return normalized
        if isinstance(parameters, list):
            return {"default": parameters}
        return {}

    for test in test_list:
        test_name = test.get("testname")
        if not test_name:
            continue

        # --- Sample status ---
        sample_status_info = sample_status_map.get(
            test_name, {"status": "Unknown", "source": "none"}
        )
        sample_status = sample_status_info.get("status", "Unknown")
        data_source = sample_status_info.get("source", "corporate_mongodb")

        # --- Get test details ---
        test_detail = core_testdetails_collection.find_one({"test_name": test_name})
        if not test_detail:
            continue

        test_id = test_detail.get("test_id")
        raw_parameters = test_detail.get("parameters", {})
        parameters = normalize_parameters(raw_parameters)

        # --- If no parameters ---
        if not parameters:
            test_code = test_detail.get(
                "test_code", f"{(test_name or '').replace(' ', '').upper()}01"
            )
            query = {
                "Barcode": barcode,
                "TestCode": test_code,
                "processingstatus": "pending",
            }
            if device_id:
                query["DeviceID"] = device_id

            record = interface_testvalue_collection.find_one(
                query, sort=[("Receiveddate", -1)]
            )

            test_value = record.get("Value", "") if record else ""
            processing_status = record.get("processingstatus", "N/A") if record else "N/A"
            created_date = to_iso_or_str(record.get("CreatedDate") if record else None)
            received_date = to_iso_or_str(record.get("Receiveddate") if record else None)

            if record:
                processed_records.append(
                    {
                        "barcode": barcode,
                        "test_code": test_code,
                        "device_id": record.get("DeviceID"),
                        "record_id": str(record.get("_id")),
                        "data_source_type": "corporate",
                    }
                )

            test_info = {
                "patient_id": patient_id,
                "patientname": patient_name,
                "barcode": barcode,
                "device_id": record.get("DeviceID", "N/A") if record else "N/A",
                "test_id": test_id,
                "testname": test_name,
                "test_code": test_code,
                "unit": test_detail.get("unit", "N/A"),
                "reference_range": test_detail.get("reference_range", "N/A"),
                "method": test_detail.get("method", "N/A"),
                "department": test_detail.get("department", "N/A"),
                "specimen_type": test_detail.get("specimen_type", "N/A"),
                "NABL": test_detail.get("NABL", "N/A"),
                "test_value": test_value,
                "processing_status": processing_status,
                "sample_status": sample_status,
                "data_source": data_source,
                "data_source_type": "corporate",
                "lab_unique_id": record.get("lab_unique_id", "N/A") if record else "N/A",
                "created_date": created_date,
                "received_date": received_date,
            }
            final_test_data.append(test_info)

        # --- If parameters exist ---
        else:
            for device_key, param_list in parameters.items():
                for param in param_list:
                    test_code = param.get("test_code")
                    if not test_code:
                        continue

                    record = interface_testvalue_collection.find_one(
                        {
                            "Barcode": barcode,
                            "TestCode": test_code,
                            "processingstatus": "pending",
                        },
                        sort=[("Receiveddate", -1)],
                    )

                    test_value = record.get("Value", "") if record else ""
                    processing_status = (
                        record.get("processingstatus", "No Data") if record else "No Data"
                    )
                    created_date = to_iso_or_str(record.get("CreatedDate") if record else None)
                    received_date = to_iso_or_str(record.get("Receiveddate") if record else None)

                    if record:
                        processed_records.append(
                            {
                                "barcode": barcode,
                                "test_code": test_code,
                                "device_id": record.get("DeviceID"),
                                "record_id": str(record.get("_id")),
                                "data_source_type": "corporate",
                            }
                        )

                    test_info = {
                        "patient_id": patient_id,
                        "patientname": patient_name,
                        "barcode": barcode,
                        "device_id": record.get("DeviceID", "N/A") if record else "N/A",
                        "test_id": test_id,
                        "testname": test_name,
                        "test_code": test_code,
                        "parameter_name": param.get("test_name"),
                        "unit": param.get("unit"),
                        "reference_range": param.get("reference_range"),
                        "method": param.get("method"),
                        "department": test_detail.get("department"),
                        "specimen_type": test_detail.get(
                            "specimen_type", param.get("specimen_type")
                        ),
                        "NABL": test_detail.get("NABL", "N/A"),
                        "test_value": test_value,
                        "processing_status": processing_status,
                        "sample_status": sample_status,
                        "data_source": data_source,
                        "data_source_type": "corporate",
                        "lab_unique_id": record.get("lab_unique_id", "N/A") if record else "N/A",
                        "created_date": created_date,
                        "received_date": received_date,
                    }
                    final_test_data.append(test_info)

    return {"test_data": final_test_data, "processed_records": processed_records}


@api_view(["GET"])
@permission_classes([HasRoleAndDataPermission])
def corporate_test_details(request):
    """
    Corporate Health Checkup test details for given barcode & optional test_name filter
    """
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    db = client.Diagnostics
    core_testdetails_collection = db.core_testdetails
    interface_testvalue_collection = db.interface_testvalue

    corporate_db = client.Corporatehealthcheckup
    billing_collection = corporate_db.core_billing
    sample_collection = corporate_db.core_sample

    barcode = request.GET.get("barcode")
    device_id = request.GET.get("device_id")
    test_name_filter = request.GET.get("test_name")  # 👈

    if not barcode:
        return JsonResponse({"error": "Barcode parameter is required"}, status=400)

    corporate_patient_id = None
    corporate_patient_name = None
    corporate_test_list = []
    corporate_sample_status_map = {}

    # Billing record
    billing_record = billing_collection.find_one({"barcode": barcode})
    if billing_record:
        corporate_patient_id = billing_record.get("patient_id")
        corporate_patient_name = billing_record.get("patientname")
        testdetails = billing_record.get("testdetails", [])
        if isinstance(testdetails, str):
            try:
                corporate_test_list = json.loads(testdetails)
            except json.JSONDecodeError:
                corporate_test_list = []
        elif isinstance(testdetails, list):
            corporate_test_list = testdetails

    # Sample status record
    sample_status_detail = sample_collection.find_one({"barcode": barcode})
    if sample_status_detail:
        sample_testdetails = sample_status_detail.get("testdetails", [])
        if isinstance(sample_testdetails, str):
            sample_testdetails = json.loads(sample_testdetails)
        for test in sample_testdetails:
            test_name = test.get("testname")
            sample_status = test.get("samplestatus")
            if test_name:
                corporate_sample_status_map[test_name] = {
                    "status": sample_status,
                    "source": "corporate_mongodb",
                }

    if not corporate_test_list:
        return JsonResponse(
            {"error": f"No corporate data found for barcode: {barcode}"}, status=404
        )

    # If test_name filter provided, reduce the list early
    if test_name_filter:
        corporate_test_list = [
            test for test in corporate_test_list
            if str(test.get("testname", "")).strip().lower() == test_name_filter.strip().lower()
        ]
        if not corporate_test_list:
            return JsonResponse(
                {"error": f"Test '{test_name_filter}' not found for barcode {barcode}"},
                status=404
            )

    # Process all (but possibly filtered) test list
    corporate_test_data = process_corporate_test_data(
        corporate_test_list,
        corporate_sample_status_map,
        corporate_patient_id,
        corporate_patient_name,
        barcode,
        device_id,
        core_testdetails_collection,
        interface_testvalue_collection,
    )

    final_data = corporate_test_data["test_data"]

    # ✅ Post-filter processed data also, to ensure only that testname is returned
    if test_name_filter:
        final_data = [
            row for row in final_data
            if str(row.get("testname", "")).strip().lower() == test_name_filter.strip().lower()
        ]

    response_data = {
        "success": True,
        "patient_info": {
            "patient_id": corporate_patient_id,
            "patient_name": corporate_patient_name,
            "barcode": barcode,
        },
        "test_count": len(final_data),
        "data": final_data,  # 👈 only the requested test data
    }

    return Response(response_data, status=200)
