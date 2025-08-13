



from ..serializers import PatientSerializer
from rest_framework.response import Response
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from django.db.models import Max
from datetime import datetime
from django.forms.models import model_to_dict
import json
import re
from ..models import Patient
from datetime import datetime, timedelta
#auth
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission


@api_view(['GET', 'POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def create_patient(request):
    if request.method == 'POST':
        serializer = PatientSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'GET':
        patient_id = request.query_params.get('patient_id')
        if not patient_id:
            return Response({'error': 'patient_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            patient = Patient.objects.get(patient_id = patient_id)  # adjust field name if different
            serializer = PatientSerializer(patient)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Patient.DoesNotExist:
            return Response({'error': 'Patient not found'}, status=status.HTTP_404_NOT_FOUND)
from ..serializers import BillingSerializer
@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def create_bill(request):
    if request.method == 'POST':
        serializer = BillingSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
from django.db.models import Max
import re

@api_view(['GET'])
def get_latest_patient_id(request):
    max_patient = Patient.objects.aggregate(max_pid=Max('patient_id'))['max_pid']
    max_num = 0
    if max_patient:
        match = re.match(r'^SD0*(\d+)$', max_patient, re.IGNORECASE)
        if match:
            max_num = int(match.group(1))
    new_patient_id = f"SD{max_num + 1:04d}"
    return Response({"patient_id": new_patient_id}, status=200)

@api_view(['GET'])
def get_latest_bill_no(request):
    today = datetime.now().strftime('%Y%m%d')  # Get today's date in YYYYMMDD format
    # Get the latest bill_no that starts with today's date
    last_bill = Patient.objects.filter(bill_no__startswith=today).aggregate(Max('bill_no'))
    if last_bill['bill_no__max']:
        # Extract the numeric part of the last bill number and increment it
        last_id = int(last_bill['bill_no__max'][-4:])  # Extract the last 4 digits
        next_id = last_id + 1
    else:
        # Start with 0001 if no bills exist for today
        next_id = 1
    # Generate the new bill number
    new_bill_no = f"{today}{next_id:04d}"  # Format: YYYYMMDD0001
    return Response({"bill_no": new_bill_no}, status=status.HTTP_200_OK)