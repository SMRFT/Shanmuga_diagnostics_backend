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
        # Connect to MongoDB for test details
        # ============================================
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        test_details_collection = db.core_testdetails
        
        # Cache test details by test_id
        test_details_cache = {}
        
        def get_test_details(test_id):
            """Fetch test details from MongoDB and cache"""
            if test_id not in test_details_cache:
                test_detail = test_details_collection.find_one(
                    {"test_id": test_id},
                    {
                        "_id": 0,
                        "test_id": 1,
                        "test_name": 1,
                        "department": 1,
                        "collection_container": 1,
                        "TAT_Time": 1,
                    }
                )
                test_details_cache[test_id] = test_detail
            return test_details_cache[test_id]
        
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
        
        def enrich_test_with_details(test):
            """Enrich test with details from MongoDB"""
            test_id = test.get('test_id')
            if test_id:
                test_detail = get_test_details(test_id)
                if test_detail:
                    test['testname'] = test_detail.get('test_name', test.get('testname', 'N/A'))
                    test['department'] = test_detail.get('department', test.get('department', 'N/A'))
                    test['container'] = test_detail.get('collection_container', test.get('container', 'N/A'))
                    test['tat_time'] = test_detail.get('TAT_Time') or test.get('TAT_Time')
            return test
        
        def match_test_values(test, barcode, test_values_by_barcode):
                """Optimized test value matching with pre-fetched data using test_id"""
                test_id = test.get('test_id')
                
                # Initialize default values
                test.update({
                    'rerun': False,
                    'approve': False,
                    'dispatch': False,
                    'test_value_exists': False,
                    'dispatch_time': None,
                    'approve_time': None,
                    'rerun_time': None,
                    'approve_by': None,
                    'dispatch_by': None,
                    'value': None,
                    'remarks': None,
                    'comment': None,
                    'verified_by': None
                })
                
                # If no test_id, cannot match
                if not test_id:
                    return test
                
                # Use cached test values for this barcode
                test_values = test_values_by_barcode.get(barcode, [])
                
                for tv in test_values:
                    tv_details = parse_testdetails(tv.testdetails)
                    
                    for tv_test in tv_details:
                        tv_test_id = tv_test.get('test_id')
                        
                        # Match based on test_id
                        if tv_test_id == test_id:
                            test.update({
                                'test_value_exists': True,
                                'approve': bool(tv_test.get('approve', False)),
                                'rerun': bool(tv_test.get('rerun', False)),
                                'dispatch': bool(tv_test.get('dispatch', False)),
                                'approve_time': tv_test.get('approve_time'),
                                'rerun_time': tv_test.get('rerun_time'),
                                'dispatch_time': tv_test.get('dispatch_time'),
                                'approve_by': tv_test.get('approve_by'),
                                'dispatch_by': tv_test.get('dispatched_by'),
                                'value': tv_test.get('value'),
                                'remarks': tv_test.get('remarks'),
                                'comment': tv_test.get('comment'),
                                'verified_by': tv_test.get('verified_by')
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
                IPOPType = "Unknown"
                phone = "Unknown"
                ref_doctor = "Unknown"
                barcode_by = "Unknown"
                barcode_date = "Unknown"
                
                barcode_details = hms_barcodes_dict.get(barcode)
                if barcode_details:                    
                        patient_name = barcode_details.patientname
                        patient_id = barcode_details.patient_id
                        age = barcode_details.age
                        gender = barcode_details.gender
                        location_id = barcode_details.location_id
                        IPOPType = barcode_details.IPOPType
                        phone = barcode_details.phone
                        ref_doctor = barcode_details.ref_doctor
                        barcode_by = barcode_details.created_by
                        barcode_date = barcode_details.created_date
                elif hasattr(sample_status, 'patient_id'):
                    patient_id = sample_status.patient_id
                
                ALLOWED_DEPARTMENTS = ["General","Haematology","Coagulation", "Biochemistry", "Immunology", "Immunoassay", "Serology", "Clinical Pathology","Molecular Biology"]
                # Enrich tests with MongoDB details and match test values
                updated_tests = []
                for test in filtered_tests:
                    enriched_test = enrich_test_with_details(test)

                    # FILTER: Only allow Microbiology
                    if enriched_test.get('department') not in ALLOWED_DEPARTMENTS:
                        continue


                    matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                    updated_tests.append(matched_test)
                # Skip patient if no microbiology
                if not updated_tests:
                    continue
                
                combined_results.append({
                    'id': sample_status.id,
                    'created_by': sample_status.created_by,
                    'created_date': safe_datetime_to_string(sample_status.created_date),
                    'barcode_by': barcode_by,
                    'barcode_date': safe_datetime_to_string(barcode_date),
                    'lastmodified_by': sample_status.lastmodified_by,
                    'lastmodified_date': safe_datetime_to_string(sample_status.lastmodified_date),
                    'patient_id': patient_id,
                    'patientname': patient_name,
                    'age': age,
                    'gender': gender,
                    'phone': phone,
                    'ref_doctor': ref_doctor,
                    'location_id': location_id,
                    'opiptype': IPOPType,
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
                    barcode_by = barcode_details.created_by
                    barcode_date = barcode_details.created_date
                else:
                    patient_name = "Unknown Patient"
                    patient_id = sample_status.patient_id if hasattr(sample_status, 'patient_id') else "Unknown ID"
                    age = "Unknown"
                    gender = "Unknown"
                    barcode_by = "Unknown"
                    barcode_date = "Unknown"
                
                ALLOWED_DEPARTMENTS = ["General","Haematology","Coagulation", "Biochemistry", "Immunology", "Immunoassay", "Serology", "Clinical Pathology","Molecular Biology"]
                # Enrich tests with MongoDB details and match test values
                updated_tests = []
                for test in filtered_tests:
                    enriched_test = enrich_test_with_details(test)

                    # FILTER: Only allow Microbiology
                    if enriched_test.get('department') not in ALLOWED_DEPARTMENTS:
                        continue


                    matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                    updated_tests.append(matched_test)
                # Skip patient if no microbiology
                if not updated_tests:
                    continue
                
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
                    'barcode_by': barcode_by,
                    'barcode_date': safe_datetime_to_string(barcode_date),
                    'date': safe_datetime_to_string(sample_status.date),
                    'testdetails': updated_tests,
                    'data_source': 'regular_django_model'
                })
        
        # ============================================
        # CHC MongoDB Processing (Optimized)
        # ============================================
        if source in ['chc', 'all']:
            try:
                corp = client.Corporatehealthcheckup
                sample_collection = corp.core_sample
                billing_collection = corp.core_billing
                patient_collection = corp.core_chcregistration

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
                    
                    ALLOWED_DEPARTMENTS = ["General","Haematology","Coagulation", "Biochemistry", "Immunology", "Immunoassay", "Serology", "Clinical Pathology","Molecular Biology"]
                # Enrich tests with MongoDB details and match test values
                    updated_tests = []
                    for test in filtered_tests:
                        enriched_test = enrich_test_with_details(test)

                        # FILTER: Only allow Microbiology
                        if enriched_test.get('department') not in ALLOWED_DEPARTMENTS:
                            continue


                        matched_test = match_test_values(enriched_test, barcode, chc_test_values_by_barcode)
                        updated_tests.append(matched_test)
                    # Skip patient if no microbiology
                    if not updated_tests:
                        continue
                    
                    combined_results.append({
                        'id': str(record.get('_id')),
                        'created_by': record.get('created_by', ''),
                        'created_date': safe_datetime_to_string(record.get('created_date')),
                        'lastmodified_by': record.get('lastmodified_by', ''),
                        'lastmodified_date': safe_datetime_to_string(record.get('lastmodified_date')),
                        'barcode': barcode,
                        'location_id': record.get('company_id', ''),
                        'patient_id': employee_id or 'Unknown ID',
                        'patientname': patient.get("employee_name", "Unknown Patient"),
                        'date': safe_datetime_to_string(record.get('date')),
                        'age': patient.get("age", "Unknown"),
                        'gender': patient.get("gender", "Unknown"),
                        'testdetails': updated_tests,
                        'data_source': 'chc_mongodb'
                    })
                    chc_processed[barcode] = True
                
            except Exception as e:
                print(f"CHC MongoDB error: {str(e)}")
        
        # ============================================
        # Regular MongoDB Processing (Optimized)
        # ============================================
        if source in ['regular', 'all']:
            try:
                franchise_db = client.franchise
                sample_collection = franchise_db.franchise_sample
                billing_collection = franchise_db.franchise_billing
                patient_collection = franchise_db.franchise_patient
                
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
                    
                    ALLOWED_DEPARTMENTS = ["General","Haematology","Coagulation", "Biochemistry", "Immunology", "Immunoassay", "Serology", "Clinical Pathology","Molecular Biology"]
                    # Enrich tests with MongoDB details and match test values
                    updated_tests = []
                    for test in filtered_tests:
                        enriched_test = enrich_test_with_details(test)

                        # FILTER: Only allow Microbiology
                        if enriched_test.get('department') not in ALLOWED_DEPARTMENTS:
                            continue


                        matched_test = match_test_values(enriched_test, barcode, mongo_test_values_by_barcode)
                        updated_tests.append(matched_test)
                    # Skip patient if no microbiology
                    if not updated_tests:
                        continue
                    
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
                        'location_id': record.get('franchise_id', ''),
                        'date': safe_datetime_to_string(record.get('created_date')),
                        'testdetails': updated_tests,
                        'data_source': 'mongodb'
                    })
                    mongo_processed[barcode] = True
                
            except Exception as e:
                print(f"MongoDB error: {str(e)}")
        
        # Close MongoDB connection
        client.close()
        
        # Sort results
        combined_results.sort(key=lambda x: x.get('date', ''), reverse=True)
        
        return Response(combined_results, status=status.HTTP_200_OK)
        
    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    


