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
import certifi
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from .models import Patient
from .models import SampleStatus
from .models import TestValue
from .models import BarcodeTestDetails
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from .models import SampleStatus, TestValue
from .serializers import SampleStatusSerializer
from .serializers import TestValueSerializer
import os
from dotenv import load_dotenv
load_dotenv()






@api_view(['GET'])
@csrf_exempt  # Allow GET, POST, and PATCH requests without CSRF protection
@permission_classes([HasRoleAndDataPermission])
def get_test_details(request):
    try:
        # MongoDB connection with TLS certificate
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics  # Database name
        collection = db.core_testdetails  # Collection name
        
        if request.method == 'GET':
            # Get query parameters
            test_id = request.GET.get('test_id')
            status = request.GET.get('status', 'Approved')  # Default to 'Approved'
            
            # Build query filter
            query_filter = {"status": status}
            
            # Add test_id filter if provided
            if test_id:
                try:
                    # Convert test_id to integer if it's a numeric string
                    test_id_int = int(test_id)
                    query_filter["test_id"] = test_id_int
                except ValueError:
                    # If conversion fails, treat as string
                    query_filter["test_id"] = test_id
            
            # Retrieve documents based on the filter
            test_details = list(collection.find(query_filter, {'_id': 0}))
            
            # Return the results
            if test_details:
                return JsonResponse({
                    'success': True,
                    'data': test_details,
                    'count': len(test_details)
                }, status=200)
            else:
                return JsonResponse({
                    'success': False,
                    'message': f'No test details found for the given criteria',
                    'data': []
                }, status=404)
                
        elif request.method == 'POST':
            try:
                data = json.loads(request.body.decode('utf-8'))
                if 'parameters' in data and isinstance(data['parameters'], list):
                    if not all(isinstance(param, dict) for param in data['parameters']):
                        return JsonResponse({'error': 'Invalid format for parameters: all elements must be dictionaries'}, status=400)
                    data['parameters'] = json.dumps(data['parameters'])
                else:
                    return JsonResponse({'error': 'Parameters should be a JSON array of dictionaries'}, status=400)
                # Insert data into MongoDB
                collection.insert_one(data)
                return JsonResponse({'message': 'Test details added successfully'}, status=201)
            except json.JSONDecodeError:
                return JsonResponse({'error': 'Invalid JSON data'}, status=400)
            except Exception as e:
                print("Error:", e)
                return JsonResponse({'error': 'An error occurred while saving data'}, status=500)
                
        elif request.method == 'PATCH':
            try:
                data = json.loads(request.body.decode('utf-8'))
                test_name = data.get('test_name')
                updated_parameters = data.get('parameters')
                if not test_name or updated_parameters is None:
                    return JsonResponse({'error': 'test_name and parameters are required'}, status=400)
                updated_parameters_json = json.dumps(updated_parameters)
                result = collection.update_one(
                    {'test_name': test_name},
                    {'$set': {'parameters': updated_parameters_json}}
                )
                if result.matched_count > 0:
                    return JsonResponse({'message': 'Parameters updated successfully'}, status=200)
                else:
                    return JsonResponse({'error': 'Test not found'}, status=404)
            except json.JSONDecodeError:
                return JsonResponse({'error': 'Invalid JSON data'}, status=400)
                
    except Exception as e:
        print("Error:", e)
        return JsonResponse({'error': 'An error occurred'}, status=500)
    