from rest_framework.response import Response
from django.http import JsonResponse , HttpResponse
from datetime import datetime
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from urllib.parse import quote_plus
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
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ...models import Hmssamplestatus, HmspatientBilling
from ...models import TestValue
from ...models import SampleStatus
from ...models import Hmsbarcode
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ...models import SampleStatus, TestValue
from ...serializers import SampleStatusSerializer
from ...serializers import TestValueSerializer
import os
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_hmssamplestatus_testvalue(request):
    try:
        # Get date parameters from query
        from_date_str = request.query_params.get('from_date', None)
        to_date_str = request.query_params.get('to_date', None)
        date_str = request.query_params.get('date', None)

        # Determine date range
        if from_date_str and to_date_str:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d').date()
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()

            if from_date > to_date:
                return Response({"error": "From date cannot be after to date."}, status=status.HTTP_400_BAD_REQUEST)

            start_of_range = datetime.combine(from_date, datetime.min.time())
            end_of_range = datetime.combine(to_date, datetime.max.time())

        elif date_str:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_of_range = datetime.combine(selected_date, datetime.min.time())
            end_of_range = start_of_range + timedelta(days=1)

        else:
            return Response({"error": "Either 'date' or both 'from_date' and 'to_date' parameters are required."}, status=status.HTTP_400_BAD_REQUEST)

        combined_results = []
        processed_barcodes = {}

        # Fetch sample statuses
        sample_statuses = Hmssamplestatus.objects.filter(
            date__gte=start_of_range,
            date__lte=end_of_range
        ).order_by('-date', '-created_date')

        for sample_status in sample_statuses:
            try:
                testdetails = json.loads(sample_status.testdetails) if isinstance(sample_status.testdetails, str) else sample_status.testdetails
            except json.JSONDecodeError:
                continue

            filtered_tests = [
                test for test in testdetails
                if test.get('samplestatus') in ['Received', 'Outsource']
            ]

            if filtered_tests:
                barcode = sample_status.barcode

                if barcode not in processed_barcodes or sample_status.created_date > processed_barcodes[barcode]['created_date']:
                    # Default values
                    patient_name = "Unknown Patient"
                    patient_id = "Unknown ID"
                    age = "Unknown"
                    gender = "Unknown"

                    try:
                        # Get Hmsbarcode details
                        barcode_details = Hmsbarcode.objects.get(barcode=barcode)
                        billnumber = barcode_details.billnumber

                        # Try to match billing using billnumber
                        try:
                            billing_details = HmspatientBilling.objects.get(billnumber=billnumber)
                            patient_name = billing_details.patientname
                            patient_id = billing_details.patient_id
                            age = billing_details.age
                            gender = billing_details.gender
                        except HmspatientBilling.DoesNotExist:
                            pass

                    except Hmsbarcode.DoesNotExist:
                        if hasattr(sample_status, 'patient_id'):
                            patient_id = sample_status.patient_id

                    # TestValue lookup
                    all_test_values = TestValue.objects.filter(
                        barcode=barcode
                    ).order_by('-created_date', '-lastmodified_date')

                    updated_tests = []
                    for test in filtered_tests:
                        test_name = test.get('testname', '').strip().lower()
                        test_code = test.get('testcode', test_name).strip().lower()

                        test.update({
                            'rerun': False,
                            'approve': False,
                            'test_value_exists': False,
                            'approve_time': None,
                            'rerun_time': None,
                            'approve_by': None
                        })

                        matching_value = None
                        for tv in all_test_values:
                            try:
                                tv_details = json.loads(tv.testdetails) if isinstance(tv.testdetails, str) else tv.testdetails
                            except Exception:
                                tv_details = []

                            for tv_test in tv_details:
                                tv_test_name = tv_test.get('testname', '').strip().lower()
                                tv_test_code = tv_test.get('testcode', tv_test_name).strip().lower()

                                if tv_test_name == test_name or tv_test_code == test_code:
                                    matching_value = tv_test
                                    break
                            if matching_value:
                                break

                        if matching_value:
                            test.update({
                                'test_value_exists': True,
                                'approve': bool(matching_value.get('approve', False)),
                                'rerun': bool(matching_value.get('rerun', False)),
                                'approve_time': matching_value.get('approve_time'),
                                'rerun_time': matching_value.get('rerun_time'),
                                'approve_by': matching_value.get('approve_by')
                            })

                        updated_tests.append(test)

                    sample_status_dict = {
                        'id': sample_status.id,
                        'created_by': sample_status.created_by,
                        'created_date': sample_status.created_date,
                        'lastmodified_by': sample_status.lastmodified_by,
                        'lastmodified_date': sample_status.lastmodified_date,
                        'patient_id': patient_id,
                        'patientname': patient_name,
                        'age': age,
                        'gender': gender,
                        'barcode': barcode,
                        'date': sample_status.date,
                        'testdetails': updated_tests,
                        'data_source': 'django_model'
                    }

                    processed_barcodes[barcode] = {
                        'created_date': sample_status.created_date,
                        'data': sample_status_dict
                    }

        # Collect results
        combined_results = [b['data'] for b in processed_barcodes.values()]

        # Serialize
        serialized_results = []
        for item in combined_results:
            for key, value in item.items():
                if isinstance(value, datetime):
                    item[key] = value.isoformat()
            serialized_results.append(item)

        serialized_results.sort(key=lambda x: x.get('date', ''), reverse=True)

        return Response(serialized_results, status=status.HTTP_200_OK)

    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def hmscompare_test_details(request):   
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics  # Database name
    
    # Collections
    core_testdetails_collection = db.core_testdetails
    interface_testvalue_collection = db.interface_testvalue
    
    # Get barcode from request
    barcode = request.GET.get('barcode')
    device_id = request.GET.get('device_id')  # Optional
    
    if not barcode:
        return JsonResponse({'error': 'Barcode parameter is required'}, status=400)
    
    # Initialize defaults
    patient_id = None
    patient_name = None
    test_list = []
    
    # Step 1: Get patient + test details
    try:
        barcode_obj = Hmsbarcode.objects.get(barcode=barcode)
        
        # ✅ FIX: check available fields safely
        patient_id = getattr(barcode_obj, "patient_id", None) \
                     or getattr(barcode_obj, "patientid", None) \
                     or getattr(barcode_obj, "billnumber", f"UNKNOWN_{barcode}")
        
        patient_name = getattr(barcode_obj, "patientname", f"Unknown Patient {barcode}")

        # Try to fetch test details from Hmssamplestatus
        try:
            sample_status_obj = Hmssamplestatus.objects.get(barcode=barcode)
            if isinstance(sample_status_obj.testdetails, str):
                test_list = json.loads(sample_status_obj.testdetails)
            elif isinstance(sample_status_obj.testdetails, list):
                test_list = sample_status_obj.testdetails
            else:
                test_list = []
        except Hmssamplestatus.DoesNotExist:
            test_list = []

    except Hmsbarcode.DoesNotExist:
        # As a fallback, check interface_testvalue
        interface_record = interface_testvalue_collection.find_one(
            {"Barcode": barcode},
            sort=[("Receiveddate", -1)]
        )
        
        if interface_record:
            patient_id = interface_record.get('patient_id', f'UNKNOWN_{barcode}')
            patient_name = interface_record.get('patientname', f'Unknown Patient {barcode}')
            
            unique_tests = interface_testvalue_collection.distinct("TestCode", {"Barcode": barcode})
            for test_code in unique_tests:
                test_detail = core_testdetails_collection.find_one({"test_code": test_code})
                test_name = test_detail.get('test_name', test_code) if test_detail else test_code
                test_list.append({
                    'testname': test_name,
                    'test_id': test_code
                })
        else:
            return JsonResponse({'error': f'No data found for barcode: {barcode}'}, status=404)
    
    # Step 2: Get sample status details (Django only)
    sample_status_map = {}
    try:
        sample_status_obj = Hmssamplestatus.objects.get(barcode=barcode)
        if isinstance(sample_status_obj.testdetails, str):
            sample_test_list = json.loads(sample_status_obj.testdetails)
        elif isinstance(sample_status_obj.testdetails, list):
            sample_test_list = sample_status_obj.testdetails
        else:
            sample_test_list = []
        
        for sample_test in sample_test_list:
            test_name = sample_test.get('testname')
            sample_status = sample_test.get('samplestatus')
            if test_name:
                sample_status_map[test_name] = {
                    'status': sample_status,
                    'source': 'django_model'
                }
    except Hmssamplestatus.DoesNotExist:
        pass
    except json.JSONDecodeError:
        pass
    
    # Step 3: Process tests
    final_test_data = []
    
    for test_item in test_list:
        test_name = test_item.get('testname') or test_item.get('test_name')
        test_id = test_item.get('test_id')
        
        sample_status_info = sample_status_map.get(test_name, {'status': 'Unknown', 'source': 'none'})
        sample_status = sample_status_info['status']
        data_source = sample_status_info['source']
        
        if sample_status == "Received" or sample_status == 'Unknown':
            # Fetch test definition from core_testdetails
            test_details_cursor = core_testdetails_collection.find({
                "test_name": test_name,
                "test_id": test_id
            })
            test_details_list = list(test_details_cursor)
            
            if not test_details_list and test_id:
                test_details_list = list(core_testdetails_collection.find({"test_id": test_id}))
            if not test_details_list:
                test_details_list = list(core_testdetails_collection.find({"test_name": test_name}))
            
            if test_details_list:
                test_detail = test_details_list[0]
                parameters = test_detail.get('parameters', {})
                
                if not parameters:
                    # Simple test
                    test_code = test_detail.get('test_code', f"{test_name.replace(' ', '').upper()}01")
                    query = {"Barcode": barcode, "TestCode": test_code, "processingstatus": "pending"}
                    if device_id:
                        query["DeviceID"] = device_id
                    
                    test_value_doc = interface_testvalue_collection.find_one(
                        query, sort=[("Receiveddate", -1)]
                    )
                    
                    test_info = {
                        "patient_id": patient_id,
                        "patientname": patient_name,
                        "barcode": barcode,
                        "device_id": test_value_doc.get('DeviceID') if test_value_doc else 'N/A',
                        "test_id": test_id,
                        "testname": test_name,
                        "test_code": test_code,
                        "parameter_name": None,
                        "unit": test_detail.get('unit', 'N/A'),
                        "reference_range": test_detail.get('reference_range', 'N/A'),
                        "method": test_detail.get('method', 'N/A'),
                        "department": test_detail.get('department', 'N/A'),
                        "specimen_type": test_detail.get('specimen_type', 'N/A'),
                        "NABL": test_detail.get('NABL', 'N/A'),
                        "test_value": test_value_doc.get('Value', '') if test_value_doc else '',
                        "processing_status": test_value_doc.get('processingstatus', 'N/A') if test_value_doc else 'N/A',
                        "sample_status": sample_status,
                        "data_source": data_source,
                        "lab_unique_id": test_value_doc.get('lab_unique_id', 'N/A') if test_value_doc else 'N/A',
                        "created_date": test_value_doc.get('CreatedDate') if test_value_doc else None,
                        "received_date": test_value_doc.get('Receiveddate') if test_value_doc else None
                    }
                    final_test_data.append(test_info)
                else:
                    # Parameterized test
                    for device_key, param_list in parameters.items():
                        for param in param_list:
                            test_code = param.get('test_code')
                            if not test_code:
                                continue
                            
                            record = interface_testvalue_collection.find_one(
                                {"Barcode": barcode, "TestCode": test_code, "processingstatus": "pending"},
                                sort=[("Receiveddate", -1)]
                            )
                            
                            test_info = {
                                "patient_id": patient_id,
                                "patientname": patient_name,
                                "barcode": barcode,
                                "device_id": device_key,
                                "test_id": test_id,
                                "testname": test_name,
                                "test_code": test_code,
                                "parameter_name": param.get('test_name'),
                                "unit": param.get('unit'),
                                "reference_range": param.get('reference_range'),
                                "method": param.get('method'),
                                "department": test_detail.get('department'),
                                "specimen_type": test_detail.get('specimen_type', param.get('specimen_type')),
                                "NABL": test_detail.get('NABL', 'N/A'),
                                "test_value": record.get('Value', '') if record else '',
                                "processing_status": record.get('processingstatus', 'No Data') if record else 'No Data',
                                "sample_status": sample_status,
                                "data_source": data_source,
                                "lab_unique_id": record.get('lab_unique_id', 'N/A') if record else 'N/A',
                                "created_date": record.get('CreatedDate') if record else None,
                                "received_date": record.get('Receiveddate') if record else None
                            }
                            final_test_data.append(test_info)
            else:
                # No test mapping found
                final_test_data.append({
                    "patient_id": patient_id,
                    "patientname": patient_name,
                    "barcode": barcode,
                    "device_id": "N/A",
                    "test_id": test_id,
                    "testname": test_name,
                    "test_code": "N/A",
                    "parameter_name": None,
                    "unit": "",
                    "reference_range": "",
                    "method": "",
                    "department": "",
                    "specimen_type": "",
                    "NABL": "N/A",
                    "test_value": "",
                    "processing_status": "No Test Details",
                    "sample_status": sample_status,
                    "data_source": data_source,
                    "lab_unique_id": "N/A",
                    "created_date": None,
                    "received_date": None
                })
    
        try:
            client.close()
        except:
            pass

        # ✅ Wrap response in structured format
        response_data = {
            "success": True,
            "patient_info": {
                "patient_id": patient_id,
                "patient_name": patient_name,
                "barcode": barcode
            },
            "test_count": len(final_test_data),
            "data": final_test_data,
            "processed_records": []  # can populate later if needed
        }

        return Response(response_data, status=200)

