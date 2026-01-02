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
from ..models import Patient,Hmssamplestatus
from ..models import SampleStatus
from ..models import TestValue
from ..models import SampleStatus
from ..models import BarcodeTestDetails
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ..models import SampleStatus, TestValue, Hmssamplestatus, Hmsbarcode, HmspatientBilling
from ..serializers import SampleStatusSerializer
from ..serializers import TestValueSerializer
import os
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_os_samplestatus_testvalue(request):
    try:
        # Get query parameters
        from_date_str = request.query_params.get('from_date', None)
        to_date_str = request.query_params.get('to_date', None)
        date_str = request.query_params.get('date', None)
        source = request.query_params.get('source', 'all')
        
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
        
        # Helper function to safely convert datetime to string
        def safe_datetime_to_string(dt_obj):
            if dt_obj is None:
                return None
            if isinstance(dt_obj, str):
                return dt_obj
            if hasattr(dt_obj, 'isoformat'):
                return dt_obj.isoformat()
            return str(dt_obj)
        
        # ============================================
        # OPTIMIZATION 1: Collect all barcodes first
        # ============================================
        all_barcodes = set()
        sample_data_by_barcode = {}
        
        # Collect barcodes from HMS
        if source in ['hms', 'all']:
            hms_samples = Hmssamplestatus.objects.filter(
                date__gte=start_of_range,
                date__lte=end_of_range
            ).select_related().order_by('-created_date')
            
            for sample in hms_samples:
                barcode = sample.barcode
                all_barcodes.add(barcode)
                if barcode not in sample_data_by_barcode:
                    sample_data_by_barcode[barcode] = {
                        'source': 'hms',
                        'sample': sample,
                        'created_date': sample.created_date
                    }
        
        # Collect barcodes from Regular
        if source in ['regular', 'all']:
            regular_samples = SampleStatus.objects.filter(
                date__gte=start_of_range, 
                date__lte=end_of_range
            ).select_related().order_by('-created_date')
            
            for sample in regular_samples:
                barcode = sample.barcode
                all_barcodes.add(barcode)
                if barcode not in sample_data_by_barcode or sample.created_date > sample_data_by_barcode[barcode]['created_date']:
                    sample_data_by_barcode[barcode] = {
                        'source': 'regular',
                        'sample': sample,
                        'created_date': sample.created_date
                    }
        
        # ============================================
        # OPTIMIZATION 2: Bulk fetch all related data
        # ============================================
        
        # Fetch ALL TestValues for all barcodes in ONE query
        test_values_by_barcode = {}
        if all_barcodes:
            all_test_values = TestValue.objects.filter(
                barcode__in=all_barcodes
            ).order_by('barcode', '-created_date', '-lastmodified_date')
            
            # Group by barcode for O(1) lookup
            for tv in all_test_values:
                if tv.barcode not in test_values_by_barcode:
                    test_values_by_barcode[tv.barcode] = []
                test_values_by_barcode[tv.barcode].append(tv)
        
        # Fetch all HMS barcode details in one query
        hms_barcodes_dict = {}
        if source in ['hms', 'all'] and all_barcodes:
            hms_barcodes = Hmsbarcode.objects.filter(
                barcode__in=all_barcodes
            ).select_related()
            hms_barcodes_dict = {hb.barcode: hb for hb in hms_barcodes}
        
        # Fetch all HMS billing details in one query
        hms_billing_dict = {}
        if source in ['hms', 'all'] and hms_barcodes_dict:
            billnumbers = [hb.billnumber for hb in hms_barcodes_dict.values() if hasattr(hb, 'billnumber')]
            if billnumbers:
                hms_billings = HmspatientBilling.objects.filter(
                    billnumber__in=billnumbers
                )
                hms_billing_dict = {hb.billnumber: hb for hb in hms_billings}
        
        # Fetch all regular barcode details in one query
        regular_barcodes_dict = {}
        if source in ['regular', 'all'] and all_barcodes:
            regular_barcodes = BarcodeTestDetails.objects.filter(
                barcode__in=all_barcodes
            )
            regular_barcodes_dict = {rb.barcode: rb for rb in regular_barcodes}
        
        # ============================================
        # OPTIMIZATION 3: Process with cached data
        # ============================================
        
        def parse_testdetails(testdetails_raw):
            """Cache-friendly test details parser"""
            try:
                return json.loads(testdetails_raw) if isinstance(testdetails_raw, str) else testdetails_raw
            except (json.JSONDecodeError, TypeError):
                return []
        
        def match_test_values(test, barcode, test_values_by_barcode):
            """Optimized test value matching with pre-fetched data"""
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
            
            # Use cached test values
            test_values = test_values_by_barcode.get(barcode, [])
            
            for tv in test_values:
                tv_details = parse_testdetails(tv.testdetails)
                
                for tv_test in tv_details:
                    tv_test_name = tv_test.get('testname', '').strip().lower()
                    tv_test_code = tv_test.get('testcode', tv_test_name).strip().lower()
                    
                    if tv_test_name == test_name or tv_test_code == test_code:
                        test.update({
                            'test_value_exists': True,
                            'approve': bool(tv_test.get('approve', False)),
                            'rerun': bool(tv_test.get('rerun', False)),
                            'approve_time': tv_test.get('approve_time'),
                            'rerun_time': tv_test.get('rerun_time'),
                            'approve_by': tv_test.get('approve_by')
                        })
                        return test
            
            return test
        
        combined_results = []
        
        # Process HMS samples
        if source in ['hms', 'all']:
            for barcode, data in sample_data_by_barcode.items():
                if data['source'] != 'hms':
                    continue
                
                sample_status = data['sample']
                testdetails = parse_testdetails(sample_status.testdetails)
                
                filtered_tests = [
                    test for test in testdetails
                    if test.get('samplestatus') in ['Outsource']
                ]
                
                if not filtered_tests:
                    continue
                
                # Use cached barcode and billing data
                patient_name = "Unknown Patient"
                patient_id = "Unknown ID"
                age = "Unknown"
                gender = "Unknown"
                
                barcode_details = hms_barcodes_dict.get(barcode)
                if barcode_details:
                    billing_details = hms_billing_dict.get(barcode_details.billnumber)
                    if billing_details:
                        patient_name = billing_details.patientname
                        patient_id = billing_details.patient_id
                        age = billing_details.age
                        gender = billing_details.gender
                elif hasattr(sample_status, 'patient_id'):
                    patient_id = sample_status.patient_id
                
                # Match test values
                updated_tests = [
                    match_test_values(test, barcode, test_values_by_barcode)
                    for test in filtered_tests
                ]
                
                combined_results.append({
                    'id': sample_status.id,
                    'created_by': sample_status.created_by,
                    'created_date': safe_datetime_to_string(sample_status.created_date),
                    'lastmodified_by': sample_status.lastmodified_by,
                    'lastmodified_date': safe_datetime_to_string(sample_status.lastmodified_date),
                    'patient_id': patient_id,
                    'patientname': patient_name,
                    'age': age,
                    'gender': gender,
                    'barcode': barcode,
                    'date': safe_datetime_to_string(sample_status.date),
                    'testdetails': updated_tests,
                    'data_source': 'hms_django_model'
                })
        
        # Process Regular samples
        if source in ['regular', 'all']:
            for barcode, data in sample_data_by_barcode.items():
                if data['source'] != 'regular':
                    continue
                
                sample_status = data['sample']
                testdetails = parse_testdetails(sample_status.testdetails)
                
                filtered_tests = [
                    test for test in testdetails
                    if test.get('samplestatus') in ['Outsource']
                ]
                
                if not filtered_tests:
                    continue
                
                # Use cached barcode data
                barcode_details = regular_barcodes_dict.get(barcode)
                if barcode_details:
                    patient_name = barcode_details.patientname
                    patient_id = barcode_details.patient_id
                    age = barcode_details.age
                    gender = barcode_details.gender
                    is_emergency = barcode_details.is_emergency
                    patient_history = barcode_details.patient_history
                else:
                    patient_name = "Unknown Patient"
                    patient_id = sample_status.patient_id if hasattr(sample_status, 'patient_id') else "Unknown ID"
                    age = "Unknown"
                    gender = "Unknown"
                
                # Match test values
                updated_tests = [
                    match_test_values(test, barcode, test_values_by_barcode)
                    for test in filtered_tests
                ]
                
                combined_results.append({
                    'id': sample_status.id,
                    'created_by': sample_status.created_by,
                    'created_date': safe_datetime_to_string(sample_status.created_date),
                    'lastmodified_by': sample_status.lastmodified_by,
                    'lastmodified_date': safe_datetime_to_string(sample_status.lastmodified_date),
                    'patient_id': patient_id,
                    'patientname': patient_name,
                    'age': age,
                    'gender': gender,
                    'is_emergency': is_emergency,
                    'patient_history': patient_history,
                    'barcode': barcode,
                    'date': safe_datetime_to_string(sample_status.date),
                    'testdetails': updated_tests,
                    'data_source': 'regular_django_model'
                })
        
        # ============================================
        # CHC MongoDB Processing (Optimized)
        # ============================================
        if source in ['chc', 'all']:
            try:
                client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
                corp = client.Corporatehealthcheckup
                sample_collection = corp.core_sample
                billing_collection = corp.core_billing
                patient_collection = corp.core_employeeregistration

                chc_query = {
                    "created_date": {"$gte": start_of_range, "$lt": end_of_range}
                }
                chc_samples = list(sample_collection.find(chc_query).sort("created_date", -1))
                
                # Bulk fetch CHC barcodes
                chc_barcodes = [s.get('barcode') for s in chc_samples if s.get('barcode')]
                
                # Bulk fetch billing and patient data
                billing_map = {b['barcode']: b for b in billing_collection.find({"barcode": {"$in": chc_barcodes}})}
                employee_ids = [b.get('employee_id') for b in billing_map.values() if b.get('employee_id')]
                patient_map = {p['employee_id']: p for p in patient_collection.find({"employee_id": {"$in": employee_ids}})}
                
                # Bulk fetch TestValues for CHC barcodes
                chc_test_values_by_barcode = {}
                if chc_barcodes:
                    chc_tvs = TestValue.objects.filter(barcode__in=chc_barcodes).order_by('barcode', '-created_date')
                    for tv in chc_tvs:
                        if tv.barcode not in chc_test_values_by_barcode:
                            chc_test_values_by_barcode[tv.barcode] = []
                        chc_test_values_by_barcode[tv.barcode].append(tv)
                
                chc_processed = {}
                for record in chc_samples:
                    barcode = record.get('barcode', '')
                    if not barcode or barcode in chc_processed:
                        continue
                    
                    testdetails = parse_testdetails(record.get('testdetails'))
                    filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Outsource']]
                    
                    if not filtered_tests:
                        continue
                    
                    billing = billing_map.get(barcode, {})
                    employee_id = billing.get("employee_id")
                    patient = patient_map.get(employee_id, {})
                    
                    updated_tests = [
                        match_test_values(test, barcode, chc_test_values_by_barcode)
                        for test in filtered_tests
                    ]
                    
                    combined_results.append({
                        'id': str(record.get('_id')),
                        'created_by': record.get('created_by', ''),
                        'created_date': safe_datetime_to_string(record.get('created_date')),
                        'lastmodified_by': record.get('lastmodified_by', ''),
                        'lastmodified_date': safe_datetime_to_string(record.get('lastmodified_date')),
                        'barcode': barcode,
                        'company_id': record.get('company_id', ''),
                        'patient_id': employee_id or 'Unknown ID',
                        'patientname': patient.get("employee_name", "Unknown Patient"),
                        'date': safe_datetime_to_string(record.get('date')),
                        'age': patient.get("age", "Unknown"),
                        'gender': patient.get("gender", "Unknown"),
                        'testdetails': updated_tests,
                        'data_source': 'chc_mongodb'
                    })
                    chc_processed[barcode] = True
                
                client.close()
            except Exception as e:
                print(f"CHC MongoDB error: {str(e)}")
        
        # ============================================
        # Regular MongoDB Processing (Optimized)
        # ============================================
        if source in ['regular', 'all']:
            try:
                client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
                db = client.franchise
                sample_collection = db.franchise_sample
                billing_collection = db.franchise_billing
                patient_collection = db.franchise_patient
                
                mongo_query = {
                    "created_date": {"$gte": start_of_range, "$lt": end_of_range}
                }
                mongodb_samples = list(sample_collection.find(mongo_query).sort("created_date", -1))
                
                # Bulk fetch billing and patient data
                mongo_barcodes = [s.get('barcode') for s in mongodb_samples if s.get('barcode')]
                billing_map = {b['barcode']: b for b in billing_collection.find({"barcode": {"$in": mongo_barcodes}})}
                patient_ids = [b.get('patient_id') for b in billing_map.values() if b.get('patient_id')]
                patient_map = {p['patient_id']: p for p in patient_collection.find({"patient_id": {"$in": patient_ids}})}
                
                # Bulk fetch TestValues
                mongo_test_values_by_barcode = {}
                if mongo_barcodes:
                    mongo_tvs = TestValue.objects.filter(barcode__in=mongo_barcodes).order_by('barcode', '-created_date')
                    for tv in mongo_tvs:
                        if tv.barcode not in mongo_test_values_by_barcode:
                            mongo_test_values_by_barcode[tv.barcode] = []
                        mongo_test_values_by_barcode[tv.barcode].append(tv)
                
                mongo_processed = {}
                for record in mongodb_samples:
                    barcode = record.get('barcode', '')
                    if not barcode or barcode in all_barcodes or barcode in mongo_processed:
                        continue
                    
                    testdetails = parse_testdetails(record.get('testdetails'))
                    filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Outsource']]
                    
                    if not filtered_tests:
                        continue
                    
                    patient_id = billing_map.get(barcode, {}).get('patient_id')
                    patient = patient_map.get(patient_id, {})
                    
                    updated_tests = [
                        match_test_values(test, barcode, mongo_test_values_by_barcode)
                        for test in filtered_tests
                    ]
                    
                    combined_results.append({
                        'id': str(record.get('_id', '')),
                        'created_by': record.get('created_by', ''),
                        'created_date': safe_datetime_to_string(record.get('created_date')),
                        'lastmodified_by': record.get('lastmodified_by', ''),
                        'lastmodified_date': safe_datetime_to_string(record.get('lastmodified_date')),
                        'patient_id': patient_id or 'Unknown ID',
                        'patientname': patient.get('patientname', 'Unknown Patient'),
                        'age': patient.get('age', 'Unknown'),
                        'gender': patient.get('gender', 'Unknown'),
                        'phoneNumber': patient.get('phoneNumber', ''),
                        'email': patient.get('email', ''),
                        'city': patient.get('city', ''),
                        'area': patient.get('area', ''),
                        'pincode': patient.get('pincode', ''),
                        'dateOfBirth': patient.get('dateOfBirth', ''),
                        'barcode': barcode,
                        'company_id': record.get('franchise_id', ''),
                        'date': safe_datetime_to_string(record.get('created_date')),
                        'testdetails': updated_tests,
                        'data_source': 'mongodb'
                    })
                    mongo_processed[barcode] = True
                
                client.close()
            except Exception as e:
                print(f"MongoDB error: {str(e)}")
        
        # Sort results
        combined_results.sort(key=lambda x: x.get('date', ''), reverse=True)
        
        return Response(combined_results, status=status.HTTP_200_OK)
        
    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def os_compare_test_details(request):
    """
    Simplified endpoint - only returns test structure from core_testdetails
    No patient data fetching - frontend passes that data
    """
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    core_testdetails_collection = db.core_testdetails

    barcode = request.GET.get('barcode')
    test_name_filter = request.GET.get('test_name')
    test_id = request.GET.get('test_id')

    if not barcode:
        return JsonResponse({'error': 'Barcode parameter is required'}, status=400)

    final_test_data = []

    def normalize_parameters(parameters):
        """Normalize parameters to a dict keyed by device-id string -> list of parameter dicts."""
        if not parameters:
            return {}
        if isinstance(parameters, dict):
            normalized = {}
            for k, v in parameters.items():
                if v is None:
                    normalized[str(k)] = []
                elif isinstance(v, list):
                    normalized[str(k)] = v
                elif isinstance(v, dict):
                    normalized[str(k)] = [v]
                else:
                    normalized[str(k)] = [v] if v is not None else []
            return normalized
        if isinstance(parameters, list):
            return {"default": parameters}
        return {}

    # Build query for core_testdetails
    query = {}
    if test_name_filter:
        query["test_name"] = test_name_filter
    if test_id:
        query["test_id"] = test_id

    # Get test details from core_testdetails
    if test_name_filter or test_id:
        test_details_list = list(core_testdetails_collection.find(query))
    else:
        # If no filter, return error
        client.close()
        return JsonResponse({
            'error': 'Either test_name or test_id parameter is required'
        }, status=400)

    if not test_details_list:
        client.close()
        return JsonResponse({
            'error': f'No test details found for the given parameters'
        }, status=404)

    # Process each test detail
    for test_detail in test_details_list:
        test_name = test_detail.get("test_name")
        test_id = test_detail.get("test_id")
        
        raw_parameters = test_detail.get("parameters", {})
        parameters = normalize_parameters(raw_parameters)

        # Handle tests without parameters
        if not parameters:
            test_code = test_detail.get("test_code", f"{(test_name or '').replace(' ', '').upper()}01")
            final_test_data.append({
                "barcode": barcode,
                "test_id": test_id,
                "testname": test_name,
                "test_code": test_code,
                "parameter_name": None,
                "unit": test_detail.get("unit", "N/A"),
                "reference_range": test_detail.get("reference_range", "N/A"),
                "method": test_detail.get("method", "N/A"),
                "department": test_detail.get("department", "N/A"),
                "specimen_type": test_detail.get("specimen_type", "N/A"),
                "NABL": test_detail.get("NABL", "N/A"),
                "test_value": "",
                "sub_title": None,
                "value_option": test_detail.get("value_option", []),
            })
            continue

        # Handle parameterized tests - use first available device
        selected_device = sorted(parameters.keys())[0] if parameters else None

        if selected_device and selected_device in parameters:
            param_list = parameters[selected_device] or []

            for param in param_list:
                if not isinstance(param, dict):
                    continue

                test_code = param.get("test_code")
                if not test_code:
                    continue

                final_test_data.append({
                    "barcode": barcode,
                    "device_id": selected_device,
                    "test_id": test_id,
                    "testname": test_name,
                    "test_code": test_code,
                    "parameter_name": param.get("test_name"),
                    "unit": param.get("unit"),
                    "reference_range": param.get("reference_range"),
                    "method": param.get("method"),
                    "department": test_detail.get("department"),
                    "specimen_type": test_detail.get("specimen_type", param.get("specimen_type")),
                    "NABL": test_detail.get("NABL", "N/A"),
                    "test_value": "",
                    "sub_title": param.get("sub_title"),
                    "value_option": param.get("value_option"),
                })

    client.close()

    if not final_test_data:
        return JsonResponse({
            'error': f'No test structure found'
        }, status=404)

    response_data = {
        'success': True,
        'barcode': barcode,
        'test_count': len(final_test_data),
        'data': final_test_data,
    }
    return Response(response_data, status=200)


@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def os_save_test_value(request):
    """
    Save test values to TestValue model
    """
    print(f"DEBUG: Request received: Method={request.method}, User={request.user}")
    
    if request.method == 'POST':
        payload = request.data
        employee_id = payload.get('auth-user-id')
        
        try:
            test_details_json = payload.get("testdetails", [])
            barcode = payload.get("barcode")
            locationId = payload.get("locationId")
            
            if not isinstance(test_details_json, list) or not test_details_json:
                return Response({"error": "Invalid test details format"}, status=status.HTTP_400_BAD_REQUEST)
            
            for test in test_details_json:
                testname = test.get('testname')
                if not testname:
                    return Response({"error": "Missing testname in test details"}, status=status.HTTP_400_BAD_REQUEST)
            
            # Create the TestValue record
            test_value_record = TestValue.objects.create(
                created_by=employee_id,
                date=payload.get('date'),
                barcode=barcode,
                locationId=locationId,
                testdetails=test_details_json,
            )
            
            return Response({
                "message": "Test details saved successfully.",
                "test_value_id": test_value_record.id
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            print(f"DEBUG: POST error: {str(e)}")
            return Response({"error": f"An error occurred: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)