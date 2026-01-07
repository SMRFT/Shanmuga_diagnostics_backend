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
def get_samplestatus_testvalue(request):
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
                    if test.get('samplestatus') in ['Received']
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
                    if test.get('samplestatus') in ['Received']
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
                    filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Received']]
                    
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
                    filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Received']]
                    
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
def compare_test_details(request):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    core_testdetails_collection = db.core_testdetails
    interface_testvalue_collection = db.interface_testvalue

    franchise_client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    franchise_db = franchise_client.franchise
    corporate_db = franchise_client.Corporatehealthcheckup
    franchise_collection = franchise_db.franchise_sample
    corporate_billing_collection = corporate_db.core_billing
    corporate_sample_collection = corporate_db.core_sample

    barcode = request.GET.get('barcode')
    device_id = request.GET.get('device_id')
    source = request.GET.get('source', 'all')
    test_name_filter = request.GET.get('test_name')

    if not barcode:
        return JsonResponse({'error': 'Barcode parameter is required'}, status=400)

    patient_id = None
    patient_name = None
    final_test_data = []
    processed_records = []

    # HMS processing
    if source in ['hms', 'all']:
        hms_patient_id = None
        hms_patient_name = None
        hms_test_list = []
        try:
            barcode_obj = Hmsbarcode.objects.get(barcode=barcode)
            hms_patient_id = (
                getattr(barcode_obj, "patient_id", None)
                or getattr(barcode_obj, "patientid", None)
                or getattr(barcode_obj, "billnumber", f"HMS_UNKNOWN_{barcode}")
            )
            hms_patient_name = getattr(barcode_obj, "patientname", f"HMS Unknown Patient {barcode}")
            try:
                sample_status_obj = Hmssamplestatus.objects.get(barcode=barcode)
                if isinstance(sample_status_obj.testdetails, str):
                    hms_test_list = json.loads(sample_status_obj.testdetails)
                elif isinstance(sample_status_obj.testdetails, list):
                    hms_test_list = sample_status_obj.testdetails
            except Hmssamplestatus.DoesNotExist:
                hms_test_list = []
        except Hmsbarcode.DoesNotExist:
            interface_record = interface_testvalue_collection.find_one(
                {"Barcode": barcode}, sort=[("Receiveddate", -1)]
            )
            if interface_record:
                hms_patient_id = interface_record.get('patient_id', f'HMS_UNKNOWN_{barcode}')
                hms_patient_name = interface_record.get('patientname', f'HMS Unknown Patient {barcode}')
                unique_tests = interface_testvalue_collection.distinct("TestCode", {"Barcode": barcode})
                for test_code in unique_tests:
                    test_detail = core_testdetails_collection.find_one({"test_code": test_code})
                    test_name = test_detail.get('test_name', test_code) if test_detail else test_code
                    hms_test_list.append({'testname': test_name, 'test_id': test_code})

        if test_name_filter and hms_test_list:
            hms_test_list = [test for test in hms_test_list 
                           if (test.get('testname') == test_name_filter or 
                               test.get('test_name') == test_name_filter)]

        hms_sample_status_map = {}
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
                    hms_sample_status_map[test_name] = {'status': sample_status, 'source': 'hms_django_model'}
        except (Hmssamplestatus.DoesNotExist, json.JSONDecodeError):
            pass

        if hms_test_list:
            if not patient_id:
                patient_id = hms_patient_id
                patient_name = hms_patient_name
            hms_test_data = process_test_data(
                hms_test_list, hms_sample_status_map, hms_patient_id, hms_patient_name,
                barcode, device_id, core_testdetails_collection,
                interface_testvalue_collection, 'hms', test_name_filter
            )
            final_test_data.extend(hms_test_data['test_data'])
            processed_records.extend(hms_test_data['processed_records'])

    # Corporate Health Checkup processing
    if source in ['corporate', 'all']:
        corporate_patient_id = None
        corporate_patient_name = None
        corporate_test_list = []
        corporate_sample_status_map = {}

        billing_record = corporate_billing_collection.find_one({"barcode": barcode})
        if billing_record:
            corporate_patient_id = billing_record.get('patient_id')
            corporate_patient_name = billing_record.get('patientname')
            testdetails = billing_record.get('testdetails', [])
            if isinstance(testdetails, str):
                try:
                    corporate_test_list = json.loads(testdetails)
                except json.JSONDecodeError:
                    corporate_test_list = []
            elif isinstance(testdetails, list):
                corporate_test_list = testdetails

        if test_name_filter and corporate_test_list:
            corporate_test_list = [test for test in corporate_test_list 
                                 if (test.get('testname') == test_name_filter or 
                                     test.get('test_name') == test_name_filter)]

        try:
            sample_status_detail = corporate_sample_collection.find_one({"barcode": barcode})
            if sample_status_detail:
                sample_testdetails = sample_status_detail.get('testdetails', [])
                if isinstance(sample_testdetails, str):
                    sample_testdetails = json.loads(sample_testdetails)
                for test in sample_testdetails:
                    test_name = test.get('testname')
                    sample_status = test.get('samplestatus')
                    if test_name:
                        corporate_sample_status_map[test_name] = {
                            'status': sample_status,
                            'source': 'corporate_mongodb'
                        }
        except Exception:
            pass

        if corporate_test_list:
            if not patient_id:
                patient_id = corporate_patient_id
                patient_name = corporate_patient_name
            corporate_test_data = process_test_data(
                corporate_test_list, corporate_sample_status_map, corporate_patient_id,
                corporate_patient_name, barcode, device_id, core_testdetails_collection,
                interface_testvalue_collection, 'corporate', test_name_filter
            )
            final_test_data.extend(corporate_test_data['test_data'])
            processed_records.extend(corporate_test_data['processed_records'])

    # Regular (Franchise) processing
    if source in ['regular', 'all']:
        regular_patient_id = None
        regular_patient_name = None
        regular_test_list = []

        franchise_sample = franchise_collection.find_one({"barcode": barcode})
        if franchise_sample:
            regular_patient_id = franchise_sample.get('patient_id')
            regular_patient_name = franchise_sample.get('patientname')
            try:
                testdetails = franchise_sample.get('testdetails', [])
                if isinstance(testdetails, str):
                    regular_test_list = json.loads(testdetails)
                elif isinstance(testdetails, list):
                    regular_test_list = testdetails
                else:
                    regular_test_list = []
            except json.JSONDecodeError:
                regular_test_list = []
        else:
            try:
                barcode_test_detail = BarcodeTestDetails.objects.get(barcode=barcode)
                regular_patient_id = barcode_test_detail.patient_id
                regular_patient_name = barcode_test_detail.patientname
                try:
                    if isinstance(barcode_test_detail.testdetails, str):
                        regular_test_list = json.loads(barcode_test_detail.testdetails)
                    elif isinstance(barcode_test_detail.testdetails, list):
                        regular_test_list = barcode_test_detail.testdetails
                except json.JSONDecodeError:
                    regular_test_list = []
            except BarcodeTestDetails.DoesNotExist:
                if source == 'regular':
                    interface_record = interface_testvalue_collection.find_one(
                        {"Barcode": barcode}, sort=[("Receiveddate", -1)]
                    )
                    if interface_record:
                        regular_patient_id = interface_record.get('patient_id', f'REG_UNKNOWN_{barcode}')
                        regular_patient_name = interface_record.get('patientname', f'Regular Unknown Patient {barcode}')
                        unique_tests = interface_testvalue_collection.distinct("TestCode", {"Barcode": barcode})
                        for test_code in unique_tests:
                            test_detail = core_testdetails_collection.find_one({"test_code": test_code})
                            test_name = test_detail.get('test_name', test_code) if test_detail else test_code
                            regular_test_list.append({'test_name': test_name, 'test_id': test_code})

        if test_name_filter and regular_test_list:
            regular_test_list = [test for test in regular_test_list 
                               if (test.get('testname') == test_name_filter or 
                                   test.get('test_name') == test_name_filter)]

        regular_sample_status_map = {}
        try:
            sample_status_detail = SampleStatus.objects.get(barcode=barcode)
            if isinstance(sample_status_detail.testdetails, str):
                sample_test_list = json.loads(sample_status_detail.testdetails)
            elif isinstance(sample_status_detail.testdetails, list):
                sample_test_list = sample_status_detail.testdetails
            else:
                sample_test_list = []
            for sample_test in sample_test_list:
                test_name = sample_test.get('testname')
                sample_status = sample_test.get('samplestatus')
                if test_name:
                    regular_sample_status_map[test_name] = {'status': sample_status, 'source': 'regular_django_model'}
        except (SampleStatus.DoesNotExist, json.JSONDecodeError):
            sample_test_list = []

        try:
            franchise_samples = franchise_collection.find({
                "barcode": barcode,
                "testdetails": {"$exists": True, "$ne": None}
            })
            for sample in franchise_samples:
                try:
                    testdetails = sample.get('testdetails', [])
                    if isinstance(testdetails, str):
                        franchise_test_list = json.loads(testdetails)
                    elif isinstance(testdetails, list):
                        franchise_test_list = testdetails
                    else:
                        continue
                except json.JSONDecodeError:
                    continue
                for sample_test in franchise_test_list:
                    test_name = sample_test.get('testname')
                    sample_status = sample_test.get('samplestatus')
                    if test_name and test_name not in regular_sample_status_map:
                        regular_sample_status_map[test_name] = {'status': sample_status, 'source': 'mongodb_franchise'}
        except Exception as franchise_error:
            print(f"Franchise MongoDB connection error: {str(franchise_error)}")

        if regular_test_list:
            if not patient_id:
                patient_id = regular_patient_id
                patient_name = regular_patient_name
            regular_test_data = process_test_data(
                regular_test_list, regular_sample_status_map, regular_patient_id, regular_patient_name,
                barcode, device_id, core_testdetails_collection, interface_testvalue_collection, 'regular', test_name_filter
            )
            final_test_data.extend(regular_test_data['test_data'])
            processed_records.extend(regular_test_data['processed_records'])

    try:
        client.close()
        franchise_client.close()
    except:
        pass

    if not final_test_data:
        error_msg = f'No data found for barcode: {barcode}'
        if test_name_filter:
            error_msg += f' and test: {test_name_filter}'
        error_msg += ' in any collection'
        return JsonResponse({'error': error_msg}, status=404)

    response_data = {
        'success': True,
        'patient_info': {
            'patient_id': patient_id,
            'patient_name': patient_name,
            'barcode': barcode
        },
        'test_count': len(final_test_data),
        'data': final_test_data,
        'processed_records': processed_records,
        'data_sources': list(set([item.get('data_source') for item in final_test_data if item.get('data_source')])),
        'filtered_by_test': test_name_filter if test_name_filter else None
    }
    return Response(response_data, status=200)


