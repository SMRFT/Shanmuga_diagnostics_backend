from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from ...models import HmspatientBilling
from ...serializers import HmspatientBillingRegistrationSerializer
from django.db.models import Max
import re
from django.utils import timezone
from pyauth.auth import HasRoleAndDataPermission
from datetime import datetime, timedelta
import json
import os
from pymongo import MongoClient
from django.views.decorators.csrf import csrf_exempt
from django.utils.dateparse import parse_datetime
from django.forms.models import model_to_dict



@api_view(["GET"])
@permission_classes([HasRoleAndDataPermission])
def hms_get_test_details(request):
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_testdetails

        if request.method == 'GET':
            approved_tests = list(collection.find(
                {"status": "Approved", "is_active": True},
                {"_id": 0}
            ))

            # Normalize field names for frontend
            formatted = [
                {
                    "test_id": t.get("test_id", ""),
                    "testname": t.get("test_name", ""),
                    "container": t.get("collection_container", ""),
                    "SH_Rate": t.get("SH_Rate", 0),
                    "department": t.get("department", ""),
                    'shortcut': t.get('shortcut', ''),
                }
                for t in approved_tests
            ]

            return JsonResponse({"success": True, "data": formatted}, status=200)

        return JsonResponse({"success": False, "message": "Method not allowed"}, status=405)

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@api_view(["POST"])
@permission_classes([HasRoleAndDataPermission])
def hms_patient_billing(request):
    try:
        serializer = HmspatientBillingRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({"success": True, "data": serializer.data}, status=201)
        return Response({"success": False, "errors": serializer.errors}, status=400)
    except Exception as e:
        return Response({"success": False, "error": str(e)}, status=500)
    

@api_view(["GET"])
@permission_classes([HasRoleAndDataPermission])
def hms_get_doctor_list(request):
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Diagnostics"]
    collection = db["core_doctorlist"]

    doctors = list(collection.find({"is_active": True}, {"doctor_name": 1, "department": 1}))
    
    # convert ObjectId to string so it’s JSON serializable
    for d in doctors:
        d["_id"] = str(d["_id"])

    return JsonResponse(doctors, safe=False)


