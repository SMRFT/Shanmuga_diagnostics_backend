from rest_framework.response import Response
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Max, Q
import json
from core.mongo_client import get_client
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
from core.pagination import paginate_queryset

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
    # MongoDB connection (shared, pooled client)
    client = get_client()
    db = client["Diagnostics"]
    return db, GridFS(db)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def sales_person(request):
    try:
        # Connect to global DB
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = get_client()
        db = client["Global"]
        collection = db["backend_diagnostics_profile"]  # <-- Same here
        # Query: employees with primaryRole == "SD-R-SMC" OR additionalRoles contains "SD-R-SMC"
        query = {
            "$or": [
                {"primaryRole": "SD-R-SE"},
                {"additionalRoles": "SD-R-SE"}
            ]
        }

        docs = collection.find(query, {"employeeName": 1, "_id": 0})
        employee_names = [doc.get("employeeName") for doc in docs if doc.get("employeeName")]

        return Response(employee_names, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

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
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@api_view(['PATCH'])
@permission_classes([HasRoleAndDataPermission])
def clinicalname_reject(request, referrerCode=None):
    try:
        clinical_name = get_object_or_404(ClinicalName, referrerCode=referrerCode)
        
        if clinical_name.status not in ['PENDING_APPROVAL', 'PENDING_FINAL']:
            return Response({"error": "This clinical name is not in a state that can be rejected."}, status=status.HTTP_400_BAD_REQUEST)
        
        rejected_reason = request.data.get("rejected_reason", "")
        rejected_id = request.data.get("auth-user-id") or request.data.get("rejected_id", "Admin")

        # Update rejection status
        clinical_name.status = 'REJECTED'
        clinical_name.rejected_reason = rejected_reason
        clinical_name.rejected_id = rejected_id
        clinical_name.rejected_date = timezone.now()
        clinical_name.save()
        
        return Response(
            {"message": "Rejection processed successfully", "referrerCode": clinical_name.referrerCode},
            status=status.HTTP_200_OK
        )
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

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
        page_obj, page_meta = paginate_queryset(clinical_names, request)
        serializer = ClinicalNameSerializer(page_obj, many=True)
        # NOTE: response shape changed from a bare JSON array to a paginated
        # object ({"data": [...], total_pages, current_page, total_count}) to
        # bound the payload as approved clinical name records grow; update any
        # frontend caller that expected a raw array here.
        return Response({
            "data": serializer.data,
            **page_meta
        })

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

from ..models import B2BPackage
from ..serializers import B2BPackageSerializer

@api_view(['GET', 'POST', 'PATCH'])
@permission_classes([HasRoleAndDataPermission])
def b2b_packages(request):
    if request.method == 'GET':
        packages = B2BPackage.objects.all()
        search = request.GET.get('search', '').strip()
        status_filter = request.GET.get('status', '').strip()
        if search:
            packages = packages.filter(Q(packageName__icontains=search) | Q(referrerCode__icontains=search))
        if status_filter and status_filter.lower() != 'all':
            packages = packages.filter(status__iexact=status_filter)
        page_obj, page_meta = paginate_queryset(packages, request)
        serializer = B2BPackageSerializer(page_obj, many=True)
        # NOTE: response shape changed from a bare JSON array to a paginated
        # object ({"data": [...], total_pages, current_page, total_count}) to
        # bound the payload as B2B package records grow; update any frontend
        # caller that expected a raw array here.
        return Response({
            "data": serializer.data,
            **page_meta
        }, status=status.HTTP_200_OK)
    
    elif request.method == 'POST':
        data = request.data.copy()
        user_id = request.headers.get('Auth-User-Id') or data.get('auth-user-id', 'System')
        data['created_by'] = user_id
        data['created_date'] = timezone.now()
        
        serializer = B2BPackageSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == 'PATCH':
        package_id = request.data.get('package_id')
        action = request.data.get('action', 'approve')
        
        if not package_id:
            return Response({"error": "package_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            package = B2BPackage.objects.get(package_id=package_id)
        except B2BPackage.DoesNotExist:
            return Response({"error": "Package not found"}, status=status.HTTP_404_NOT_FOUND)
            
        user_id = request.headers.get('Auth-User-Id') or request.data.get('auth-user-id', 'System')
        
        if action == 'reject':
            package.status = "Rejected"
            package.rejected_by = user_id
            package.rejected_date = timezone.now()
            package.rejected_Reason = request.data.get('reason', '')
        else:
            package.status = "Approved"
            package.approved_by = user_id
            package.approved_date = timezone.now()
            
        package.lastmodified_by = user_id
        package.lastmodified_date = timezone.now()
        package.save()
        
        return Response({"message": f"Package {package.packageName} {package.status.lower()} successfully"}, status=status.HTTP_200_OK)