def resolve_gender_fields(meta, gender):
    """
    Pick reference_range based on gender from male/female fields.
    Falls back to generic reference_range if gender-specific not available.
    Also returns low and high critical threshold fields.
    """
    gender_key = (gender or '').strip().lower()  # 'male', 'female', or ''

    if gender_key == 'male' and meta.get('male'):
        reference_range = meta['male']
    elif gender_key == 'female' and meta.get('female'):
        reference_range = meta['female']
    else:
        reference_range = meta.get('reference_range', '') or ''

    low  = meta.get('low',  '') or ''
    high = meta.get('high', '') or ''

    return reference_range, low, high

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

    barcode        = request.GET.get('barcode')
    device_id      = request.GET.get('device_id')
    source         = request.GET.get('source', 'all')
    test_id_filter = request.GET.get('test_id')
    gender         = request.GET.get('gender', '')

    if not barcode:
        return JsonResponse({'error': 'Barcode parameter is required'}, status=400)

    final_test_data   = []
    processed_records = []

    # ── HMS processing ────────────────────────────────────────────────────────
    if source in ['hms', 'all']:
        hms_test_list = []
        try:
            barcode_obj = Hmsbarcode.objects.get(barcode=barcode)
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
                unique_tests = interface_testvalue_collection.distinct("TestCode", {"Barcode": barcode})
                for test_code in unique_tests:
                    test_detail = core_testdetails_collection.find_one({"test_code": test_code})
                    if test_detail:
                        test_name = test_detail.get('test_name', test_code)
                        test_id   = test_detail.get('test_id')
                        hms_test_list.append({'testname': test_name, 'test_id': test_id})

        if test_id_filter and hms_test_list:
            hms_test_list = [t for t in hms_test_list if str(t.get('test_id')) == str(test_id_filter)]

        # ── Build HMS sample_status_map keyed by test_id ──────────────────
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
                test_id_key   = sample_test.get('test_id')       # ← test_id
                sample_status = sample_test.get('samplestatus')
                if test_id_key:
                    hms_sample_status_map[str(test_id_key)] = {
                        'status': sample_status,
                        'source': 'hms_django_model'
                    }
        except (Hmssamplestatus.DoesNotExist, json.JSONDecodeError):
            pass

        if hms_test_list:
            hms_test_data = process_test_data(
                hms_test_list, hms_sample_status_map,
                barcode, device_id, core_testdetails_collection,
                interface_testvalue_collection, 'hms', test_id_filter,
                gender=gender,
            )
            final_test_data.extend(hms_test_data['test_data'])
            processed_records.extend(hms_test_data['processed_records'])

    # ── Corporate Health Checkup processing ───────────────────────────────────
    if source in ['corporate', 'all']:
        corporate_test_list         = []
        corporate_sample_status_map = {}

        billing_record = corporate_billing_collection.find_one({"barcode": barcode})
        if billing_record:
            testdetails = billing_record.get('testdetails', [])
            if isinstance(testdetails, str):
                try:
                    corporate_test_list = json.loads(testdetails)
                except json.JSONDecodeError:
                    corporate_test_list = []
            elif isinstance(testdetails, list):
                corporate_test_list = testdetails

        if test_id_filter and corporate_test_list:
            corporate_test_list = [t for t in corporate_test_list if str(t.get('test_id')) == str(test_id_filter)]

        # ── Build Corporate sample_status_map keyed by test_id ───────────
        try:
            sample_status_detail = corporate_sample_collection.find_one({"barcode": barcode})
            if sample_status_detail:
                sample_testdetails = sample_status_detail.get('testdetails', [])
                if isinstance(sample_testdetails, str):
                    sample_testdetails = json.loads(sample_testdetails)
                for test in sample_testdetails:
                    test_id_key   = test.get('test_id')           # ← test_id
                    sample_status = test.get('samplestatus')
                    if test_id_key:
                        corporate_sample_status_map[str(test_id_key)] = {
                            'status': sample_status,
                            'source': 'corporate_mongodb'
                        }
        except Exception:
            pass

        if corporate_test_list:
            corporate_test_data = process_test_data(
                corporate_test_list, corporate_sample_status_map,
                barcode, device_id, core_testdetails_collection,
                interface_testvalue_collection, 'corporate', test_id_filter,
                gender=gender,
            )
            final_test_data.extend(corporate_test_data['test_data'])
            processed_records.extend(corporate_test_data['processed_records'])

    # ── Regular (Franchise) processing ───────────────────────────────────────
    if source in ['regular', 'all']:
        regular_test_list = []

        franchise_sample = franchise_collection.find_one({"barcode": barcode})
        if franchise_sample:
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
                        unique_tests = interface_testvalue_collection.distinct("TestCode", {"Barcode": barcode})
                        for test_code in unique_tests:
                            test_detail = core_testdetails_collection.find_one({"test_code": test_code})
                            if test_detail:
                                test_name = test_detail.get('test_name', test_code)
                                test_id   = test_detail.get('test_id')
                                regular_test_list.append({'test_name': test_name, 'test_id': test_id})

        if test_id_filter and regular_test_list:
            regular_test_list = [t for t in regular_test_list if str(t.get('test_id')) == str(test_id_filter)]

        # ── Build Regular sample_status_map keyed by test_id ─────────────
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
                test_id_key   = sample_test.get('test_id')       # ← test_id
                sample_status = sample_test.get('samplestatus')
                if test_id_key:
                    regular_sample_status_map[str(test_id_key)] = {
                        'status': sample_status,
                        'source': 'regular_django_model'
                    }
        except (SampleStatus.DoesNotExist, json.JSONDecodeError):
            pass

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
                    test_id_key   = sample_test.get('test_id')   # ← test_id
                    sample_status = sample_test.get('samplestatus')
                    if test_id_key and str(test_id_key) not in regular_sample_status_map:
                        regular_sample_status_map[str(test_id_key)] = {
                            'status': sample_status,
                            'source': 'mongodb_franchise'
                        }
        except Exception as franchise_error:
            print(f"Franchise MongoDB connection error: {str(franchise_error)}")

        if regular_test_list:
            regular_test_data = process_test_data(
                regular_test_list, regular_sample_status_map,
                barcode, device_id, core_testdetails_collection,
                interface_testvalue_collection, 'regular', test_id_filter,
                gender=gender,
            )
            final_test_data.extend(regular_test_data['test_data'])
            processed_records.extend(regular_test_data['processed_records'])

    try:
        client.close()
        franchise_client.close()
    except Exception:
        pass

    if not final_test_data:
        error_msg = f'No data found for barcode: {barcode}'
        if test_id_filter:
            error_msg += f' and test_id: {test_id_filter}'
        error_msg += ' in any collection'
        return JsonResponse({'error': error_msg}, status=404)

    response_data = {
        'success': True,
        'test_count': len(final_test_data),
        'data': final_test_data,
        'processed_records': processed_records,
        'data_sources': list(set([
            item.get('data_source') for item in final_test_data if item.get('data_source')
        ])),
        'filtered_by_test_id': test_id_filter if test_id_filter else None,
    }
    return Response(response_data, status=200)

