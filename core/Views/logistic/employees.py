import os
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q, Count, Case, When, IntegerField
from pymongo import MongoClient
import certifi
from gridfs import GridFS
from datetime import datetime, timedelta
from django.core.files.storage import default_storage
from pymongo import MongoClient
import certifi
from gridfs import GridFS

from collections import defaultdict
from core.utils import get_employee_name

from ...models import Logistics, Billing, CustomerComplaint
from ...serializers import LogisticsSerializer, BillingSerializer, CustomerComplaintSerializer
from core.pagination import paginate_queryset


from ..dbcollection import profile_collection, B2B_ROLES, B2B_LAB_Roles, BIO_CHESMISTRY_ROLES

#auth
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission
import os
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_b2b_employees(request):
    try:
        employee_id = request.data.get("auth-user-id")
        logger.info(f"Fetching B2B employees for employee_id: {employee_id}")

        query = {
            "$or": [
                {"primaryRole": {"$in": B2B_ROLES}},
                {"additionalRoles": {"$in": B2B_ROLES}}
            ]
        }

        projection = {
            "_id": 0,
            "employeeId": 1,
            "employeeName": 1,
            "primaryRole": 1,
            "additionalRoles": 1,
            "hospitalCode": 1
        }

        employees = list(profile_collection.find(query, projection))

        page_obj, page_meta = paginate_queryset(employees, request)

        return Response(
            {
                "status": True,
                "message": "B2B Employees fetched successfully",
                "data": list(page_obj),
                **page_meta
            },
            status=status.HTTP_200_OK
        )

    except Exception as e:
        return Response(
            {
                "status": False,
                "message": str(e),
                "data": []
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_b2b_lab_employees(request):
    try:
        employee_id = request.data.get("auth-user-id")
        logger.info(f"Fetching B2B Lab employees for employee_id: {employee_id}")

        query = {
            "$or": [
                {"primaryRole": {"$in": B2B_LAB_Roles}},
                {"additionalRoles": {"$in": B2B_LAB_Roles}},
                {"designation": {"$in": BIO_CHESMISTRY_ROLES}}
            ]
        }

        projection = {
            "_id": 0,
            "employeeId": 1,
            "employeeName": 1,
            "primaryRole": 1,
            "additionalRoles": 1,
            "designation": 1,
            "hospitalCode": 1
        }

        employees = list(profile_collection.find(query, projection))

        page_obj, page_meta = paginate_queryset(employees, request)

        return Response(
            {
                "status": True,
                "message": "B2B Lab Employees fetched successfully",
                "data": list(page_obj),
                **page_meta
            },
            status=status.HTTP_200_OK
        )

    except Exception as e:
        return Response(
            {
                "status": False,
                "message": str(e),
                "data": []
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
