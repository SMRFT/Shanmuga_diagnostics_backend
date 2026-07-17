from datetime import datetime
from rest_framework.decorators import api_view
from urllib.parse import quote_plus
from core.mongo_client import get_client
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
from ...models import TestValue,Patient,Hmsbarcode
from django.http import JsonResponse
from datetime import datetime
import os, json
from django.utils.timezone import make_aware
from dotenv import load_dotenv
load_dotenv()

from urllib.parse import unquote_plus
import re
from core.pagination import paginate_queryset


@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def approve_test_detail(request, barcode):
    client = get_client()
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
    client = get_client()
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


@api_view(['PATCH'])
def edit_test_value(request, barcode):
    client = get_client()
    collection = client.Diagnostics.core_testvalue

    try:
        data = request.data
        barcode = data.get("barcode")
        created_date_str = data.get("created_date")
        test_id = data.get("test_id")
        new_value = data.get("new_value")
        new_status  = data.get("new_status")
        new_comment = data.get("new_comment")
        history_entry = data.get("history_entry")  # {old_value, edited_by, reason, edited_at}
        param_index = data.get("param_index")       # None for test-level, int for parameter

        if not (barcode and created_date_str and test_id and (new_value or new_status or new_comment) and history_entry):
            return JsonResponse({"error": "barcode, created_date, test_id, and at least one of new_value, new_status, or new_comment are required"}, status=400)

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
                params = detail.get("parameters", [])
                if not (0 <= param_index < len(params)):
                    return JsonResponse({"error": "Invalid param_index."}, status=400)
                if "history" not in params[param_index]:
                    params[param_index]["history"] = []
                params[param_index]["history"].insert(0, history_entry)
                if new_value is not None:
                    params[param_index]["value"] = new_value
                if new_status is not None:
                    params[param_index]["status"] = new_status
                if new_comment is not None:              # ← add this
                    params[param_index]["comment"] = new_comment
                detail["parameters"] = params
            else:
                if "history" not in detail:
                    detail["history"] = []
                detail["history"].insert(0, history_entry)
                if new_value is not None:
                    detail["value"] = new_value
                if new_status is not None:
                    detail["status"] = new_status
                if new_comment is not None:              # ← add this
                    detail["comment"] = new_comment
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