def process_test_data(
    test_list,
    sample_status_map,
    barcode,
    device_id,
    core_testdetails_collection,
    interface_testvalue_collection,
    data_source_type,
    test_id_filter=None,
    gender='',
):
    final_test_data   = []
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

    # ── Pre-fetch all interface_testvalue records for this plain barcode ──────
    # This fetches records where Barcode starts with the plain barcode
    # e.g. "000759", "000759-F", "000759-P", "000759-R"
    all_interface_records = list(interface_testvalue_collection.find({
        "Barcode": {"$regex": f"^{barcode}"},
        "processingstatus": "pending",
    }))

    print(f"DEBUG [{data_source_type}]: Pre-fetched {len(all_interface_records)} interface records "
          f"for barcode prefix={barcode}")

    # ── Build a map: (test_code, suffix) → interface record ──────────────────
    # suffix is extracted from the Barcode field itself
    # e.g. "000759-F" → suffix="F", "000759" → suffix=None
    interface_map = {}
    for record in all_interface_records:
        rec_barcode  = record.get("Barcode", "")
        rec_testcode = record.get("TestCode", "")

        # Extract suffix from the record's Barcode field
        if "-" in rec_barcode[len(barcode):]:
            rec_suffix = rec_barcode[len(barcode) + 1:]  # everything after "barcode-"
        else:
            rec_suffix = None

        key = (rec_testcode, rec_suffix)  # e.g. ("01", "F") or ("01", None)

        # Keep the most recent record per (test_code, suffix)
        if key not in interface_map:
            interface_map[key] = record
        else:
            existing = interface_map[key]
            if (record.get("Receiveddate") or "") > (existing.get("Receiveddate") or ""):
                interface_map[key] = record

    print(f"DEBUG [{data_source_type}]: interface_map keys={list(interface_map.keys())}")
    # ─────────────────────────────────────────────────────────────────────────

    for test_item in test_list:
        test_id = test_item.get("test_id")

        if test_id_filter and str(test_id) != str(test_id_filter):
            continue

        test_detail_doc = core_testdetails_collection.find_one({"test_id": test_id})
        if not test_detail_doc:
            print(f"DEBUG [{data_source_type}]: No core_testdetails for test_id={test_id}, skipping.")
            continue

        test_name   = test_detail_doc.get("test_name")
        core_suffix = test_detail_doc.get("suffix")  # e.g. "F", "P", "R", or None

        # ── Resolve lookup barcode from interface_map ─────────────────────
        # If core_testdetails has a suffix, the interface record barcode
        # will be "barcode-suffix", otherwise plain barcode
        if core_suffix:
            lookup_barcode = f"{barcode}-{core_suffix}"
        else:
            lookup_barcode = barcode

        print(f"DEBUG [{data_source_type}]: test_id={test_id}, test_name={test_name}, "
              f"core_suffix={core_suffix}, lookup_barcode={lookup_barcode}")
        # ─────────────────────────────────────────────────────────────────

        sample_status_info = sample_status_map.get(str(test_id), {"status": "Unknown", "source": "none"})
        sample_status      = sample_status_info["status"]
        data_source        = sample_status_info["source"]

        print(f"DEBUG [{data_source_type}]: sample_status={sample_status}, data_source={data_source}")

        if sample_status in ("Rejected", "Cancelled"):
            print(f"DEBUG [{data_source_type}]: Skipping test_id={test_id}, status={sample_status}")
            continue

        # ── Resolve test details from core_testdetails ────────────────────
        query = {"test_name": test_name, "test_id": test_id}
        test_details_list = list(core_testdetails_collection.find(query))

        if not test_details_list and test_id:
            test_details_list = list(core_testdetails_collection.find({"test_id": test_id}))

        if not test_details_list:
            candidates = list(core_testdetails_collection.find({"test_name": test_name}))
            if len(candidates) > 1 and test_id:
                picked = next((d for d in candidates if d.get("test_id") == test_id), None)
                test_details_list = [picked] if picked else candidates
            else:
                test_details_list = candidates

        test_found = False
        for test_detail in test_details_list:
            test_found = True

            raw_parameters = test_detail.get("parameters", {})
            parameters     = normalize_parameters(raw_parameters)

            # ── Tests WITHOUT parameters (single-value tests) ─────────────
            if not parameters:
                test_code = test_detail.get(
                    "test_code",
                    f"{(test_name or '').replace(' ', '').upper()}01"
                )

                # ── Look up from pre-fetched interface_map ────────────────
                # Key is (test_code, core_suffix) — suffix from core_testdetails
                interface_key = (test_code, core_suffix)
                test_value_doc = interface_map.get(interface_key)

                # Fallback: try without suffix if not found
                if not test_value_doc and core_suffix:
                    test_value_doc = interface_map.get((test_code, None))

                print(f"DEBUG [{data_source_type}]: Looking up interface_map key={interface_key}, "
                      f"found={test_value_doc is not None}")

                if test_value_doc:
                    test_value        = test_value_doc.get("Value", "")
                    processing_status = test_value_doc.get("processingstatus", "N/A")
                    device_id_used    = test_value_doc.get("DeviceID", "N/A")
                    processed_records.append({
                        "barcode":          test_value_doc.get("Barcode"),  # actual barcode used
                        "test_code":        test_code,
                        "device_id":        device_id_used,
                        "record_id":        str(test_value_doc.get("_id")),
                        "data_source_type": data_source_type,
                    })
                else:
                    test_value        = ""
                    processing_status = "N/A"
                    device_id_used    = "N/A"

                created_date  = to_iso_or_str(test_value_doc.get("CreatedDate")  if test_value_doc else None)
                received_date = to_iso_or_str(test_value_doc.get("Receiveddate") if test_value_doc else None)

                reference_range, low, high = resolve_gender_fields(test_detail, gender)

                test_info = {
                    "barcode":           barcode,
                    "lookup_barcode":    lookup_barcode,
                    "suffix":            core_suffix,
                    "device_id":         device_id_used,
                    "test_id":           test_id,
                    "testname":          test_name,
                    "test_code":         test_code,
                    "parameter_name":    None,
                    "unit":              test_detail.get("unit", "N/A"),
                    "reference_range":   reference_range,
                    "low":               low,
                    "high":              high,
                    "method":            test_detail.get("method", "N/A"),
                    "department":        test_detail.get("department", "N/A"),
                    "specimen_type":     test_detail.get("specimen_type", "N/A"),
                    "NABL":              test_detail.get("NABL", "N/A"),
                    "interpretation":    test_detail.get("interpretation", ""),
                    "critical_range":    test_detail.get("critical_range", ""),
                    "specimen_options":  test_detail.get("specimen_options", []),
                    "lod":               test_detail.get("lod", ""),
                    "test_value":        test_value,
                    "processing_status": processing_status,
                    "sample_status":     sample_status,
                    "data_source":       data_source,
                    "data_source_type":  data_source_type,
                    "lab_unique_id":     test_value_doc.get("lab_unique_id", "N/A") if test_value_doc else "N/A",
                    "created_date":      created_date,
                    "received_date":     received_date,
                    "sub_title":         None,
                    "value_option":      test_detail.get("value_option", []),
                    "comment_options":   test_detail.get("comment_options", []),
                    "specimen_options":  test_detail.get("specimen_options", []),
                    "interpretation":    test_detail.get("interpretation", ""),
                    "critical_range":    test_detail.get("critical_range", ""),
                    "lod":               test_detail.get("lod", ""),
                }
                final_test_data.append(test_info)
                continue

            # ── Tests WITH parameters ─────────────────────────────────────
            has_interface_data = False
            selected_device    = None

            all_test_codes_for_this_test = []
            for device_key in parameters:
                param_test_codes = [
                    p.get("test_code") for p in parameters[device_key]
                    if isinstance(p, dict) and p.get("test_code")
                ]
                all_test_codes_for_this_test.extend(param_test_codes)
            all_test_codes_for_this_test = list(set(all_test_codes_for_this_test))

            # ── Filter pre-fetched records for this test's codes + suffix ─
            all_barcode_records = [
                r for r in all_interface_records
                if r.get("TestCode") in all_test_codes_for_this_test
                and r.get("Barcode") == lookup_barcode   # exact suffix-aware barcode
            ]

            print(f"DEBUG [{data_source_type}]: Found {len(all_barcode_records)} records "
                  f"for test {test_name} using lookup_barcode={lookup_barcode}")

            device_id_from_interface = "N/A"
            if all_barcode_records:
                first_record_device = all_barcode_records[0].get("DeviceID")
                device_id_from_interface = str(first_record_device) if first_record_device else "N/A"

            interface_test_codes = []
            interface_device_ids = []
            if all_barcode_records:
                interface_test_codes = [r.get("TestCode") for r in all_barcode_records if r.get("TestCode")]
                interface_device_ids = list(set([
                    str(r.get("DeviceID")) for r in all_barcode_records if r.get("DeviceID")
                ]))

                best_match_device = None
                best_match_count  = 0

                for device_key in parameters:
                    param_test_codes = [
                        p.get("test_code") for p in parameters[device_key]
                        if isinstance(p, dict) and p.get("test_code")
                    ]
                    matches = len(set(param_test_codes) & set(interface_test_codes))
                    if matches > best_match_count:
                        best_match_count  = matches
                        best_match_device = device_key

                for interface_dev_id in interface_device_ids:
                    if interface_dev_id in parameters:
                        param_test_codes = [
                            p.get("test_code") for p in parameters[interface_dev_id]
                            if isinstance(p, dict) and p.get("test_code")
                        ]
                        matches = len(set(param_test_codes) & set(interface_test_codes))
                        if matches > best_match_count:
                            best_match_count  = matches
                            best_match_device = interface_dev_id

                if best_match_device and best_match_count > 0:
                    selected_device    = best_match_device
                    has_interface_data = True
                else:
                    selected_device = (
                        str(device_id) if device_id and str(device_id) in parameters
                        else (sorted(parameters.keys())[0] if parameters else None)
                    )
            else:
                selected_device = (
                    str(device_id) if device_id and str(device_id) in parameters
                    else (sorted(parameters.keys())[0] if parameters else None)
                )

            if selected_device and selected_device in parameters:
                param_list = parameters[selected_device] or []

                for param in param_list:
                    if not isinstance(param, dict):
                        continue

                    test_code = param.get("test_code")
                    if not test_code:
                        continue

                    test_value        = ""
                    processing_status = "No Data"
                    lab_unique_id     = "N/A"
                    created_date      = None
                    received_date     = None

                    if has_interface_data:
                        matching_record = next(
                            (r for r in all_barcode_records
                             if r.get("TestCode") == test_code
                             and r.get("processingstatus") == "pending"),
                            None,
                        )
                        if matching_record:
                            processed_records.append({
                                "barcode":          matching_record.get("Barcode"),
                                "test_code":        test_code,
                                "device_id":        matching_record.get("DeviceID"),
                                "record_id":        str(matching_record.get("_id")),
                                "data_source_type": data_source_type,
                            })
                            test_value        = matching_record.get("Value", "")
                            processing_status = matching_record.get("processingstatus", "pending")
                            lab_unique_id     = matching_record.get("lab_unique_id", "N/A")
                            created_date      = matching_record.get("CreatedDate")
                            received_date     = matching_record.get("Receiveddate")

                    created_date  = to_iso_or_str(created_date)
                    received_date = to_iso_or_str(received_date)

                    reference_range, low, high = resolve_gender_fields(param, gender)

                    test_info = {
                        "barcode":           barcode,
                        "lookup_barcode":    lookup_barcode,
                        "suffix":            core_suffix,
                        "device_id":         device_id_from_interface,
                        "test_id":           test_id,
                        "testname":          test_name,
                        "test_code":         test_code,
                        "parameter_name":    param.get("test_name"),
                        "unit":              param.get("unit"),
                        "reference_range":   reference_range,
                        "low":               low,
                        "high":              high,
                        "method":            param.get("method"),
                        "department":        test_detail.get("department"),
                        "specimen_type":     test_detail.get("specimen_type", param.get("specimen_type")),
                        "NABL":              test_detail.get("NABL", "N/A"),
                        "test_value":        test_value,
                        "processing_status": processing_status,
                        "sample_status":     sample_status,
                        "data_source":       data_source,
                        "data_source_type":  data_source_type,
                        "lab_unique_id":     lab_unique_id,
                        "created_date":      created_date,
                        "received_date":     received_date,
                        "sub_title":         param.get("sub_title"),
                        "value_option":      param.get("value_option"),
                        "comment_options":   test_detail.get("comment_options", []),
                        "specimen_options":  test_detail.get("specimen_options", []),
                        "interpretation":    test_detail.get("interpretation", ""),
                        "critical_range":    test_detail.get("critical_range", ""),
                        "lod":               test_detail.get("lod", ""),
                    }
                    final_test_data.append(test_info)

            break  # process only first matching test_detail

        if not test_found:
            final_test_data.append({
                "barcode":           barcode,
                "lookup_barcode":    lookup_barcode,
                "suffix":            core_suffix,
                "device_id":         "N/A",
                "test_id":           test_id,
                "testname":          test_name if 'test_name' in dir() else "N/A",
                "test_code":         "N/A",
                "parameter_name":    None,
                "unit":              "",
                "reference_range":   "",
                "low":               "",
                "high":              "",
                "method":            "",
                "department":        "",
                "specimen_type":     "",
                "NABL":              "N/A",
                "test_value":        "",
                "processing_status": "No Test Details",
                "sample_status":     sample_status,
                "data_source":       data_source,
                "data_source_type":  data_source_type,
                "lab_unique_id":     "N/A",
                "created_date":      None,
                "received_date":     None,
                "sub_title":         None,
                "value_option":      None,
                "comment_options":   [],
            })

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



            
@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def save_test_value(request):
    if request.method == 'POST':
        payload = request.data
        employee_id = payload.get('auth-user-id')
        try:
            test_details_json = payload.get("testdetails", [])
            barcode           = payload.get("barcode")
            locationId        = payload.get("locationId")
            processed_records = payload.get("processed_records", [])

            if not isinstance(test_details_json, list) or not test_details_json:
                return Response(
                    {"error": "Invalid test details format"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            for test in test_details_json:
                if not test.get('test_id'):
                    return Response(
                        {"error": "Missing test_id in test details"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # ── Duplicate check ───────────────────────────────────────────────
            # For each test_id in the incoming payload, check if a TestValue
            # record already exists for this barcode with that test_id.
            #
            # Save is BLOCKED if the existing record has:
            #   - approve = False  (pending, not yet rerun)
            #   - approve = True   (already approved)
            #
            # Save is ALLOWED only when:
            #   - No existing record at all, OR
            #   - Existing record has approve=False AND rerun=True  (rerun in progress)
            # ─────────────────────────────────────────────────────────────────

            blocked_tests = []

            # Fetch all existing TestValue records for this barcode at once
            existing_records = TestValue.objects.filter(barcode=barcode)

            for incoming_test in test_details_json:
                incoming_test_id = incoming_test.get('test_id')

                for existing_record in existing_records:
                    # testdetails is stored as a JSON string or list
                    try:
                        existing_testdetails = (
                            json.loads(existing_record.testdetails)
                            if isinstance(existing_record.testdetails, str)
                            else existing_record.testdetails
                        )
                    except (json.JSONDecodeError, TypeError):
                        existing_testdetails = []

                    if not isinstance(existing_testdetails, list):
                        continue

                    for existing_test in existing_testdetails:
                        if str(existing_test.get('test_id')) != str(incoming_test_id):
                            continue

                        approve = existing_test.get('approve', False)
                        rerun   = existing_test.get('rerun',   False)
                        # Block: approve=True (already approved — cannot overwrite)
                        if approve is True:
                            blocked_tests.append({
                                'test_id': incoming_test_id,
                                'reason': 'already approved'
                            })
                            break

                        # Block: approve=False AND rerun=False
                        # (data exists but not flagged for rerun — duplicate entry)
                        if approve is False and rerun is False:
                            blocked_tests.append({
                                'test_id': incoming_test_id,
                                'reason': 'already exists and not flagged for rerun'
                            })
                            break

                        # Block: approve=None AND rerun=False
                        # (saved but pending manual approval — not flagged for rerun)
                        if approve is None and rerun is False:
                            blocked_tests.append({
                                'test_id': incoming_test_id,
                                'reason': 'already exists and not flagged for rerun'
                            })
                            break

                        # Allow: approve=False AND rerun=True → fall through (rerun scenario)

            if blocked_tests:
                reasons = "; ".join(
                    f"test_id {b['test_id']}: {b['reason']}" for b in blocked_tests
                )
                return Response(
                    {
                        "error": f"Save blocked for the following tests — {reasons}",
                        "blocked_tests": blocked_tests,
                    },
                    status=status.HTTP_409_CONFLICT
                )

            # ── All checks passed — inject approve_by and save ────────────────
            AUTO_APPROVE_BY = "60463"

            for test in test_details_json:
                if test.get('approve') is True:
                    test['approve_by'] = AUTO_APPROVE_BY

            test_value_record = TestValue.objects.create(
                created_by=employee_id,
                date=payload.get('date'),
                barcode=barcode,
                locationId=locationId,
                testdetails=test_details_json,
)
            # ── Update processing status for interface records ─────────────────
            update_success_count = 0
            update_errors        = []

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
                response_message += (
                    f" Updated processing status for "
                    f"{update_success_count}/{len(processed_records)} records."
                )
                if update_errors:
                    response_message += f" Errors: {'; '.join(update_errors[:3])}"

            return Response(
                {
                    "message": response_message,
                    "updated_records": update_success_count,
                    "total_records":   len(processed_records),
                    "errors":          update_errors if update_errors else None,
                },
                status=status.HTTP_201_CREATED
            )

        except Patient.DoesNotExist:
            print("DEBUG: Patient not found")
            return Response(
                {"error": "Patient not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"DEBUG: POST error: {str(e)}")
            return Response(
                {"error": f"An error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def worklist_view(request):
    """
    GET /worklist/?uhid=<patient_id>

    Returns all barcodes for a given UHID with patient details and test names.

    Response:
    {
      "patient": {
        "uhid": "...",
        "name": "...",
        "age": "...",
        "age_type": "...",
        "gender": "..."
      },
      "barcodes": [
        {
          "barcode": "...",
          "date": "YYYY-MM-DD",
          "billnumber": "...",
          "ipnumber": "...",
          "opiptype": "...",
          "test_names": "Test A, Test B",
          "no_of_tests": 2
        },
        ...
      ]
    }
    """
    uhid = request.GET.get("uhid", "").strip()

    if not uhid:
        return JsonResponse({"error": "uhid query parameter is required"}, status=400)

    try:
        # 1. Fetch all Hmsbarcode records for this UHID
        barcode_records = list(
            Hmsbarcode.objects.filter(patient_id=uhid)
            .values(
                "billnumber", "barcode", "date", "testdetails",
                "patient_id", "patientname", "age", "age_type", "gender",
                "IPOPType", "ipnumber",
            )
            .order_by("-date")
        )

        if not barcode_records:
            return JsonResponse(
                {"error": f"No records found for UHID: {uhid}"},
                status=404,
            )

        # 2. Patient info from first record
        first = barcode_records[0]
        patient_info = {
            "uhid":     first.get("patient_id", "N/A"),
            "name":     first.get("patientname", "N/A"),
            "age":      first.get("age", "N/A"),
            "age_type": first.get("age_type", ""),
            "gender":   first.get("gender", "N/A"),
        }

        # 3. Collect all test_ids across all barcodes
        all_test_ids = set()
        parsed_test_fields = {}

        for record in barcode_records:
            barcode = record.get("barcode")
            test_field = record.get("testdetails", [])
            if isinstance(test_field, str):
                try:
                    test_field = json.loads(test_field.strip('"'))
                except json.JSONDecodeError:
                    test_field = []
            if not isinstance(test_field, list):
                test_field = []

            parsed_test_fields[barcode] = test_field
            for t in test_field:
                if isinstance(t, dict) and t.get("test_id"):
                    all_test_ids.add(t["test_id"])

        # 4. Bulk-fetch test names from MongoDB
        mongo_client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
        core_collection = mongo_client.Diagnostics.core_testdetails

        test_info_map = {}  # test_id -> test_name
        if all_test_ids:
            cursor = core_collection.find(
                {"test_id": {"$in": list(all_test_ids)}},
                {"_id": 0, "test_id": 1, "test_name": 1},
            )
            for doc in cursor:
                test_info_map[doc["test_id"]] = doc.get("test_name", "N/A")

        mongo_client.close()

        # 5. Build barcodes list
        result_barcodes = []

        for record in barcode_records:
            barcode = record.get("barcode", "N/A")
            test_field = parsed_test_fields.get(barcode, [])

            test_names = ", ".join(
                test_info_map.get(t.get("test_id"), t.get("testname", "N/A"))
                for t in test_field
                if isinstance(t, dict) and t.get("test_id")
            )

            result_barcodes.append({
                "barcode":     barcode,
                "date":        record["date"].strftime("%Y-%m-%d") if record.get("date") else "N/A",
                "billnumber":  record.get("billnumber", "N/A"),
                "ipnumber":    record.get("ipnumber", "N/A"),
                "opiptype":    record.get("IPOPType", "N/A"),
                "test_names":  test_names,
                "no_of_tests": len(test_field),
            })

        return JsonResponse(
            {"patient": patient_info, "barcodes": result_barcodes},
            safe=False,
        )

    except Exception as exc:
        print(traceback.format_exc())
        return JsonResponse({"error": str(exc)}, status=500)