def process_test_data(
    test_list,
    sample_status_map,
    patient_id,
    patient_name,
    barcode,
    device_id,
    core_testdetails_collection,
    interface_testvalue_collection,
    data_source_type,
    test_name_filter=None,
):
    """
    Helper function to process test data for HMS, Corporate, and Regular sources
    - Now includes sub_title and value_option for parameters
    """
    final_test_data = []
    processed_records = []

    def to_iso_or_str(dt):
        if not dt:
            return None
        if hasattr(dt, "isoformat"):
            try:
                return dt.isoformat()
            except Exception:
                return str(dt)
        if isinstance(dt, str):
            return dt
        return str(dt)

    def normalize_parameters(parameters):
        """
        Normalize parameters to a dict keyed by device-id string -> list of parameter dicts.
        """
        if parameters is None:
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

    for test_item in test_list:
        test_name = test_item.get("test_name") or test_item.get("testname")
        test_id = test_item.get("test_id")

        # SKIP if test_name_filter is provided and doesn't match
        if test_name_filter and test_name != test_name_filter:
            continue

        sample_status_info = sample_status_map.get(test_name, {"status": "Unknown", "source": "none"})
        sample_status = sample_status_info["status"]
        data_source = sample_status_info["source"]

        if sample_status in ("Received", "Unknown"):
            # Try exact match test_name + test_id
            query = {"test_name": test_name, "test_id": test_id}
            test_details_list = list(core_testdetails_collection.find(query))

            # Fallback to test_id
            if not test_details_list and test_id:
                test_details_list = list(core_testdetails_collection.find({"test_id": test_id}))

            # Fallback to test_name
            if not test_details_list:
                candidates = list(core_testdetails_collection.find({"test_name": test_name}))
                if len(candidates) > 1 and test_id:
                    picked = None
                    for detail in candidates:
                        if detail.get("test_id") == test_id:
                            picked = detail
                            break
                    test_details_list = [picked] if picked else candidates
                else:
                    test_details_list = candidates

            test_found = False
            for test_detail in test_details_list:
                test_found = True

                raw_parameters = test_detail.get("parameters", {})
                parameters = normalize_parameters(raw_parameters)

                print(f"DEBUG [{data_source_type}]: Processing test_id {test_id}, test_name: {test_name}")
                print(f"DEBUG [{data_source_type}]: parameters type={type(raw_parameters).__name__}, normalized keys={list(parameters.keys())}")

                # Handle tests without parameters
                if not parameters:
                    test_code = test_detail.get("test_code", f"{(test_name or '').replace(' ', '').upper()}01")

                    # FOCUSED QUERY: Only query for this specific test code and barcode
                    test_value_query = {
                        "Barcode": barcode,
                        "TestCode": test_code,
                        "processingstatus": "pending",
                    }
                    if device_id:
                        test_value_query["DeviceID"] = device_id

                    test_value_doc = interface_testvalue_collection.find_one(
                        test_value_query, sort=[("Receiveddate", -1)]
                    )

                    if test_value_doc:
                        test_value = test_value_doc.get("Value", "")
                        processing_status = test_value_doc.get("processingstatus", "N/A")
                        device_id_used = test_value_doc.get("DeviceID", "N/A")

                        processed_records.append(
                            {
                                "barcode": barcode,
                                "test_code": test_code,
                                "device_id": device_id_used,
                                "record_id": str(test_value_doc.get("_id")),
                                "data_source_type": data_source_type,
                            }
                        )
                    else:
                        test_value = ""
                        processing_status = "N/A"
                        device_id_used = "N/A"

                    created_date = to_iso_or_str(test_value_doc.get("CreatedDate") if test_value_doc else None)
                    received_date = to_iso_or_str(test_value_doc.get("Receiveddate") if test_value_doc else None)

                    test_info = {
                        "patient_id": patient_id,
                        "patientname": patient_name,
                        "barcode": barcode,
                        "device_id": device_id_used,
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
                        "test_value": test_value,
                        "processing_status": processing_status,
                        "sample_status": sample_status,
                        "data_source": data_source,
                        "data_source_type": data_source_type,
                        "lab_unique_id": test_value_doc.get("lab_unique_id", "N/A") if test_value_doc else "N/A",
                        "created_date": created_date,
                        "received_date": received_date,
                        "sub_title": None,
                        "value_option": test_detail.get("value_option", []),  # ADD THIS LINE - Extract value_option from test_detail
                    }
                    final_test_data.append(test_info)
                    continue

                # DEVICE SELECTION LOGIC FOR PARAMETERIZED TESTS
                has_interface_data = False
                selected_device = None

                # FOCUSED QUERY: Get all test codes for this specific test from parameters
                all_test_codes_for_this_test = []
                for device_key in parameters:
                    param_test_codes = [
                        p.get("test_code") for p in parameters[device_key] 
                        if isinstance(p, dict) and p.get("test_code")
                    ]
                    all_test_codes_for_this_test.extend(param_test_codes)

                # Remove duplicates
                all_test_codes_for_this_test = list(set(all_test_codes_for_this_test))
                
                # FOCUSED QUERY: Only get records for this barcode and these specific test codes
                focused_query = {
                    "Barcode": barcode,
                    "TestCode": {"$in": all_test_codes_for_this_test},
                    "processingstatus": "pending"
                }
                
                all_barcode_records = list(interface_testvalue_collection.find(focused_query))
                print(f"DEBUG [{data_source_type}]: Found {len(all_barcode_records)} focused records for test {test_name}")

                interface_test_codes = []
                interface_device_ids = []
                if all_barcode_records:
                    interface_test_codes = [
                        r.get("TestCode") for r in all_barcode_records if r.get("TestCode")
                    ]
                    interface_device_ids = list(
                        set([str(r.get("DeviceID")) for r in all_barcode_records if r.get("DeviceID")])
                    )

                    print(f"DEBUG [{data_source_type}]: Interface test codes: {interface_test_codes}")
                    print(f"DEBUG [{data_source_type}]: Interface device IDs: {interface_device_ids}")
                    print(f"DEBUG [{data_source_type}]: Available parameter devices: {list(parameters.keys())}")

                    best_match_device = None
                    best_match_count = 0

                    # Score devices by test code overlap
                    for device_key in parameters:
                        param_test_codes = [
                            p.get("test_code") for p in parameters[device_key] if isinstance(p, dict) and p.get("test_code")
                        ]
                        matches = len(set(param_test_codes) & set(interface_test_codes))
                        print(f"DEBUG [{data_source_type}]: Device {device_key} - Matches with interface: {matches}")
                        if matches > best_match_count:
                            best_match_count = matches
                            best_match_device = device_key

                    # Direct device id matches (ensure str)
                    for interface_dev_id in interface_device_ids:
                        if interface_dev_id in parameters:
                            param_test_codes = [
                                p.get("test_code") for p in parameters[interface_dev_id] if isinstance(p, dict) and p.get("test_code")
                            ]
                            matches = len(set(param_test_codes) & set(interface_test_codes))
                            print(f"DEBUG [{data_source_type}]: Direct device match {interface_dev_id} - Matches: {matches}")
                            if matches > best_match_count:
                                best_match_count = matches
                                best_match_device = interface_dev_id

                    if best_match_device and best_match_count > 0:
                        selected_device = best_match_device
                        has_interface_data = True
                        print(f"DEBUG [{data_source_type}]: SELECTED DEVICE: {selected_device} with {best_match_count} matching test codes")
                    else:
                        # No matches found: honor requested device if present
                        if device_id and str(device_id) in parameters:
                            selected_device = str(device_id)
                            print(f"DEBUG [{data_source_type}]: Using requested device {selected_device} (no test code matches)")
                        else:
                            # Use first available device key
                            selected_device = sorted(parameters.keys())[0] if parameters else None
                            print(f"DEBUG [{data_source_type}]: Using default device {selected_device} (no matches found)")
                else:
                    # No interface data found
                    if device_id and str(device_id) in parameters:
                        selected_device = str(device_id)
                    else:
                        selected_device = sorted(parameters.keys())[0] if parameters else None
                    print(f"DEBUG [{data_source_type}]: Using device {selected_device} (no interface data)")

                if selected_device and selected_device in parameters:
                    param_list = parameters[selected_device] or []
                    print(f"DEBUG [{data_source_type}]: Processing {len(param_list)} parameters for device: {selected_device}")

                    for param in param_list:
                        if not isinstance(param, dict):
                            continue

                        test_code = param.get("test_code")
                        if not test_code:
                            continue

                        test_value = ""
                        processing_status = "No Data"
                        lab_unique_id = "N/A"
                        created_date = None
                        received_date = None

                        if has_interface_data:
                            matching_record = None
                            for record in all_barcode_records:
                                if record.get("TestCode") == test_code and record.get("processingstatus") == "pending":
                                    matching_record = record
                                    break

                            if matching_record:
                                processed_records.append(
                                    {
                                        "barcode": barcode,
                                        "test_code": test_code,
                                        "device_id": matching_record.get("DeviceID"),
                                        "record_id": str(matching_record.get("_id")),
                                        "data_source_type": data_source_type,
                                    }
                                )
                                test_value = matching_record.get("Value", "")
                                processing_status = matching_record.get("processingstatus", "pending")
                                lab_unique_id = matching_record.get("lab_unique_id", "N/A")
                                created_date = matching_record.get("CreatedDate")
                                received_date = matching_record.get("Receiveddate")
                                print(f"DEBUG [{data_source_type}]: Found data for {test_code}: Value={test_value}")
                            else:
                                print(f"DEBUG [{data_source_type}]: No interface data found for {test_code}")

                        created_date = to_iso_or_str(created_date)
                        received_date = to_iso_or_str(received_date)

                        # IMPORTANT: Include sub_title and value_option from parameter
                        test_info = {
                            "patient_id": patient_id,
                            "patientname": patient_name,
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
                            "test_value": test_value,
                            "processing_status": processing_status,
                            "sample_status": sample_status,
                            "data_source": data_source,
                            "data_source_type": data_source_type,
                            "lab_unique_id": lab_unique_id,
                            "created_date": created_date,
                            "received_date": received_date,
                            "sub_title": param.get("sub_title"),  # ADD sub_title
                            "value_option": param.get("value_option"),  # ADD value_option
                        }
                        final_test_data.append(test_info)

                # Avoid duplicates: process only the first matching test_detail
                break

            if not test_found:
                test_info = {
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
                    "data_source_type": data_source_type,
                    "lab_unique_id": "N/A",
                    "created_date": None,
                    "received_date": None,
                    "sub_title": None,
                    "value_option": None,
                }
                final_test_data.append(test_info)

    return {"test_data": final_test_data, "processed_records": processed_records}


    return {"test_data": final_test_data, "processed_records": processed_records}
    
def update_processing_status(barcode, test_code, device_id, latest_record_id_str):
    """
    Helper function to update processing status:
    - Mark the latest record as 'Completed'
    - Mark older records as 'Ignored'
    """
    client = None
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        interface_testvalue_collection = db.interface_testvalue
        
        # Convert string ID back to ObjectId
        try:
            latest_record_id = ObjectId(latest_record_id_str)
        except Exception as e:
            print(f"Invalid ObjectId format: {latest_record_id_str}, Error: {str(e)}")
            return False
        
        # Debug logging
        print(f"DEBUG: Updating processing status for:")
        print(f"  Barcode: {barcode}")
        print(f"  TestCode: {test_code}")
        print(f"  DeviceID: {device_id}")
        print(f"  RecordID: {latest_record_id_str}")
        
        # First, verify the record exists
        latest_record = interface_testvalue_collection.find_one({"_id": latest_record_id})
        if not latest_record:
            print(f"Record with ID {latest_record_id_str} not found")
            return False
        
        # Check current processing status
        current_status = latest_record.get('processingstatus', 'N/A')
        print(f"Current processing status: {current_status}")
        
        # Skip if already processed
        if current_status in ['Completed', 'Ignored']:
            print(f"Record already processed with status: {current_status}")
            return True
        
        # Update the latest record to 'Completed'
        update_result = interface_testvalue_collection.update_one(
            {
                "_id": latest_record_id,
                "Barcode": barcode,
                "TestCode": test_code,
                "DeviceID": device_id
            },
            {
                "$set": {
                    "processingstatus": "Completed",
                    "processesdate": datetime.utcnow()
                }
            }
        )
        
        if update_result.modified_count > 0:
            print(f"Successfully updated latest record to Completed")
        else:
            print(f"Failed to update latest record - criteria may not match")
            return False
        
        # Update older records for the same barcode, test_code, and device_id to 'Ignored'
        ignore_query = {
            "Barcode": barcode,
            "TestCode": test_code,
            "DeviceID": device_id,
            "_id": {"$ne": latest_record_id},
            "processingstatus": {"$nin": ["Ignored", "Completed"]}
        }
        
        count_to_ignore = interface_testvalue_collection.count_documents(ignore_query)
        print(f"Records to be marked as Ignored: {count_to_ignore}")
        
        if count_to_ignore > 0:
            ignore_result = interface_testvalue_collection.update_many(
                ignore_query,
                {
                    "$set": {
                        "processingstatus": "Ignored",
                        "processesdate": datetime.utcnow()
                    }
                }
            )
            print(f"Updated {ignore_result.modified_count} older records to Ignored")
        
        return True
        
    except Exception as e:
        print(f"Error updating processing status for barcode {barcode}, test_code {test_code}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if client:
            client.close()



            
@api_view([ 'POST'])
@permission_classes([HasRoleAndDataPermission])
def save_test_value(request):
    print(f"DEBUG: Request received: Method={request.method}, User={request.user}, Data={request.data}")
    
   
    if request.method == 'POST':
        payload = request.data
        employee_id = payload.get('auth-user-id')
        print(f"DEBUG: POST payload: {payload}")
        try:
           
            test_details_json = payload.get("testdetails", [])
            barcode = payload.get("barcode")
            locationId = payload.get("locationId")
            
            processed_records = payload.get("processed_records", [])
            
            print(f"DEBUG POST: Received {len(processed_records)} processed records")
            
            if not isinstance(test_details_json, list) or not test_details_json:
                return Response({"error": "Invalid test details format"}, status=status.HTTP_400_BAD_REQUEST)
            
            for test in test_details_json:
                testname = test.get('testname')
                if not testname:
                    return Response({"error": "Missing testname in test details"}, status=status.HTTP_400_BAD_REQUEST)
            
            test_value_record = TestValue.objects.create(
              
                created_by=employee_id,
                date=payload.get('date'),
                barcode=barcode,
                locationId=locationId,
                testdetails=test_details_json,
            )
            
            update_success_count = 0
            update_errors = []
            
            for record in processed_records:
                try:
                    success = update_processing_status(
                        record['barcode'],
                        record['test_code'],
                        record['device_id'],
                        record['record_id']
                    )
                    if success:
                        update_success_count += 1
                    else:
                        update_errors.append(f"Failed to update record {record['record_id']}")
                except Exception as e:
                    error_msg = f"Error updating record {record['record_id']}: {str(e)}"
                    print(error_msg)
                    update_errors.append(error_msg)
            
            response_message = "Test details saved successfully."
            if processed_records:
                response_message += f" Updated processing status for {update_success_count}/{len(processed_records)} records."
                if update_errors:
                    response_message += f" Errors: {'; '.join(update_errors[:3])}"
            
            return Response({
                "message": response_message,
                "updated_records": update_success_count,
                "total_records": len(processed_records),
                "errors": update_errors if update_errors else None
            }, status=status.HTTP_201_CREATED)
            
        except Patient.DoesNotExist:
            print("DEBUG: Patient not found")
            return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            print(f"DEBUG: POST error: {str(e)}")
            return Response({"error": f"An error occurred: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
