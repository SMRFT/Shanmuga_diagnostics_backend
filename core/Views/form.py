from rest_framework.response import Response
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from django.views.decorators.csrf import csrf_exempt
from pymongo import MongoClient
import os

#models and serializers
from ..models import RefBy
from ..serializers import RefBySerializer

#auth
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def sample_collector(request):
    try:
        # Connect to global DB
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        db = client["Global"]
        collection = db["backend_diagnostics_profile"]  # <-- Same here
        # Query: employees with primaryRole == "SD-R-SMC" OR additionalRoles contains "SD-R-SMC"
        query = {
            "$or": [
                {"primaryRole": "SD-R-SMC"},
                {"additionalRoles": "SD-R-SMC"}
            ]
        }

        docs = collection.find(query, {"employeeName": 1, "_id": 0})
        employee_names = [doc.get("employeeName") for doc in docs if doc.get("employeeName")]

        return Response(employee_names, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET', 'POST'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def refby(request):
    if request.method == 'POST':
        serializer = RefBySerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'GET':
        collectors = RefBy.objects.all()
        serializer = RefBySerializer(collectors, many=True)
        return Response(serializer.data)