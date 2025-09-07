from rest_framework.response import Response
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Max
import json
from pymongo import MongoClient
import certifi
from gridfs import GridFS
from django.utils import timezone 
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from bson import ObjectId
from rest_framework import viewsets, status
from rest_framework.decorators import action
import pytz
import os

#models and serializers
from ..serializers import ClinicalNameSerializer
from ..models import ClinicalName

#auth
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv

load_dotenv()
# Define IST timezone
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)

# MongoDB Connection Setup
def get_mongodb_connection():
    # MongoDB connection with TLS certificate
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client["Diagnostics"]
    return db, GridFS(db)

# View for handling referrer code generation
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_last_referrer_code(request):
    try:
        last_clinical = ClinicalName.objects.all().order_by('-referrerCode').first()
        if last_clinical:
            return Response({'referrerCode': last_clinical.referrerCode})
        else:
            return Response({'referrerCode': 'SD0000'})
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

@api_view(['POST', 'GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def clinical_name(request):
    if request.method == 'POST':
        mou_copy = request.FILES.get('mouCopy')
        data = request.data.copy()

        if not data.get('clinicalname'):
            return Response({"error": "Clinical name is required"}, status=status.HTTP_400_BAD_REQUEST)

        if mou_copy:
            data.pop('mouCopy', None)

        data['status'] = 'PENDING_APPROVAL'
        data['first_approved'] = False
        data['final_approved'] = False

        serializer = ClinicalNameSerializer(data=data)

        if serializer.is_valid():
            try:
                clinical_name_instance = serializer.save()

                if mou_copy:
                    db, fs = get_mongodb_connection()
                    file_content = mou_copy.read()
                    file_id = fs.put(
                        file_content,
                        filename=mou_copy.name,
                        content_type=mou_copy.content_type,
                        clinical_name=clinical_name_instance.clinicalname
                    )
                    clinical_name_instance.mou_file_id = str(file_id)
                    clinical_name_instance.save()

                return Response(serializer.data, status=status.HTTP_201_CREATED)

            except Exception as e:
                return Response(
                    {'error': 'Clinical name creation failed', 'details': str(e)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == 'GET':
        clinical_names = ClinicalName.objects.filter(status="APPROVED")
        serializer = ClinicalNameSerializer(clinical_names, many=True)
        return Response(serializer.data)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def download_mou_file(request, clinical_name_id):
    try:
        db, fs = get_mongodb_connection()
        # Find the file by clinical_name_id
        file_record = fs.find_one({'clinical_name_id': clinical_name_id})
        if file_record:
            file_data = file_record.read()
            response = HttpResponse(
                file_data,
                content_type=file_record.content_type
            )
            response['Content-Disposition'] = f'attachment; filename="{file_record.filename}"'
            return response
        else:
            return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response(
            {'error': 'File retrieval failed', 'details': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def preview_mou_file(request, file_id):
    try:
        db, fs = get_mongodb_connection()
        # Convert the file id from string to ObjectId
        file_record = fs.find_one({'_id': ObjectId(file_id)})
        if file_record:
            file_data = file_record.read()
            response = HttpResponse(
                file_data,
                content_type=file_record.content_type
            )
            response = HttpResponse(file_data, content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="{file_record.filename}"'
            return response

        else:
            return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response(
            {'error': 'File retrieval failed', 'details': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@permission_classes([HasRoleAndDataPermission])
class ClinicalNameViewSet(viewsets.ModelViewSet):
    queryset = ClinicalName.objects.all()
    serializer_class = ClinicalNameSerializer
    
    def get_queryset(self):
        queryset = ClinicalName.objects.all()
        status_filter = self.request.query_params.get('status', None)
        
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset
    
    @action(detail=False, methods=['patch'], url_path='(?P<referrerCode>[^/.]+)/first_approve')
    def first_approve(self, request, referrerCode=None):
        try:
            clinical_name = get_object_or_404(ClinicalName, referrerCode=referrerCode)
            
            if clinical_name.status != 'PENDING_APPROVAL':
                return Response({"error": "This clinical name is not pending first approval."}, status=status.HTTP_400_BAD_REQUEST)
            
            # Update approval status
            clinical_name.first_approved = True
            clinical_name.first_approved_timestamp = timezone.now()
            clinical_name.status = 'PENDING_FINAL'
            clinical_name.save()
            
            return Response(
                {"message": "First approval completed successfully", "referrerCode": clinical_name.referrerCode},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['patch'], url_path='(?P<referrerCode>[^/.]+)/final_approve')
    def final_approve(self, request, referrerCode=None):
        try:
            clinical_name = get_object_or_404(ClinicalName, referrerCode=referrerCode)
            
            if clinical_name.status != 'PENDING_FINAL':
                return Response({"error": "This clinical name is not pending final approval."}, status=status.HTTP_400_BAD_REQUEST)
            
            # Update final approval status
            clinical_name.final_approved = True
            clinical_name.final_approved_timestamp = timezone.now()
            clinical_name.status = 'APPROVED'
            clinical_name.save()
            
            return Response(
                {"message": "Final approval completed successfully", "referrerCode": clinical_name.referrerCode},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_clinicalname(request):
    if request.method == 'GET':
        clinicalname = ClinicalName.objects.all()
        serializer = ClinicalNameSerializer(clinicalname, many=True)
        return Response(serializer.data)
    

@api_view(['PUT'])
@permission_classes([HasRoleAndDataPermission])
def update_clinicalname(request):
    referrer_code = request.data.get('referrerCode')

    if not referrer_code:
        return Response({"error": "referrerCode is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        instance = ClinicalName.objects.get(referrerCode=referrer_code)
    except ClinicalName.DoesNotExist:
        return Response({"error": "Clinical record not found."}, status=status.HTTP_404_NOT_FOUND)

    serializer = ClinicalNameSerializer(instance, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)