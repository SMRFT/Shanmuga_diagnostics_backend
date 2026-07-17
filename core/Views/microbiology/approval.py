from rest_framework.response import Response
from django.http import JsonResponse
from datetime import datetime
from rest_framework.decorators import api_view
from rest_framework import  status
from core.mongo_client import get_client
from rest_framework import status
from datetime import datetime, timedelta
from django.conf import settings
from django.utils.timezone import make_aware
from datetime import datetime
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ...models import Patient
from ...models import BarcodeTestDetails
from django.http import JsonResponse
from datetime import datetime, timedelta
import os, json
from django.utils.timezone import make_aware
from ...models import SampleStatus, MBTestValue, Hmssamplestatus, Hmsbarcode,Billing
from dotenv import load_dotenv
load_dotenv()
from urllib.parse import unquote_plus
import re
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from django.utils.dateparse import parse_datetime
import pytz
import gridfs
import logging
from core.pagination import paginate_queryset
# Define IST timezone
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)

logger = logging.getLogger(__name__)


@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def mb_approve_test_detail(request, barcode):
    client = get_client()
    db = client.Diagnostics
    collection = db.core_mbtestvalue
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
def mb_rerun_test_detail(request, barcode):
    client = get_client()
    db = client.Diagnostics
    collection = db.core_mbtestvalue
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
@permission_classes([HasRoleAndDataPermission])
def mb_update_dispatch_status(request, barcode):
    """
    Update dispatch status for a specific test in core_testvalue collection.
    Uses barcode, test_id, and created_date for accurate targeting.
    """
    client = get_client()
    db = client.Diagnostics  # Database name
    collection = db.core_mbtestvalue

    try:
        # Get parameters from request data
        auth_user_id = request.data.get('auth-user-id')
        auth_user_name = request.data.get('auth-user-name')
        test_id = request.data.get('test_id')
        created_date_str = request.data.get('created_date')

        if not auth_user_id:
            return Response(
                {"error": "auth-user-id parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not test_id:
            return Response(
                {"error": "test_id parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not created_date_str:
            return Response(
                {"error": "created_date parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Parse the created_date string to datetime object
        try:
            # Parse ISO format datetime string (e.g., "2026-01-07T07:36:50.214000+00:00")
            created_date = parse_datetime(created_date_str)
            if not created_date:
                # Try alternative parsing if parse_datetime fails
                created_date = datetime.fromisoformat(created_date_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError) as e:
            return Response(
                {"error": f"Invalid created_date format: {created_date_str}. Expected ISO format."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Build the query filter with barcode and created_date
        query_filter = {
            "barcode": barcode,
            "created_date": created_date
        }

        # Find the specific document
        test_value_record = collection.find_one(query_filter)

        if not test_value_record:
            return Response({
                "error": f"No TestValue record found for barcode: {barcode} and created_date: {created_date}",
                "debug_info": {
                    "barcode": barcode,
                    "created_date_str": created_date_str,
                    "parsed_created_date": str(created_date)
                }
            }, status=status.HTTP_404_NOT_FOUND)

        # Parse the testdetails field
        test_details = test_value_record.get("testdetails")

        # Handle both string and list formats
        if isinstance(test_details, str):
            test_details = json.loads(test_details)
        elif not isinstance(test_details, list):
            return Response({
                "error": "Invalid testdetails format"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Find and update the specific test
        test_found = False
        tests_updated = 0

        for test in test_details:
            if test.get("test_id") == test_id:
                test_found = True
                # Only update if not already dispatched
                if not test.get("dispatch", False):
                    test["dispatch"] = True
                    test["dispatched_by"] = auth_user_name
                    test["dispatch_time"] = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
                    tests_updated += 1
                break

        if not test_found:
            return Response({
                "error": f"Test with test_id {test_id} not found in the document"
            }, status=status.HTTP_404_NOT_FOUND)

        if tests_updated == 0:
            return Response({
                "message": f"Test is already dispatched",
                "test_id": test_id,
                "barcode": barcode,
                "created_date": created_date_str
            }, status=status.HTTP_200_OK)

        # Convert the updated testdetails back to a JSON string
        updated_test_details = json.dumps(test_details)

        # Update the document in MongoDB
        result = collection.update_one(
            {"_id": test_value_record["_id"]},
            {"$set": {
                "testdetails": updated_test_details,
                "lastmodified_by": auth_user_id,
                "lastmodified_date": datetime.now(IST)
            }}
        )

        if result.matched_count > 0:
            return Response({
                "message": "Dispatch status updated successfully",
                "barcode": barcode,
                "created_date": created_date_str,
                "test_id": test_id,
                "tests_updated": tests_updated,
                "modified_by": auth_user_id,
                "document_id": str(test_value_record["_id"])
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "Failed to update the document"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    except Exception as e:
        import traceback
        return Response({
            "error": str(e),
            "traceback": traceback.format_exc()
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass
