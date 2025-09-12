from django.http import JsonResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from ..models import SalesVisitLog
from ..serializers import SalesVisitLogSerializer, HospitalLabSerializer
from django.db.models import Max
import re
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
import os
@api_view(['POST'])
# @permission_classes([HasRoleAndDataPermission])
def hospitallabform(request):
    if request.method == 'POST':
        serializer = HospitalLabSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {"success": True, "message": "Hospital/Lab details saved successfully."},
                status=status.HTTP_201_CREATED
            )
        return Response(
            {"success": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )

@csrf_exempt
@api_view(['POST','GET'])
@permission_classes([HasRoleAndDataPermission])
def salesvisitlog(request):
    if request.method == 'POST':
        serializer = SalesVisitLogSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'GET':
        from_date = request.query_params.get('fromDate')
        to_date = request.query_params.get('toDate')
        salesPerson = request.query_params.get('salesPerson')
        query = {}
        # Filter by date range
        if from_date and to_date:
            try:
                from_date_parsed = datetime.strptime(from_date, "%Y-%m-%d")
                to_date_parsed = datetime.strptime(to_date, "%Y-%m-%d")
                query['date__gte'] = from_date_parsed
                query['date__lte'] = to_date_parsed
            except ValueError:
                return Response({"error": "Invalid fromDate or toDate format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        # Filter by salesPerson (case-insensitive)
        if salesPerson:
            query['salesMapping__icontains'] = salesPerson
        logs = SalesVisitLog.objects.filter(**query)
        serializer = SalesVisitLogSerializer(logs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from pymongo import MongoClient
# Connect to MongoDB
client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
db = client["Diagnostics"]
@api_view(['GET'])
# @permission_classes([HasRoleAndDataPermission])
def get_all_clinicalnames(request):
    try:
        # --- Fetch from core_clinicalname ---
        clinical_cursor = db.core_clinicalname.find({}, {
            "_id": 0,
            "clinicalname": 1,
            "type": 1,
            "salesMapping": 1,
            "contactPerson": 1,
            "phone": 1,
            "email": 1
        })
        clinical_list = list(clinical_cursor)
        for item in clinical_list:
            item["hospitalName"] = item.get("clinicalname", "")
            item["contactNumber"] = item.get("phone", "")
            item["emailId"] = item.get("email", "")
        # --- Fetch from core_hospitallab ---
        hospital_cursor = db.core_hospitallab.find({}, {
            "_id": 0,
            "hospitalName": 1,
            "type": 1,
            "salesMapping": 1,
            "contactPerson": 1,
            "contactNumber": 1,
            "emailId": 1
        })
        hospital_list = list(hospital_cursor)
        # --- Merge both ---
        combined = clinical_list + hospital_list
        return Response(
            {"success": True, "data": combined},
            status=status.HTTP_200_OK
        )
    except Exception as e:
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )