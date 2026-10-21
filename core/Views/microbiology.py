from rest_framework.response import Response
from django.http import JsonResponse
from datetime import datetime
from rest_framework.decorators import api_view
from rest_framework import  status
from pymongo import MongoClient
from rest_framework import status
from datetime import datetime, timedelta
from django.conf import settings  
from django.utils.timezone import make_aware
from datetime import datetime
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ..models import Patient
from ..models import BarcodeTestDetails
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json
from django.utils.timezone import make_aware
from ..models import SampleStatus, MBTestValue, Hmssamplestatus, Hmsbarcode,Billing
from dotenv import load_dotenv
load_dotenv()
from urllib.parse import unquote_plus
import re
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from django.utils.dateparse import parse_datetime
import pytz
import gridfs
# Define IST timezone
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)


# Module-level connection and catalogue cache for Microbiology
_mb_mongo_client = None
_mb_test_details_cache = None
_mb_test_details_cache_time = 0

def get_mb_mongo_client():
    global _mb_mongo_client
    if _mb_mongo_client is None:
        _mb_mongo_client = MongoClient(
            os.getenv('GLOBAL_DB_HOST'),
            maxPoolSize=50,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000
        )
    return _mb_mongo_client

def get_cached_mb_test_details(db):
    global _mb_test_details_cache, _mb_test_details_cache_time
    import time as pytime
    now = pytime.time()
    if _mb_test_details_cache is None or (now - _mb_test_details_cache_time > 1800):
        cache = {}
        for td_doc in db.core_testdetails.find({}, {"_id": 0, "test_id": 1, "test_name": 1, "department": 1, "collection_container": 1, "TAT_Time": 1}):
            tid = td_doc.get('test_id')
            cache[tid] = td_doc
            cache[str(tid)] = td_doc
            if str(tid).isdigit():
                cache[int(tid)] = td_doc
        _mb_test_details_cache = cache
        _mb_test_details_cache_time = now
    return _mb_test_details_cache


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def micro_biology_testvalue(request):
    try:
        from datetime import date
        # Get query parameters
        from_date_str = request.query_params.get('from_date', None)
        to_date_str = request.query_params.get('to_date', None)
        date_str = request.query_params.get('date', None)
        source = request.query_params.get('source', 'all')
        pending_type = request.query_params.get('pending_type', None)  # 'technician', 'doctor', 'dispatch', or None
        scope = request.query_params.get('scope', 'date_range')        # 'date_range', 'all_days', or 'split'
        exclude_from_date_str = request.query_params.get('exclude_from_date', None)
        exclude_to_date_str = request.query_params.get('exclude_to_date', None)
        exclude_dispatched = request.query_params.get('exclude_dispatched', 'true' if pending_type else 'false').lower() == 'true'

        start_of_range = None
        end_of_range = None
        exclude_start = None
        exclude_end = None

        # Determine date range or exclusions
        if scope == 'all_days':
            if exclude_from_date_str and exclude_to_date_str:
                ex_from = datetime.strptime(exclude_from_date_str, '%Y-%m-%d').date()
                ex_to = datetime.strptime(exclude_to_date_str, '%Y-%m-%d').date()
                exclude_start = datetime.combine(ex_from, datetime.min.time())
                exclude_end = datetime.combine(ex_to, datetime.max.time())
        else:
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
                end_of_range = datetime.combine(selected_date, datetime.max.time())

            elif scope == 'split':
                today_d = date.today()
                start_of_range = datetime.combine(today_d, datetime.min.time())
                end_of_range = datetime.combine(today_d, datetime.max.time())

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

        def get_sample_field(obj, field, default=None):
            if obj is None:
                return default
            if isinstance(obj, dict):
                return obj.get(field, default)
            return getattr(obj, field, default)

        # Connect to MongoDB for test details (Cached)
        client = get_mb_mongo_client()
        db = client.Diagnostics
        test_details_cache = get_cached_mb_test_details(db)

        # Multi-tab pending counts
        all_tech_barcodes = set()
        all_doc_barcodes = set()
        all_dispatch_barcodes = set()

        def get_test_details(test_id):
            if test_id not in test_details_cache:
                td = db.core_testdetails.find_one(
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
                if td:
                    test_details_cache[test_id] = td
                    test_details_cache[str(test_id)] = td
            return test_details_cache.get(test_id) or test_details_cache.get(str(test_id))

        # Build mongo date filter for HMS & Regular
        sample_date_filter = {}
        if scope == 'all_days':
            if exclude_start and exclude_end:
                sample_date_filter['$or'] = [
                    {'date': {'$lt': exclude_start}},
                    {'date': {'$gt': exclude_end}}
                ]
        else:
            sample_date_filter['date'] = {'$gte': start_of_range, '$lte': end_of_range}

        sample_projection = {
            '_id': 1,
            'barcode': 1,
            'testdetails': 1,
            'date': 1,
            'created_date': 1,
            'created_by': 1,
            'lastmodified_by': 1,
            'lastmodified_date': 1,
            'patient_id': 1,
            'location_id': 1
        }

        all_barcodes = set()
        sample_data_by_barcode = {}

        # 1. Collect barcodes from HMS
        if source in ['hms', 'all']:
            if scope == 'split':
                hms_selected = list(db.core_hmssamplestatus.find({'date': {'$gte': start_of_range, '$lte': end_of_range}}, sample_projection))
                hms_backlog = list(db.core_hmssamplestatus.find({'$or': [{'date': {'$lt': start_of_range}}, {'date': {'$gt': end_of_range}}]}, sample_projection).sort('created_date', -1).limit(600))
                hms_samples_iter = hms_selected + hms_backlog
            else:
                hms_query = db.core_hmssamplestatus.find(sample_date_filter, sample_projection).sort('created_date', -1)
                if scope == 'all_days':
                    hms_query = hms_query.limit(600)
                hms_samples_iter = list(hms_query)

            for sample in hms_samples_iter:
                barcode = sample.get('barcode')
                if barcode:
                    all_barcodes.add(barcode)
                    if barcode not in sample_data_by_barcode:
                        sample_data_by_barcode[barcode] = {
                            'source': 'hms',
                            'sample': sample,
                            'created_date': sample.get('created_date')
                        }

        # 2. Collect barcodes from Regular
        if source in ['regular', 'all']:
            if scope == 'split':
                reg_selected = list(db.core_samplestatus.find({'date': {'$gte': start_of_range, '$lte': end_of_range}}, sample_projection))
                reg_backlog = list(db.core_samplestatus.find({'$or': [{'date': {'$lt': start_of_range}}, {'date': {'$gt': end_of_range}}]}, sample_projection).sort('created_date', -1).limit(600))
                reg_samples_iter = reg_selected + reg_backlog
            else:
                regular_query = db.core_samplestatus.find(sample_date_filter, sample_projection).sort('created_date', -1)
                if scope == 'all_days':
                    regular_query = regular_query.limit(600)
                reg_samples_iter = list(regular_query)

            for sample in reg_samples_iter:
                barcode = sample.get('barcode')
                if barcode:
                    all_barcodes.add(barcode)
                    s_created = sample.get('created_date')
                    if barcode not in sample_data_by_barcode or (s_created and s_created > sample_data_by_barcode[barcode]['created_date']):
                        sample_data_by_barcode[barcode] = {
                            'source': 'regular',
                            'sample': sample,
                            'created_date': s_created
                        }

        # 3. Bulk fetch MBTestValues
        test_values_by_barcode = {}
        if all_barcodes:
            tv_projection = {'barcode': 1, 'testdetails': 1, 'created_date': 1, 'lastmodified_date': 1, 'is_preliminary': 1}
            all_test_values = list(db.core_mbtestvalue.find(
                {'barcode': {'$in': list(all_barcodes)}},
                tv_projection
            ).sort('created_date', -1))

            for tv in all_test_values:
                b = tv.get('barcode')
                if b not in test_values_by_barcode:
                    test_values_by_barcode[b] = []
                test_values_by_barcode[b].append(tv)

            for b in test_values_by_barcode:
                test_values_by_barcode[b].sort(
                    key=lambda x: x.get('created_date') or datetime.min,
                    reverse=True
                )

        def parse_testdetails(testdetails_raw):
            try:
                return json.loads(testdetails_raw) if isinstance(testdetails_raw, str) else (testdetails_raw or [])
            except (json.JSONDecodeError, TypeError):
                return []

        def enrich_test_with_details(test, sample_status=None):
            test_id = test.get('test_id')
            if test_id:
                test_detail = get_test_details(test_id)
                if test_detail:
                    test['testname'] = test_detail.get('test_name', test.get('testname', 'N/A'))
                    test['department'] = test_detail.get('department', test.get('department', 'N/A'))
                    test['container'] = test_detail.get('collection_container', test.get('container', 'N/A'))
                    test['tat_time'] = test_detail.get('TAT_Time') or test.get('TAT_Time')

            if not test.get('samplecollected_time') and not test.get('sample_collected_time'):
                coll_time = (
                    test.get('collected_time') or
                    (safe_datetime_to_string(get_sample_field(sample_status, 'created_date')) if sample_status else None) or
                    (safe_datetime_to_string(get_sample_field(sample_status, 'date')) if sample_status else None)
                )
                if coll_time:
                    test['samplecollected_time'] = coll_time
            return test

        def match_test_values(test, barcode, test_values_by_barcode):
            test_id = test.get('test_id')
            test.update({
                'rerun': False,
                'approve': False,
                'dispatch': False,
                'is_dispatched': False,
                'test_value_exists': False,
                'approve_time': None,
                'rerun_time': None,
                'dispatch_time': None,
                'approve_by': None,
                'dispatch_by': None,
                'value': None,
                'remarks': None,
                'comment': None,
                'verified_by': None,
                'is_preliminary': False
            })

            if not test_id:
                return test

            test_values = test_values_by_barcode.get(barcode, [])
            for tv in test_values:
                tv_details = parse_testdetails(tv.get('testdetails') if isinstance(tv, dict) else tv.testdetails)
                for tv_test in tv_details:
                    tv_test_id = tv_test.get('test_id')
                    if str(tv_test_id) == str(test_id):
                        is_dispatched_val = bool(tv_test.get('dispatch', False) or tv_test.get('is_dispatched', False))
                        is_prelim = bool(tv.get('is_preliminary', False) or tv_test.get('is_preliminary', False) or tv_test.get('parameter_type') == 'Preliminary')
                        raw_appr = tv_test.get('approve_time')
                        raw_rr = tv_test.get('rerun_time')
                        raw_disp = tv_test.get('dispatch_time')
                        appr_time = None if raw_appr in ['null', 'undefined', 'None', '', None] else raw_appr
                        rr_time = None if raw_rr in ['null', 'undefined', 'None', '', None] else raw_rr
                        disp_time = None if raw_disp in ['null', 'undefined', 'None', '', None] else raw_disp

                        test.update({
                            'test_value_exists': True,
                            'approve': bool(tv_test.get('approve', False)),
                            'rerun': bool(tv_test.get('rerun', False)),
                            'dispatch': is_dispatched_val,
                            'is_dispatched': is_dispatched_val,
                            'is_preliminary': is_prelim,
                            'approve_time': appr_time,
                            'rerun_time': rr_time,
                            'dispatch_time': disp_time,
                            'approve_by': tv_test.get('approve_by'),
                            'dispatch_by': tv_test.get('dispatched_by') or tv_test.get('dispatch_by'),
                            'value': tv_test.get('value'),
                            'remarks': tv_test.get('remarks'),
                            'comment': tv_test.get('comment'),
                            'verified_by': tv_test.get('verified_by')
                        })
                        return test
            return test

        combined_results = []

        # Process HMS Samples
        qualifying_hms = []
        needed_hms_barcodes = []
        if source in ['hms', 'all']:
            for barcode, data in sample_data_by_barcode.items():
                if data['source'] != 'hms':
                    continue

                sample_status = data['sample']
                testdetails = parse_testdetails(get_sample_field(sample_status, 'testdetails'))
                filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Received']]
                if not filtered_tests:
                    continue

                updated_tests = []
                for test in filtered_tests:
                    enriched_test = enrich_test_with_details(test, sample_status)
                    if enriched_test.get('department') != 'Microbiology':
                        continue

                    matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                    if matched_test.get('dispatch') or matched_test.get('is_dispatched'):
                        continue

                    location_id = get_sample_field(sample_status, 'location_id') or "Shanmuga Reference Lab"

                    # Multi-tab count tracking
                    if not matched_test.get('test_value_exists') or matched_test.get('rerun') or (matched_test.get('is_preliminary') and matched_test.get('approve')):
                        all_tech_barcodes.add(barcode)
                    elif not matched_test.get('approve'):
                        all_doc_barcodes.add(barcode)
                    elif not matched_test.get('is_preliminary') and not matched_test.get('dispatch'):
                        loc_lower = str(location_id).lower()
                        if 'hms' in loc_lower or 'shanmuga' in loc_lower or 'reference' in loc_lower:
                            all_dispatch_barcodes.add(barcode)

                    # Tab filtering
                    if pending_type == 'technician':
                        if not (not matched_test.get('test_value_exists') or matched_test.get('rerun') or (matched_test.get('is_preliminary') and matched_test.get('approve'))):
                            continue
                    elif pending_type == 'doctor':
                        if not (matched_test.get('test_value_exists') and not matched_test.get('approve') and not matched_test.get('rerun')):
                            continue
                    elif pending_type == 'dispatch':
                        if not (matched_test.get('approve') and not matched_test.get('is_preliminary') and not matched_test.get('dispatch') and not matched_test.get('rerun')):
                            continue
                        loc_lower = str(location_id).lower()
                        if not ('hms' in loc_lower or 'shanmuga' in loc_lower or 'reference' in loc_lower):
                            continue

                    updated_tests.append(matched_test)

                if updated_tests:
                    qualifying_hms.append((sample_status, barcode, updated_tests))
                    needed_hms_barcodes.append(barcode)

        # Process Regular Samples
        qualifying_regular = []
        needed_regular_barcodes = []
        if source in ['regular', 'all']:
            for barcode, data in sample_data_by_barcode.items():
                if data['source'] != 'regular':
                    continue

                sample_status = data['sample']
                testdetails = parse_testdetails(get_sample_field(sample_status, 'testdetails'))
                filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Received']]
                if not filtered_tests:
                    continue

                updated_tests = []
                for test in filtered_tests:
                    enriched_test = enrich_test_with_details(test, sample_status)
                    if enriched_test.get('department') != 'Microbiology':
                        continue

                    matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                    if matched_test.get('dispatch') or matched_test.get('is_dispatched'):
                        continue

                    location_id = get_sample_field(sample_status, 'location_id') or "Shanmuga Reference Lab"

                    # Multi-tab count tracking
                    if not matched_test.get('test_value_exists') or matched_test.get('rerun') or (matched_test.get('is_preliminary') and matched_test.get('approve')):
                        all_tech_barcodes.add(barcode)
                    elif not matched_test.get('approve'):
                        all_doc_barcodes.add(barcode)
                    elif not matched_test.get('is_preliminary') and not matched_test.get('dispatch'):
                        loc_lower = str(location_id).lower()
                        if 'hms' in loc_lower or 'shanmuga' in loc_lower or 'reference' in loc_lower:
                            all_dispatch_barcodes.add(barcode)

                    # Tab filtering
                    if pending_type == 'technician':
                        if not (not matched_test.get('test_value_exists') or matched_test.get('rerun') or (matched_test.get('is_preliminary') and matched_test.get('approve'))):
                            continue
                    elif pending_type == 'doctor':
                        if not (matched_test.get('test_value_exists') and not matched_test.get('approve') and not matched_test.get('rerun')):
                            continue
                    elif pending_type == 'dispatch':
                        if not (matched_test.get('approve') and not matched_test.get('is_preliminary') and not matched_test.get('dispatch') and not matched_test.get('rerun')):
                            continue
                        loc_lower = str(location_id).lower()
                        if not ('hms' in loc_lower or 'shanmuga' in loc_lower or 'reference' in loc_lower):
                            continue

                    updated_tests.append(matched_test)

                if updated_tests:
                    qualifying_regular.append((sample_status, barcode, updated_tests))
                    needed_regular_barcodes.append(barcode)

        # Demographics bulk lookup
        hms_barcodes_dict = {}
        if needed_hms_barcodes:
            for hb in db.core_hmsbarcode.find({'barcode': {'$in': needed_hms_barcodes}}):
                hms_barcodes_dict[hb.get('barcode')] = hb

        regular_barcodes_dict = {}
        if needed_regular_barcodes:
            for rb in db.core_barcodetestdetails.find({'barcode': {'$in': needed_regular_barcodes}}):
                regular_barcodes_dict[rb.get('barcode')] = rb

        # Assemble HMS Results
        for sample_status, barcode, updated_tests in qualifying_hms:
            patient_name = "Unknown Patient"
            patient_id = "Unknown ID"
            age = "Unknown"
            gender = "Unknown"
            location_id = "Shanmuga Reference Lab"
            IPOPType = "Unknown"
            phone = "Unknown"
            ref_doctor = "Unknown"
            barcode_by = "Unknown"
            barcode_date = "Unknown"

            barcode_details = hms_barcodes_dict.get(barcode)
            if barcode_details:
                patient_name = get_sample_field(barcode_details, 'patientname') or "Unknown Patient"
                patient_id = get_sample_field(barcode_details, 'patient_id') or "Unknown ID"
                age = get_sample_field(barcode_details, 'age') or "Unknown"
                gender = get_sample_field(barcode_details, 'gender') or "Unknown"
                location_id = get_sample_field(barcode_details, 'location_id') or "Shanmuga Reference Lab"
                IPOPType = get_sample_field(barcode_details, 'IPOPType') or get_sample_field(barcode_details, 'opiptype') or ("IP" if get_sample_field(barcode_details, 'ipnumber') else "OP")
                phone = get_sample_field(barcode_details, 'phone') or "Unknown"
                ref_doctor = get_sample_field(barcode_details, 'ref_doctor') or "Unknown"
                barcode_by = get_sample_field(barcode_details, 'created_by') or "Unknown"
                barcode_date = get_sample_field(barcode_details, 'created_date') or "Unknown"
            elif get_sample_field(sample_status, 'patient_id'):
                patient_id = get_sample_field(sample_status, 'patient_id')

            if pending_type == 'dispatch':
                loc_lower = str(location_id or '').lower()
                if not ('hms' in loc_lower or 'shanmuga' in loc_lower or 'reference' in loc_lower):
                    continue

            combined_results.append({
                'id': str(get_sample_field(sample_status, 'id') or get_sample_field(sample_status, '_id')),
                'created_by': get_sample_field(sample_status, 'created_by'),
                'created_date': safe_datetime_to_string(get_sample_field(sample_status, 'created_date')),
                'barcode_by': barcode_by,
                'barcode_date': safe_datetime_to_string(barcode_date),
                'lastmodified_by': get_sample_field(sample_status, 'lastmodified_by'),
                'lastmodified_date': safe_datetime_to_string(get_sample_field(sample_status, 'lastmodified_date')),
                'patient_id': patient_id,
                'patientname': patient_name,
                'age': age,
                'gender': gender,
                'phone': phone,
                'ref_doctor': ref_doctor,
                'location_id': location_id,
                'opiptype': IPOPType,
                'barcode': barcode,
                'date': safe_datetime_to_string(get_sample_field(sample_status, 'date')),
                'testdetails': updated_tests,
                'data_source': 'hms_django_model'
            })

        # Assemble Regular Results
        for sample_status, barcode, updated_tests in qualifying_regular:
            patient_name = "Unknown Patient"
            patient_id = "Unknown ID"
            age = "Unknown"
            gender = "Unknown"
            location_id = "Shanmuga Reference Lab"
            is_emergency = False
            patient_history = ""
            phone = "Unknown"
            ref_doctor = "Unknown"
            barcode_by = "Unknown"
            barcode_date = "Unknown"

            barcode_details = regular_barcodes_dict.get(barcode)
            if barcode_details:
                patient_name = get_sample_field(barcode_details, 'patientname') or "Unknown Patient"
                patient_id = get_sample_field(barcode_details, 'patient_id') or "Unknown ID"
                age = get_sample_field(barcode_details, 'age') or "Unknown"
                gender = get_sample_field(barcode_details, 'gender') or "Unknown"
                is_emergency = bool(get_sample_field(barcode_details, 'is_emergency', False))
                patient_history = get_sample_field(barcode_details, 'patient_history') or ""
                phone = get_sample_field(barcode_details, 'phone') or "Unknown"
                ref_doctor = get_sample_field(barcode_details, 'ref_doctor') or "Unknown"
                barcode_by = get_sample_field(barcode_details, 'created_by') or "Unknown"
                barcode_date = get_sample_field(barcode_details, 'created_date') or "Unknown"
            elif get_sample_field(sample_status, 'patient_id'):
                patient_id = get_sample_field(sample_status, 'patient_id')

            if pending_type == 'dispatch':
                loc_lower = str(location_id or '').lower()
                if not ('hms' in loc_lower or 'shanmuga' in loc_lower or 'reference' in loc_lower):
                    continue

            combined_results.append({
                'id': str(get_sample_field(sample_status, 'id') or get_sample_field(sample_status, '_id')),
                'created_by': get_sample_field(sample_status, 'created_by'),
                'created_date': safe_datetime_to_string(get_sample_field(sample_status, 'created_date')),
                'barcode_by': barcode_by,
                'barcode_date': safe_datetime_to_string(barcode_date),
                'lastmodified_by': get_sample_field(sample_status, 'lastmodified_by'),
                'lastmodified_date': safe_datetime_to_string(get_sample_field(sample_status, 'lastmodified_date')),
                'patient_id': patient_id,
                'patientname': patient_name,
                'age': age,
                'gender': gender,
                'phone': phone,
                'ref_doctor': ref_doctor,
                'is_emergency': is_emergency,
                'patient_history': patient_history,
                'barcode': barcode,
                'location_id': location_id,
                'date': safe_datetime_to_string(get_sample_field(sample_status, 'date')),
                'testdetails': updated_tests,
                'data_source': 'regular_django_model'
            })

        # Process CHC Samples (if requested)
        if source in ['chc', 'all']:
            try:
                corp = client.Corporatehealthcheckup
                sample_collection = corp.core_sample
                billing_collection = corp.core_billing
                patient_collection = corp.core_chcregistration

                if scope == 'split':
                    chc_s = list(sample_collection.find({'created_date': {'$gte': start_of_range, '$lte': end_of_range}}))
                    chc_b = list(sample_collection.find({'$or': [{'created_date': {'$lt': start_of_range}}, {'created_date': {'$gt': end_of_range}}]}).sort('created_date', -1).limit(200))
                    chc_samples = chc_s + chc_b
                elif scope == 'all_days':
                    chc_query = {}
                    if exclude_start and exclude_end:
                        chc_query['$or'] = [
                            {'created_date': {'$lt': exclude_start}},
                            {'created_date': {'$gt': exclude_end}}
                        ]
                    chc_samples = list(sample_collection.find(chc_query).sort("created_date", -1).limit(300))
                else:
                    chc_samples = list(sample_collection.find({'created_date': {"$gte": start_of_range, "$lt": end_of_range}}).sort("created_date", -1))

                chc_barcodes = [s.get('barcode') for s in chc_samples if s.get('barcode')]
                chc_test_values_by_barcode = {}
                if chc_barcodes:
                    for tv in db.core_mbtestvalue.find({'barcode': {'$in': chc_barcodes}}, {'barcode': 1, 'testdetails': 1, 'created_date': 1, 'is_preliminary': 1}).sort('created_date', -1):
                        b = tv.get('barcode')
                        if b not in chc_test_values_by_barcode:
                            chc_test_values_by_barcode[b] = []
                        chc_test_values_by_barcode[b].append(tv)

                billing_map = {b['barcode']: b for b in billing_collection.find({"barcode": {"$in": chc_barcodes}})}
                employee_ids = [b.get('employee_id') for b in billing_map.values() if b.get('employee_id')]
                patient_map = {p['employee_id']: p for p in patient_collection.find({"employee_id": {"$in": employee_ids}})}

                chc_processed = set()
                for record in chc_samples:
                    barcode = record.get('barcode', '')
                    if not barcode or barcode in chc_processed:
                        continue

                    testdetails = parse_testdetails(record.get('testdetails'))
                    filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Received']]
                    if not filtered_tests:
                        continue

                    updated_tests = []
                    for test in filtered_tests:
                        enriched_test = enrich_test_with_details(test, record)
                        if enriched_test.get('department') != 'Microbiology':
                            continue

                        matched_test = match_test_values(enriched_test, barcode, chc_test_values_by_barcode)
                        if matched_test.get('dispatch') or matched_test.get('is_dispatched'):
                            continue

                        if not matched_test.get('test_value_exists') or matched_test.get('rerun') or (matched_test.get('is_preliminary') and matched_test.get('approve')):
                            all_tech_barcodes.add(barcode)
                        elif not matched_test.get('approve'):
                            all_doc_barcodes.add(barcode)

                        if pending_type == 'technician':
                            if not (not matched_test.get('test_value_exists') or matched_test.get('rerun') or (matched_test.get('is_preliminary') and matched_test.get('approve'))):
                                continue
                        elif pending_type == 'doctor':
                            if not (matched_test.get('test_value_exists') and not matched_test.get('approve') and not matched_test.get('rerun')):
                                continue
                        elif pending_type == 'dispatch':
                            continue

                        updated_tests.append(matched_test)

                    if not updated_tests:
                        continue

                    billing = billing_map.get(barcode, {})
                    employee_id = billing.get("employee_id")
                    patient = patient_map.get(employee_id, {})

                    combined_results.append({
                        'id': str(record.get('_id')),
                        'created_by': record.get('created_by', ''),
                        'created_date': safe_datetime_to_string(record.get('created_date')),
                        'lastmodified_by': record.get('lastmodified_by', ''),
                        'lastmodified_date': safe_datetime_to_string(record.get('lastmodified_date')),
                        'barcode': barcode,
                        'location_id': record.get('company_id', ''),
                        'is_preliminary': record.get('is_preliminary', False),
                        'patient_id': employee_id or 'Unknown ID',
                        'patientname': patient.get("employee_name", "Unknown Patient"),
                        'date': safe_datetime_to_string(record.get('date')),
                        'age': patient.get("age", "Unknown"),
                        'gender': patient.get("gender", "Unknown"),
                        'testdetails': updated_tests,
                        'data_source': 'chc_mongodb'
                    })
                    chc_processed.add(barcode)
            except Exception as e:
                print(f"CHC MongoDB error in MB: {str(e)}")

        # Process Franchise Samples (if requested)
        if source in ['regular', 'all']:
            try:
                franchise_db = client.franchise
                sample_collection = franchise_db.franchise_sample
                billing_collection = franchise_db.franchise_billing
                patient_collection = franchise_db.franchise_patient

                if scope == 'split':
                    fran_s = list(sample_collection.find({'created_date': {'$gte': start_of_range, '$lte': end_of_range}}))
                    fran_b = list(sample_collection.find({'$or': [{'created_date': {'$lt': start_of_range}}, {'created_date': {'$gt': end_of_range}}]}).sort('created_date', -1).limit(50))
                    mongodb_samples = fran_s + fran_b
                elif scope == 'all_days':
                    mongo_query = {}
                    if exclude_start and exclude_end:
                        mongo_query['$or'] = [
                            {'created_date': {'$lt': exclude_start}},
                            {'created_date': {'$gt': exclude_end}}
                        ]
                    mongodb_samples = list(sample_collection.find(mongo_query).sort("created_date", -1).limit(500))
                else:
                    mongodb_samples = list(sample_collection.find({'created_date': {"$gte": start_of_range, "$lt": end_of_range}}).sort("created_date", -1))

                mongo_barcodes = [s.get('barcode') for s in mongodb_samples if s.get('barcode')]
                mongo_test_values_by_barcode = {}
                if mongo_barcodes:
                    for tv in db.core_mbtestvalue.find({'barcode': {'$in': mongo_barcodes}}, {'barcode': 1, 'testdetails': 1, 'created_date': 1, 'is_preliminary': 1}).sort('created_date', -1):
                        b = tv.get('barcode')
                        if b not in mongo_test_values_by_barcode:
                            mongo_test_values_by_barcode[b] = []
                        mongo_test_values_by_barcode[b].append(tv)

                billing_map = {b['barcode']: b for b in billing_collection.find({"barcode": {"$in": mongo_barcodes}})}
                patient_ids = [b.get('patient_id') for b in billing_map.values() if b.get('patient_id')]
                patient_map = {p['patient_id']: p for p in patient_collection.find({"patient_id": {"$in": patient_ids}})}

                mongo_processed = set()
                for record in mongodb_samples:
                    barcode = record.get('barcode', '')
                    if not barcode or barcode in all_barcodes or barcode in mongo_processed:
                        continue

                    testdetails = parse_testdetails(record.get('testdetails'))
                    filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Received']]
                    if not filtered_tests:
                        continue

                    updated_tests = []
                    for test in filtered_tests:
                        enriched_test = enrich_test_with_details(test)
                        if enriched_test.get('department') != 'Microbiology':
                            continue

                        matched_test = match_test_values(enriched_test, barcode, mongo_test_values_by_barcode)
                        if matched_test.get('dispatch') or matched_test.get('is_dispatched'):
                            continue

                        if not matched_test.get('test_value_exists') or matched_test.get('rerun') or (matched_test.get('is_preliminary') and matched_test.get('approve')):
                            all_tech_barcodes.add(barcode)
                        elif not matched_test.get('approve'):
                            all_doc_barcodes.add(barcode)

                        if pending_type == 'technician':
                            if not (not matched_test.get('test_value_exists') or matched_test.get('rerun') or (matched_test.get('is_preliminary') and matched_test.get('approve'))):
                                continue
                        elif pending_type == 'doctor':
                            if not (matched_test.get('test_value_exists') and not matched_test.get('approve') and not matched_test.get('rerun')):
                                continue
                        elif pending_type == 'dispatch':
                            continue

                        updated_tests.append(matched_test)

                    if not updated_tests:
                        continue

                    patient_id = billing_map.get(barcode, {}).get('patient_id')
                    patient = patient_map.get(patient_id, {})

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
                        'barcode': barcode,
                        'location_id': record.get('franchise_id', ''),
                        'is_preliminary': record.get('is_preliminary', False),
                        'date': safe_datetime_to_string(record.get('created_date')),
                        'testdetails': updated_tests,
                        'data_source': 'mongodb'
                    })
                    mongo_processed.add(barcode)
            except Exception as e:
                print(f"Franchise MongoDB error in MB: {str(e)}")

        # Sort results
        combined_results.sort(key=lambda x: x.get('date') or x.get('created_date') or '', reverse=True)

        if scope == 'split':
            selected_date_results = []
            all_days_results = []

            for r in combined_results:
                r_date_raw = r.get('date') or r.get('created_date')
                is_selected = False
                if r_date_raw:
                    try:
                        if isinstance(r_date_raw, str):
                            clean_str = r_date_raw.replace("T", " ")[:19]
                            r_dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
                        elif isinstance(r_date_raw, datetime):
                            r_dt = r_date_raw.replace(tzinfo=None) if r_date_raw.tzinfo else r_date_raw
                        else:
                            r_dt = None

                        if r_dt and start_of_range <= r_dt <= end_of_range:
                            is_selected = True
                    except Exception:
                        is_selected = False

                if is_selected:
                    selected_date_results.append(r)
                else:
                    all_days_results.append(r)

            return Response({
                'selected_date': selected_date_results,
                'all_days': all_days_results,
                'counts': {
                    'technician': len(all_tech_barcodes),
                    'doctor': len(all_doc_barcodes),
                    'dispatch': len(all_dispatch_barcodes)
                }
            }, status=status.HTTP_200_OK)

        return Response(combined_results, status=status.HTTP_200_OK)
        
    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def mb_compare_test_details(request):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    core_testdetails_collection = db.core_testdetails

    barcode = request.GET.get('barcode')
    test_name_filter = request.GET.get('test_name')
    test_id = request.GET.get('test_id')
    parameter_type = request.GET.get('parameter_type')  # NEW: GNB, GPC, or Normal

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
            return {"N/A": parameters}
        return {}

    # Build query for core_testdetails
    query = {}
    if test_name_filter:
        query["test_name"] = test_name_filter
    if test_id:
        try:
            query["test_id"] = int(test_id)
        except (ValueError, TypeError):
            client.close()
            return JsonResponse({
                'error': 'Invalid test_id parameter - must be a number'
            }, status=400)

    # Get test details from core_testdetails
    if test_name_filter or test_id:
        test_details_list = list(core_testdetails_collection.find(query))
    else:
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
        
        # NEW: Handle "Normal" parameter type - return minimal data
        if parameter_type in ['Normal', 'Preliminary']:
            test_code = test_detail.get("test_code", f"{(test_name or '').replace(' ', '').upper()}01")
            final_test_data.append({
                "barcode": barcode,
                "test_id": test_id,
                "parameter_type": parameter_type,
                "test_code": test_code,
                "testname": test_name,
                "department": test_detail.get("department", "N/A"),
                "specimen_type": test_detail.get("specimen_type", "N/A"),
                "NABL": test_detail.get("NABL", False),
            })
            continue  # Skip parameter processing for Normal type
        
        raw_parameters = test_detail.get("parameters", {})
        parameters = normalize_parameters(raw_parameters)

        # Filter by parameter_type (GNB or GPC) if specified
        if parameter_type and isinstance(raw_parameters, dict):
            # Check if the parameter_type exists in the raw parameters
            if parameter_type in raw_parameters:
                # Only use the specified parameter type
                parameters = {parameter_type: raw_parameters[parameter_type]}
            else:
                # Parameter type not found, skip this test
                continue

        # Handle tests without parameters
        if not parameters:
            test_code = test_detail.get("test_code", f"{(test_name or '').replace(' ', '').upper()}01")
            final_test_data.append({
                "barcode": barcode,
                "test_id": test_id,
                "parameter_type": parameter_type,
                "testname": test_name,
                "test_code": test_code,
                "department": test_detail.get("department", "N/A"),
                "specimen_type": test_detail.get("specimen_type", "N/A"),
                "NABL": test_detail.get("NABL", "N/A"),
                "sub_title": None,
                "value_option": test_detail.get("value_option", []),
            })
            continue

        # Handle parameterized tests (GNB/GPC)
        # If parameter_type is specified, use it as the parameter_type
        # Otherwise, use the first available device
        if parameter_type and parameter_type in parameters:
            selected_device = parameter_type
        else:
            selected_device = sorted(parameters.keys())[0] if parameters else "N/A"

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
                    "test_id": test_id,
                    "parameter_type": parameter_type,
                    "testname": test_name,
                    "test_code": test_code,
                    "parameter_name": param.get("test_name"),
                    "department": test_detail.get("department"),
                    "specimen_type": test_detail.get("specimen_type", param.get("specimen_type")),
                    "NABL": test_detail.get("NABL", "N/A"),
                    "sub_title": param.get("sub_title"),
                    "value_option": param.get("value_option") or test_detail.get("value_option") or [],
                })

    client.close()

    if not final_test_data:
        return JsonResponse({
            'error': f'No test structure found for parameter type: {parameter_type}' if parameter_type else 'No test structure found'
        }, status=404)

    response_data = {
        'success': True,
        'barcode': barcode,
        'parameter_type': parameter_type,  # Include in response
        'test_count': len(final_test_data),
        'data': final_test_data,
    }
    return Response(response_data, status=200)

@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def mb_save_test_value(request):
    print(f"DEBUG: Request received: Method={request.method}, User={request.user}, Data={request.data}")
    
    payload = request.data
    employee_id = payload.get('auth-user-id')
    print(f"DEBUG: POST payload: {payload}")
    
    try:
        test_details_json = payload.get("testdetails", [])
        barcode = payload.get("barcode")
        locationId = payload.get("locationId")
        
        if not isinstance(test_details_json, list) or not test_details_json:
            return Response({"error": "Invalid test details format"}, status=status.HTTP_400_BAD_REQUEST)
        
        for test in test_details_json:
            test_id = test.get('test_id')
            if not test_id:
                return Response({"error": "Missing test_id in test details"}, status=status.HTTP_400_BAD_REQUEST)
            
        
        is_preliminary = request.data.get('is_preliminary', False)
        
        test_value_record = MBTestValue.objects.create(
            created_by=employee_id,
            date=payload.get('date'),
            barcode=barcode,
            locationId=locationId,
            is_preliminary=is_preliminary,
            testdetails=test_details_json,
        )
        
        return Response({
            "message": "Test details saved successfully."
        }, status=status.HTTP_201_CREATED)
        
    except Patient.DoesNotExist:
        print("DEBUG: Patient not found")
        return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        print(f"DEBUG: POST error: {str(e)}")
        return Response({"error": f"An error occurred: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# 🟢 3️⃣ Process testname filter once
def normalize_testname(name):
    if not name:
        return ""
    normalized = re.sub(r'\s+', ' ', name.strip().lower())
    normalized = normalized.replace('&', 'and').replace('/', ' ')
    return normalized


@api_view([ 'GET'])
@permission_classes([HasRoleAndDataPermission])
def mb_get_test_values(request):
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    db = client.franchise
    billing_col = db.franchise_billing
    patient_col = db.franchise_patient

    corp = client.Corporatehealthcheckup
    billing_collection = corp.core_billing
    patient_collection = corp.core_chcregistration
    
    # BarcodeTestDetails collection
    diagnostics = client.Diagnostics
    barcode_test_col = diagnostics.core_barcodetestdetails
    # NEW: core_testdetails collection for test metadata
    test_details_col = diagnostics.core_testdetails

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    patient_id_filter = request.GET.get('patient_id')
    testname_filter = request.GET.get('testname')
    emergency_filter = request.GET.get('emergency')

    scope = request.GET.get('scope')

    def get_val(obj, field, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(field, default)
        return getattr(obj, field, default)

    selected_patient_data = []
    backlog_patient_data = []

    if scope == 'split':
        # Delegate to micro_biology_testvalue to obtain active doctor-pending candidate barcodes.
        # This guarantees MBPatientList.js displays the exact same active records as MBPatientDetails.js (Doctor's Pending tab).
        from .microbiology import micro_biology_testvalue

        sub_request = request._request if hasattr(request, '_request') else request
        orig_get = sub_request.GET
        sub_get = orig_get.copy()
        sub_get['pending_type'] = 'doctor'
        sub_get['scope'] = 'split'
        sub_get['exclude_dispatched'] = 'true'
        sub_request.GET = sub_get

        try:
            view_func = micro_biology_testvalue.cls.as_view(permission_classes=[])
            res = view_func(sub_request)
            res_data = getattr(res, 'data', {})
        finally:
            sub_request.GET = orig_get

        sel_barcodes = [str(p['barcode']) for p in res_data.get('selected_date', []) if p.get('barcode')]
        bak_barcodes = [str(p['barcode']) for p in res_data.get('all_days', []) if p.get('barcode')]

        selected_docs = list(diagnostics.core_mbtestvalue.find({
            'barcode': {'$in': sel_barcodes}
        }).sort('created_date', -1)) if sel_barcodes else []
        for d in selected_docs:
            d['_scope_group'] = 'selected'

        backlog_docs = list(diagnostics.core_mbtestvalue.find({
            'barcode': {'$in': bak_barcodes}
        }).sort('created_date', -1)) if bak_barcodes else []
        for d in backlog_docs:
            d['_scope_group'] = 'backlog'

        patients_list = selected_docs + backlog_docs
        if not patients_list:
            return JsonResponse({
                'success': True,
                'selected_date': [],
                'all_days': [],
                'data': {'selected_date': [], 'all_days': []}
            })
    else:
        # Date filters using direct MongoDB query (avoids Djongo SQLDecodeError on ORDER BY)
        query = {}
        date_param = request.GET.get('date')
        from_date = request.GET.get('from_date') or date_param
        to_date = request.GET.get('to_date') or date_param
        if from_date and to_date:
            try:
                parsed_from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
                parsed_to_date = datetime.strptime(to_date, '%Y-%m-%d').date()
                start_dt = datetime.combine(parsed_from_date, datetime.min.time())
                end_dt = datetime.combine(parsed_to_date, datetime.max.time())
                query['date'] = {'$gte': start_dt, '$lte': end_dt}
            except ValueError:
                return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

        patients_list = list(diagnostics.core_mbtestvalue.find(query).sort('created_date', -1).limit(600 if not query else 0))

        if not patients_list:
            return JsonResponse([], safe=False)

    # Extract all barcodes upfront
    barcodes = [str(get_val(p, 'barcode')).zfill(0) for p in patients_list if get_val(p, 'barcode')]
    
    # ============================================
    # BULK FETCH ALL DATA SOURCES AT ONCE
    # ============================================
    
    # 1. Fetch all BarcodeTestDetails with is_emergency and patient_history
    barcode_details_docs = list(barcode_test_col.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "is_emergency": 1, "patient_history": 1, "_id": 0}
    ))
    barcode_details_dict = {
        doc['barcode']: {
            'patient_id': doc.get('patient_id'),
            'is_emergency': doc.get('is_emergency', False),
            'patient_history': doc.get('patient_history', '')
        }
        for doc in barcode_details_docs
    }
    
    # Get all patient IDs from barcode details
    barcode_patient_ids = [info['patient_id'] for info in barcode_details_dict.values() if info['patient_id']]
    
    # Fetch all Patient records
    patients_dict = {
        p.patient_id: {'name': p.patientname, 'age': p.age}
        for p in Patient.objects.filter(patient_id__in=barcode_patient_ids).only('patient_id', 'patientname', 'age')
    }
    
    # 2. NEW: Fetch patient details from core_hmsbarcode (PRIMARY SOURCE)
    hms_barcodes_docs = list(diagnostics.core_hmsbarcode.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "patientname": 1, "age": 1, "billnumber": 1, "gender": 1, "ref_doctor": 1, "phone": 1, "location_id": 1, "_id": 0}
    ))
    hms_barcodes_dict = {
        doc['barcode']: {
            'patient_id': doc.get('patient_id'),
            'name': doc.get('patientname'),
            'age': doc.get('age'),
            'billnumber': doc.get('billnumber'),
            'gender': doc.get('gender'),
            'ref_doctor': doc.get('ref_doctor'),
            'phone': doc.get('phone'),
            'location_id': doc.get('location_id')
        }
        for doc in hms_barcodes_docs
    }
    
    # 3. MongoDB franchise - bulk fetch (fallback)
    mongo_billing_docs = list(billing_col.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "_id": 0}
    ))
    mongo_billing_dict = {doc['barcode']: doc.get('patient_id') for doc in mongo_billing_docs}
    
    mongo_patient_ids = list(mongo_billing_dict.values())
    mongo_patient_docs = list(patient_col.find(
        {"patient_id": {"$in": mongo_patient_ids}},
        {"patient_id": 1, "patientname": 1, "age": 1, "_id": 0}
    ))
    mongo_patient_dict = {
        doc['patient_id']: {'name': doc.get('patientname', 'N/A'), 'age': doc.get('age', 'N/A')}
        for doc in mongo_patient_docs
    }
    
    # 4. Corporate - bulk fetch (fallback)
    corp_billing_docs = list(billing_collection.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "employee_id": 1, "_id": 0}
    ))
    corp_billing_dict = {doc['barcode']: doc.get('employee_id') for doc in corp_billing_docs}
    
    corp_employee_ids = list(corp_billing_dict.values())
    corp_employee_docs = list(patient_collection.find(
        {"employee_id": {"$in": corp_employee_ids}},
        {"employee_id": 1, "employee_name": 1, "age": 1, "_id": 0}
    ))
    corp_employee_dict = {
        doc['employee_id']: {'name': doc.get('employee_name', 'N/A'), 'age': doc.get('age', 'N/A')}
        for doc in corp_employee_docs
    }
    
    # NEW: Collect all unique test_ids from all patients
    all_test_ids = set()
    for patient in patients_list:
        try:
            raw_td = get_val(patient, 'testdetails')
            test_details = json.loads(raw_td) if isinstance(raw_td, str) else (raw_td or [])
            for test in test_details:
                if test.get('test_id'):
                    all_test_ids.add(test['test_id'])
        except (json.JSONDecodeError, TypeError):
            continue
    
    # NEW: Bulk fetch test details from MongoDB core_testdetails
    test_metadata_docs = list(test_details_col.find(
        {"test_id": {"$in": list(all_test_ids)}},
        {
            "test_id": 1, 
            "test_name": 1, 
            "specimen_type": 1, 
            "collection_container": 1,
            "department": 1,
            "parameters": 1,
            "_id": 0
        }
    ))
    
    # Create lookup dictionary: test_id -> test metadata
    test_metadata_dict = {doc['test_id']: doc for doc in test_metadata_docs}
    
    # Helper function to get parameter details from test metadata
    def get_parameter_details(test_id, test_code, parameter_type=None, param_index=None):
        """Get parameter details from MongoDB test metadata by test_code and index"""
        test_meta = test_metadata_dict.get(test_id)
        if not test_meta:
            return None
        
        # Get parameters - it can be either a dict or a list
        params_data = test_meta.get('parameters', [])
        
        # Case 1: parameters is a dictionary with parameter_type keys
        if isinstance(params_data, dict):
            # If device_id is provided and exists in parameters, use it
            if parameter_type and parameter_type in params_data:
                params_list = params_data[parameter_type]
            else:
                # Get first device's parameters
                parameter_types = test_meta.get('parameter_type', [])
                if parameter_types and len(parameter_types) > 0:
                    first_device = parameter_types[0]
                    params_list = params_data.get(first_device, [])
                else:
                    # No device_id found, try to get first value from dict
                    params_list = next(iter(params_data.values())) if params_data else []
        
        # Case 2: parameters is a list (current case for PT test)
        elif isinstance(params_data, list):
            params_list = params_data
        
        else:
            return None
        
        # Ensure params_list is actually a list
        if not isinstance(params_list, list):
            params_list = []
        
        # If param_index is provided, use it directly (faster and more accurate)
        if param_index is not None and 0 <= param_index < len(params_list):
            param = params_list[param_index]
            return {
                'parameter_name': param.get('test_name'),
                'department': param.get('department') or test_meta.get('department'),
                'sub_title': param.get('sub_title', ''),
                'value_option': param.get('value_option', [])
            }
        
        # Fallback: Find matching parameter by test_code (may not be unique!)
        matching_params = [p for p in params_list if isinstance(p, dict) and p.get('test_code') == test_code]
        
        if matching_params:
            # If multiple params have same test_code, return first one
            # (This is a limitation - ideally use param_index)
            param = matching_params[0]
            return {
                'parameter_name': param.get('test_name'),
                'department': param.get('department') or test_meta.get('department'),
                'sub_title': param.get('sub_title', ''),
                'value_option': param.get('value_option', [])
            }
        
        return None
    
    # ============================================
    # PROCESS PATIENTS USING PRE-FETCHED DATA
    # ============================================
    
    filter_normalized = None
    if testname_filter and testname_filter != 'undefined':
        testname_filter = unquote_plus(testname_filter)
        filter_normalized = normalize_testname(testname_filter)
    
    patient_data = []
    
    for patient in patients_list:
        barcode_val = str(get_val(patient, 'barcode')).zfill(0)
        
        try:
            raw_td = get_val(patient, 'testdetails')
            test_details = json.loads(raw_td) if isinstance(raw_td, str) else (raw_td or [])
        except (json.JSONDecodeError, TypeError):
            test_details = []
        
        # Initialize variables
        patient_name, patient_age, current_patient_id = "N/A", "N/A", None
        is_emergency = False
        patient_history = ""
        
        # PRIORITY 1: Get from Hmsbarcode (PRIMARY SOURCE)
        if barcode_val in hms_barcodes_dict:
            hms_info = hms_barcodes_dict[barcode_val]
            current_patient_id = hms_info['patient_id']
            patient_name = hms_info['name']
            patient_age = hms_info['age']
        
        # PRIORITY 2: BarcodeTestDetails lookup (includes emergency status and history)
        if barcode_val in barcode_details_dict:
            barcode_info = barcode_details_dict[barcode_val]
            if not current_patient_id:
                current_patient_id = barcode_info['patient_id']
            is_emergency = barcode_info['is_emergency']
            patient_history = barcode_info['patient_history']
            
            if current_patient_id in patients_dict and patient_name == "N/A":
                patient_info = patients_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age = patient_info['age']
        
        # PRIORITY 3: MongoDB franchise lookup (fallback)
        if not current_patient_id and barcode_val in mongo_billing_dict:
            current_patient_id = mongo_billing_dict[barcode_val]
            if current_patient_id in mongo_patient_dict:
                patient_info = mongo_patient_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age = patient_info['age']
        
        # PRIORITY 4: Corporate lookup (fallback)
        if not current_patient_id and barcode_val in corp_billing_dict:
            current_patient_id = corp_billing_dict[barcode_val]
            if current_patient_id in corp_employee_dict:
                corp_info = corp_employee_dict[current_patient_id]
                patient_name = corp_info['name']
                patient_age = corp_info['age']
        
        # Final fallback
        if not current_patient_id:
            current_patient_id = getattr(patient, 'patient_id', None)
        
        # Patient ID filter
        if patient_id_filter and current_patient_id != patient_id_filter:
            continue
        
        # Emergency filter
        if emergency_filter:
            if emergency_filter == 'emergency' and not is_emergency:
                continue
            elif emergency_filter == 'normal' and is_emergency:
                continue
        
        # NEW: Enrich test details with metadata from MongoDB
        enriched_test_details = []
        for test in test_details:
            test_id = test.get('test_id')
            test_code = test.get('test_code')
            parameter_type = test.get('parameter_type')
            parameters = test.get('parameters', [])
            
            # Get test metadata
            test_meta = test_metadata_dict.get(test_id, {})
            
            # Check if test has parameters array (like CBC with 20 parameters or PT with 4 parameters)
            if parameters and isinstance(parameters, list) and len(parameters) > 0:
                # This is a test with multiple parameters (e.g., CBC, PT)
                # Enrich each parameter with its metadata
                enriched_parameters = []
                
                for param_index, param in enumerate(parameters):
                    param_test_code = param.get('test_code')
                    param_value = param.get('value')
                    param_comment = param.get('comment', '')
                    param_result = param.get('result', '')
                    
                    # Get parameter-specific details from MongoDB using INDEX
                    # This is more reliable than test_code when multiple params share the same test_code
                    param_details = get_parameter_details(test_id, param_test_code, parameter_type, param_index=param_index)
                    
                    if param_details:
                        enriched_param = {
                            'test_code': param_test_code,
                            'parameter_name': param_details.get('parameter_name'),
                            'value': param_value,
                            'department': param_details.get('department', 'N/A'),
                            'sub_title': param_details.get('sub_title', ''),
                            'value_option': param_details.get('value_option', []),
                            'comment': param_comment,
                            'result': param_result
                        }
                    else:
                        # No matching parameter found in MongoDB
                        enriched_param = {
                            'test_code': param_test_code,
                            'parameter_name': None,
                            'value': param_value,
                            'department': 'N/A',
                            'sub_title': '',
                            'value_option': [],
                            'comment': param_comment,
                            'result': param_result
                        }
                    
                    enriched_parameters.append(enriched_param)
                
                # Create enriched test with parameters
                enriched_test = {
                    **test,  # Keep all existing fields
                    'test_name': test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type': test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department': test_meta.get('department', test.get('department', 'N/A')),
                    'parameters': enriched_parameters  # Replace with enriched parameters
                }
                
            else:
                # This is a single test with test_code (not parameters array)
                # Start with base test info from metadata
                enriched_test = {
                    **test,  # Keep all existing fields from core_testvalue
                    'test_name': test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type': test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department': test_meta.get('department', test.get('department', 'N/A')),
                }
                
                # If test_code exists, get parameter-specific details
                if test_code and test_code != 'N/A':
                    param_details = get_parameter_details(test_id, test_code, parameter_type)
                    
                    if param_details:
                        # Override with parameter-specific details
                        enriched_test.update({
                            'parameter_name': param_details.get('parameter_name'), 
                            'department': param_details.get('department', enriched_test['department']),
                        })
                    else:
                        # No matching parameter found, use defaults
                        enriched_test.update({
                            'parameter_name': None,
                        })
                else:
                    # No test_code, this is a main test without parameters
                    enriched_test.update({
                        'parameter_name': None,
                    })
            
            enriched_test_details.append(enriched_test)
        
        # Replace test_details with enriched version
        test_details = enriched_test_details
        
        # Testname filtering
        if filter_normalized:
            filtered_test_details = []
            for test in test_details:
                test_name = test.get('test_name', '')
                test_normalized = normalize_testname(test_name)
                match_found = (
                    test_normalized == filter_normalized or
                    filter_normalized in test_normalized or
                    test_normalized in filter_normalized or
                    all(word in test_normalized for word in filter_normalized.split() if len(word) > 2)
                )
                if match_found:
                    filtered_test_details.append(test)
            test_details = filtered_test_details
        
        # Only pending tests
        filtered_tests = [
            test for test in test_details
            if not test.get('approve', False) and not test.get('rerun', False)
        ]
        
        if not filtered_tests:
            continue
        
        loc_val = get_val(patient, 'locationId') or get_val(patient, 'location_id')
        if not loc_val:
            if barcode_val in hms_barcodes_dict:
                loc_val = hms_barcodes_dict[barcode_val].get('location_id') or 'hms'
            else:
                loc_val = 'Shanmuga Reference Lab'

        item = {
            "patient_id": current_patient_id,
            "patientname": patient_name,
            "age": patient_age,
            "locationId": loc_val,
            "barcode": barcode_val,
            "date": get_val(patient, 'date'),
            "created_date": get_val(patient, 'created_date'),
            "is_emergency": is_emergency,
            "patient_history": patient_history,
            "testdetails": filtered_tests
        }

        patient_data.append(item)
        if get_val(patient, '_scope_group') == 'selected':
            selected_patient_data.append(item)
        elif get_val(patient, '_scope_group') == 'backlog':
            backlog_patient_data.append(item)

    if scope == 'split':
        return JsonResponse({
            'success': True,
            'selected_date': selected_patient_data,
            'all_days': backlog_patient_data,
            'data': {
                'selected_date': selected_patient_data,
                'all_days': backlog_patient_data
            }
        })

    return JsonResponse(patient_data, safe=False)


@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def mb_approve_test_detail(request, barcode):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_mbtestvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode")
        created_date_str = update_data.get("created_date")
        test_id = update_data.get("test_id")
        approve_time = update_data.get("approve_time")  # Get from frontend
        if not (barcode and created_date_str and test_id):
            return JsonResponse({
                "error": "barcode, created_date, and test_id required"
            }, status=400)
        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
    except Exception as e:
        return JsonResponse({"error": f"Invalid request format: {str(e)}"}, status=400)
    # Query with barcode and created_date
    query = {"barcode": barcode, "created_date": created_date}
    test_value = collection.find_one(query)
    if not test_value:
        return JsonResponse({
            "error": "Patient record not found for given barcode & created_date."
        }, status=404)
    try:
        test_details = json.loads(test_value.get("testdetails", "[]"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to decode test details."}, status=500)
    # Find the test by test_id
    test_found = False
    for test_detail in test_details:
        if test_detail.get("test_id") == test_id:
            if test_detail.get("rerun"):
                return JsonResponse({"error": "Already reruned."}, status=400)
            if test_detail.get("approve"):
                return JsonResponse({"error": "Already approved."}, status=400)

            test_detail["approve"] = update_data.get("approve", False)
            if test_detail["approve"]:
                # Use approve_time from frontend if provided
                if approve_time:
                    test_detail["approve_time"] = approve_time
                if "approve_by" in update_data:
                    test_detail["approve_by"] = update_data["approve_by"]
            test_found = True
            break
    if not test_found:
        return JsonResponse({"error": "Test not found with given test_id."}, status=404)
    # Update the document
    result = collection.update_one(
        query,
        {"$set": {"testdetails": json.dumps(test_details)}}
    )
    if result.modified_count > 0:
        return JsonResponse({"message": "Test detail approved successfully."})
    return JsonResponse({"error": "Failed to update test detail."}, status=500)

@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def mb_rerun_test_detail(request, barcode):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_mbtestvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode")
        created_date_str = update_data.get("created_date")
        test_id = update_data.get("test_id")
        rerun_time = update_data.get("rerun_time")  # Get from frontend
        if not (barcode and created_date_str and test_id):
            return JsonResponse({
                "error": "barcode, created_date, and test_id required"
            }, status=400)
        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
    except Exception as e:
        return JsonResponse({"error": f"Invalid request format: {str(e)}"}, status=400)
    # Query with barcode and created_date
    query = {"barcode": barcode, "created_date": created_date}
    test_value = collection.find_one(query)
    if not test_value:
        return JsonResponse({
            "error": "Patient record not found for given barcode & created_date."
        }, status=404)
    try:
        test_details = json.loads(test_value.get("testdetails", "[]"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to decode test details."}, status=500)
    # Find the test by test_id
    test_found = False
    for test_detail in test_details:
        if test_detail.get("test_id") == test_id:
            if test_detail.get("approve"):
                return JsonResponse({"error": "Already approved unable to rerun the approved value."}, status=400)
            if test_detail.get("rerun"):
                return JsonResponse({"error": "Already reruned."}, status=400)

            test_detail["rerun"] = update_data.get("rerun", False)
            if test_detail["rerun"]:
                # Use rureun_time from frontend if provided
                if rerun_time:
                    test_detail["rerun_time"] = rerun_time
                if "rerun_by" in update_data:
                    test_detail["rerun_by"] = update_data["rerun_by"]
            test_found = True
            break
    if not test_found:
        return JsonResponse({"error": "Test not found with given test_id."}, status=404)
    # Update the document
    result = collection.update_one(
        query,
        {"$set": {"testdetails": json.dumps(test_details)}}
    )
    if result.modified_count > 0:
        return JsonResponse({"message": "Test detail rerun Initiated."})
    return JsonResponse({"error": "Failed to update test detail."}, status=500)

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def mb_patient_test_sorting(request):
    try:
        # Get barcode from request
        barcode = request.GET.get('barcode')
        date = request.GET.get('date', datetime.now().strftime("%Y-%m-%d"))
        
        if not barcode:
            return JsonResponse({'error': 'Missing barcode'}, status=400)
        
        # Ensure the date is in YYYY-MM-DD format
        try:
            formatted_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)
        
        # MongoDB connection
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        core_testdetails_collection = db["core_testdetails"]
        
        # Filter test values by barcode
        tests = MBTestValue.objects.filter(barcode=barcode, date=formatted_date).values("pk","testdetails", "created_date", "is_preliminary")
        test_list = []
        
        for test in tests:
            test_created_date = test.get("created_date")
            is_preliminary = test.get("is_preliminary", False)
            record_id = test.get("pk")
            testdetails_data = test["testdetails"]
            if isinstance(testdetails_data, str):
                try:
                    testdetails_list = json.loads(testdetails_data)
                except json.JSONDecodeError:
                    continue  # Skip invalid JSON
            elif isinstance(testdetails_data, list):
                testdetails_list = testdetails_data
            else:
                continue
            
            # Filter only approved tests and enrich with test_name from core_testdetails
            for test_item in testdetails_list:
                if test_item.get('approve') is True:
                    test_id = test_item.get('test_id')
                    
                    # Fetch test_name from core_testdetails collection
                    if test_id:
                        core_test = core_testdetails_collection.find_one(
                            {"test_id": test_id},
                            {"test_name": 1, "order_id": 1, "_id": 0}
                        )
                        
                        if core_test:
                            test_item['test_name'] = core_test.get('test_name', 'N/A')
                            test_item['order_id'] = core_test.get('order_id', '')
                        else:
                            test_item['test_name'] = 'N/A'
                            test_item['order_id'] = ''
                    else:
                        test_item['test_name'] = 'N/A'
                        test_item['order_id'] = ''

                    # Add created_date to each test item
                    test_item['is_preliminary'] = is_preliminary
                    test_item['record_id'] = record_id
                    test_item['created_date'] = test_created_date.isoformat() if test_created_date else None                    
                    test_list.append(test_item)
        
        # Return barcode as key
        if test_list:
            return JsonResponse({barcode: {"testdetails": test_list}})
        else:
            return JsonResponse({'error': 'No records found for this barcode'}, status=404)
    
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        # Close MongoDB connection
        if 'client' in locals():
            client.close()


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def mb_get_patient_test_details(request):
    barcode = request.GET.get('barcode')
    record_ids_param = request.GET.get('record_ids', '')
    # Check if barcode is provided
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)
    try:
        # Get patient_id and bill_no from BarcodeTestDetails using barcode
        barcode_details = BarcodeTestDetails.objects.filter(barcode=barcode).first()
        if not barcode_details:
            return JsonResponse({'error': 'No barcode details found for the given barcode'}, status=404)
        patient_id = barcode_details.patient_id
        bill_no = barcode_details.bill_no
        # Get TestValue records using patient_id and barcode
        if record_ids_param:
            record_ids = [rid.strip() for rid in record_ids_param.split(',') if rid.strip()]
            test_values = MBTestValue.objects.filter(barcode=barcode, _id__in=record_ids)
        else:
            test_values = MBTestValue.objects.filter(barcode=barcode)
        if not test_values.exists():
            return JsonResponse({'error': 'No test records found for the given barcode'}, status=404)
        # Get patient details from Patient model using patient_id
        patient = Patient.objects.filter(patient_id=patient_id).first()
        # Get billing details from Billing model using bill_no
        billing = Billing.objects.filter(bill_no=bill_no).first()
        # Get sample status
        sample_status = SampleStatus.objects.filter(patient_id=patient_id)
        # Get barcodes information
        barcodes = []
        try:
            tests = json.loads(barcode_details.testdetails) if isinstance(barcode_details.testdetails, str) else barcode_details.testdetails
            barcodes = [test.get("barcode") for test in tests if test.get("barcode")]
        except (json.JSONDecodeError, AttributeError):
            barcodes = []
        
        # Connect to MongoDB
        mongo_client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        mongo_db = mongo_client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails
        
        # Connect to global database for employee profiles
        global_db = mongo_client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs = gridfs.GridFS(global_db)
        
        # Helper function to get parameter details by index or test_code
        def get_parameter_from_core(core_test, parameter_type, test_code=None, param_index=None):
            """
            Get parameter details from core_testdetails
            Supports both dict (parameter_type-keyed) and list formats
            Uses param_index for accurate matching when available
            """
            core_parameters = core_test.get("parameters", {})
            params_list = []
            
            # Case 1: parameters is a dictionary with device_id keys
            if isinstance(core_parameters, dict):
                # Try to get parameters for the specific device_id
                if parameter_type and parameter_type != "N/A" and parameter_type in core_parameters:
                    params_list = core_parameters[parameter_type]
                else:
                    # Use first available device's parameters
                    if len(core_parameters) > 0:
                        first_device = list(core_parameters.keys())[0]
                        params_list = core_parameters[first_device]
            
            # Case 2: parameters is a list (like PT test)
            elif isinstance(core_parameters, list):
                params_list = core_parameters
            
            # Ensure params_list is actually a list
            if not isinstance(params_list, list):
                return None
            
            # If param_index is provided, use it directly (most accurate)
            if param_index is not None and 0 <= param_index < len(params_list):
                return params_list[param_index]
            
            # Fallback: Find by test_code (may not be unique)
            if test_code:
                matching_params = [p for p in params_list if isinstance(p, dict) and p.get("test_code") == test_code]
                if matching_params:
                    return matching_params[0]
            
            return None
        
        # Helper function to get employee signature data
        def get_employee_signature_data(employee_id):
            """
            Fetch employee profile and signature image from MongoDB
            Returns dict with employeeName, designation, and signature base64
            """
            if not employee_id:
                return None
            
            try:
                # Get employee profile
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None
                
                employee_name = profile.get("employeeName", "")
                designation = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                
                signature_base64 = None
                if signature_file_id:
                    try:
                        # Convert string ID to ObjectId if needed
                        from bson import ObjectId
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        
                        # Fetch signature image from GridFS
                        signature_file = fs.get(signature_file_id)
                        signature_bytes = signature_file.read()
                        
                        # Convert to base64
                        import base64
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {employee_id}: {str(e)}")
                
                return {
                    "employeeName": employee_name,
                    "designation": designation,
                    "signatureBase64": signature_base64
                }
            except Exception as e:
                print(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None
        
        all_results = []
        # Collect all unique approvers across all test values
        all_approvers = set()
        
        # Process each TestValue record
        for test_value_record in test_values:
            # Filter for approved tests only
            approved_tests = []
            
            # Parse testdetails if it's a string
            test_details_list = test_value_record.testdetails
            if isinstance(test_details_list, str):
                try:
                    test_details_list = json.loads(test_details_list)
                except:
                    test_details_list = []
            
            if not isinstance(test_details_list, list):
                test_details_list = []
            
            for test in test_details_list:
                # Check if the test is approved
                if test.get("approve") == True:  # Only include approved tests
                    test_id = test.get("test_id")
                    parameter_type = test.get("parameter_type")
                    parameters = test.get("parameters", [])
                    approve_by = test.get("approve_by", "")
                    
                    # Collect approver ID
                    if approve_by:
                        all_approvers.add(approve_by)
                    
                    # Fetch test details from core_testdetails
                    core_test = core_testdetails_collection.find_one({"test_id": test_id})
                    
                    if not core_test:
                        # Fallback to original data if core_test not found
                        testname = test.get("testname")
                        department = test.get("department", "N/A")
                        specimen_type = test.get("specimen_type", "N/A")
                        is_AG_title = test.get("is_AG_title", "N/A")
                    else:
                        testname = core_test.get("test_name")
                        department = core_test.get("department", "N/A")
                        specimen_type = core_test.get("specimen_type", "N/A")
                        is_AG_title = core_test.get("is_AG_title", False)
                    
                    comment = test.get("comment", "")
                    remarks = test.get("remarks", "")
                    colony_count = test.get("colony_count", "")
                    verified_by = test.get("verified_by", "N/A")
                    approve_time = test.get("approve_time", "N/A")
                    dispatch_time = test.get("dispatch_time", "N/A")
                    
                    # Get sample status information
                    status = None
                    if sample_status.exists():
                        for sample_status_record in sample_status:
                            status_details = sample_status_record.testdetails
                            if isinstance(status_details, str):
                                try:
                                    status_details = json.loads(status_details)
                                except:
                                    status_details = []
                            
                            if isinstance(status_details, list):
                                status = next(
                                    (s for s in status_details if s.get("test_id") == test_id or s.get("testname") == testname),
                                    None
                                )
                            if status:
                                break
                    
                    samplecollected_time = status.get("samplecollected_time") if status else None
                    received_time = status.get("received_time") if status else None
                    
                    # Build simplified test_detail with only required fields
                    test_detail = {
                        "test_id": test_id,
                        "parameter_type": parameter_type,                        
                        "record_id": str(test_value_record._id),              # ← ADD
                        "is_preliminary": test.get("is_preliminary", False),  # ← ADD
                        "testname": testname,
                        "department": department,
                        "specimen_type": specimen_type,
                        "is_AG_title": is_AG_title,
                        "remarks": remarks,
                        "colony_count": colony_count,
                        "verified_by": verified_by,
                        "approve_by": approve_by,  # Include approve_by in response
                        "approve_time": approve_time,
                        "dispatch_time": dispatch_time,
                        "samplecollected_time": samplecollected_time,
                        "received_time": received_time
                    }
                    
                    # Handle parameters - extract only essential fields
                    if parameters and len(parameters) > 0 and core_test:
                        simplified_parameters = []
                        
                        # Use index-based matching for accurate parameter retrieval
                        for param_index, param_value in enumerate(parameters):
                            test_code = param_value.get("test_code")
                            value = param_value.get("value", "")
                            result = param_value.get("result", "")
                            param_comment = param_value.get("comment", "")
                            
                            # Get parameter definition using INDEX (most accurate)
                            param_def = get_parameter_from_core(
                                core_test, 
                                parameter_type, 
                                test_code=test_code, 
                                param_index=param_index
                            )
                            
                            if param_def:
                                simplified_param = {
                                    "test_name": param_def.get("test_name", ""),
                                    "test_code": test_code,
                                    "value": value,
                                    "result": result,
                                    "comment": param_comment
                                }
                                simplified_parameters.append(simplified_param)
                            else:
                                # Fallback if parameter definition not found
                                simplified_param = {
                                    "test_name": param_value.get("name", "N/A"),
                                    "test_code": test_code,
                                    "value": value,
                                    "result": result,
                                    "comment": param_comment
                                }
                                simplified_parameters.append(simplified_param)
                        
                        test_detail["parameters"] = simplified_parameters
                    elif parameters and len(parameters) > 0:
                        # No core_test found, use original parameters with simplified fields
                        simplified_parameters = []
                        for param_value in parameters:
                            simplified_param = {
                                "test_name": param_value.get("name", "N/A"),
                                "test_code": param_value.get("test_code", ""),
                                "value": param_value.get("value", ""),
                                "result": param_value.get("result", ""),
                                "comment": param_value.get("comment", "")
                            }
                            simplified_parameters.append(simplified_param)
                        test_detail["parameters"] = simplified_parameters
                    else:
                        # No parameters - single test with value
                        test_detail["value"] = test.get("value", "")
                        test_detail["result"] = test.get("result", "")
                    
                    approved_tests.append(test_detail)
            
            # Only add patient details if there are approved tests
            if approved_tests:
                patient_details = {
                    "patient_id": patient_id,
                    "patientname": patient.patientname if patient else "N/A",
                    "age": patient.age if patient else "N/A",
                    "age_type": patient.age_type if patient else "Years",
                    "gender": patient.gender if patient else "N/A",
                    "date": test_value_record.date,
                    "barcode": test_value_record.barcode,
                    "bill_no": bill_no,
                    "barcodes": barcodes,
                    "testdetails": approved_tests,
                    "refby": billing.refby if billing else "N/A",
                    "B2B": billing.B2B if billing else False,
                    "branch": billing.branch if billing else "N/A",
                }
                all_results.append(patient_details)
        
        if not all_results:
            return JsonResponse({'error': 'No approved test records found'}, status=404)
        
        # Fetch signature data for all approvers
        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)
        
        # Prepare final response
        response_data = {
            "patient_data": all_results[0] if len(all_results) == 1 else all_results,
            "signatures": signatures_data
        }
        
        return JsonResponse(response_data, safe=False)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)  

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def hms_mb_get_patient_test_details(request):
    barcode = request.GET.get('barcode')
    record_ids_param = request.GET.get('record_ids', '')
    # Check if barcode is provided
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)
    try:
        # Get TestValue records using barcode
        # Filter by specific record_ids if provided, else get all
        if record_ids_param:
            record_ids = [rid.strip() for rid in record_ids_param.split(',') if rid.strip()]
            test_values = MBTestValue.objects.filter(barcode=barcode, _id__in=record_ids)
        else:
            test_values = MBTestValue.objects.filter(barcode=barcode)
        if not test_values.exists():
            return JsonResponse({'error': 'No test records found for the given barcode'}, status=404)
        
        # CHANGED: Get patient details from Hmsbarcode using barcode (instead of BarcodeTestDetails + Patient)
        barcode_details = Hmsbarcode.objects.filter(barcode=barcode).first()
        if not barcode_details:
            return JsonResponse({'error': 'No patient details found for the given barcode'}, status=404)
        
        # CHANGED: Get sample status from Hmssamplestatus using barcode (instead of SampleStatus with patient_id)
        sample_status = Hmssamplestatus.objects.filter(barcode=barcode)
        
        # Get barcodes information (keeping this from original logic if needed)
        barcodes = []
        try:
            # Try to get from BarcodeTestDetails if it exists for additional barcode info
            barcode_test_details = BarcodeTestDetails.objects.filter(barcode=barcode).first()
            if barcode_test_details:
                tests = json.loads(barcode_test_details.testdetails) if isinstance(barcode_test_details.testdetails, str) else barcode_test_details.testdetails
                barcodes = [test.get("barcode") for test in tests if test.get("barcode")]
        except (json.JSONDecodeError, AttributeError):
            barcodes = [barcode]  # Fallback to current barcode
        
        if not barcodes:
            barcodes = [barcode]
        
        # Connect to MongoDB
        mongo_client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        mongo_db = mongo_client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails
        
        # Connect to global database for employee profiles
        global_db = mongo_client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs = gridfs.GridFS(global_db)
        
        # Helper function to get parameter details by index or test_code
        def get_parameter_from_core(core_test, parameter_type, test_code=None, param_index=None):
            """
            Get parameter details from core_testdetails
            Supports both dict (parameter_type-keyed) and list formats
            Uses param_index for accurate matching when available
            """
            core_parameters = core_test.get("parameters", {})
            params_list = []
            
            # Case 1: parameters is a dictionary with device_id keys
            if isinstance(core_parameters, dict):
                # Try to get parameters for the specific device_id
                if parameter_type and parameter_type != "N/A" and parameter_type in core_parameters:
                    params_list = core_parameters[parameter_type]
                else:
                    # Use first available device's parameters
                    if len(core_parameters) > 0:
                        first_device = list(core_parameters.keys())[0]
                        params_list = core_parameters[first_device]
            
            # Case 2: parameters is a list (like PT test)
            elif isinstance(core_parameters, list):
                params_list = core_parameters
            
            # Ensure params_list is actually a list
            if not isinstance(params_list, list):
                return None
            
            # If param_index is provided, use it directly (most accurate)
            if param_index is not None and 0 <= param_index < len(params_list):
                return params_list[param_index]
            
            # Fallback: Find by test_code (may not be unique)
            if test_code:
                matching_params = [p for p in params_list if isinstance(p, dict) and p.get("test_code") == test_code]
                if matching_params:
                    return matching_params[0]
            
            return None
        
        # Helper function to get employee signature data
        def get_employee_signature_data(employee_id):
            """
            Fetch employee profile and signature image from MongoDB
            Returns dict with employeeName, designation, and signature base64
            """
            if not employee_id:
                return None
            
            try:
                # Get employee profile
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None
                
                employee_name = profile.get("employeeName", "")
                designation = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                
                signature_base64 = None
                if signature_file_id:
                    try:
                        # Convert string ID to ObjectId if needed
                        from bson import ObjectId
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        
                        # Fetch signature image from GridFS
                        signature_file = fs.get(signature_file_id)
                        signature_bytes = signature_file.read()
                        
                        # Convert to base64
                        import base64
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {employee_id}: {str(e)}")
                
                return {
                    "employeeName": employee_name,
                    "designation": designation,
                    "signatureBase64": signature_base64
                }
            except Exception as e:
                print(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None
        
        all_results = []
        # Collect all unique approvers across all test values
        all_approvers = set()
        
        # Process each TestValue record
        for test_value_record in test_values:
            # Filter for approved tests only
            approved_tests = []
            
            # Parse testdetails if it's a string
            test_details_list = test_value_record.testdetails
            if isinstance(test_details_list, str):
                try:
                    test_details_list = json.loads(test_details_list)
                except:
                    test_details_list = []
            
            if not isinstance(test_details_list, list):
                test_details_list = []
            
            for test in test_details_list:
                # Check if the test is approved
                if test.get("approve") == True:  # Only include approved tests
                    test_id = test.get("test_id")
                    parameter_type = test.get("parameter_type")
                    parameters = test.get("parameters", [])
                    approve_by = test.get("approve_by", "")
                    
                    # Collect approver ID
                    if approve_by:
                        all_approvers.add(approve_by)
                    
                    # Fetch test details from core_testdetails
                    core_test = core_testdetails_collection.find_one({"test_id": test_id})
                    
                    if not core_test:
                        # Fallback to original data if core_test not found
                        testname = test.get("testname")
                        department = test.get("department", "N/A")
                        specimen_type = test.get("specimen_type", "N/A")
                        is_AG_title = test.get("is_AG_title", False)
                    else:
                        testname = core_test.get("test_name")
                        department = core_test.get("department", "N/A")
                        specimen_type = core_test.get("specimen_type", "N/A")
                        is_AG_title = core_test.get("is_AG_title", False)
                    
                    comment = test.get("comment", "")
                    remarks = test.get("remarks", "")
                    colony_count = test.get("colony_count", "")
                    verified_by = test.get("verified_by", "N/A")
                    approve_time = test.get("approve_time", "N/A")
                    dispatch_time = test.get("dispatch_time", "N/A")
                    
                    # CHANGED: Get sample status information from Hmssamplestatus
                    status = None
                    if sample_status.exists():
                        for sample_status_record in sample_status:
                            status_details = sample_status_record.testdetails
                            if isinstance(status_details, str):
                                try:
                                    status_details = json.loads(status_details)
                                except:
                                    status_details = []
                            
                            if isinstance(status_details, list):
                                # Match by test_id (more reliable than testname)
                                status = next(
                                    (s for s in status_details if s.get("test_id") == test_id),
                                    None
                                )
                            if status:
                                break
                    
                    samplecollected_time = status.get("samplecollected_time") if status else None
                    received_time = status.get("received_time") if status else None
                    
                    # Build simplified test_detail with only required fields
                    test_detail = {
                        "test_id": test_id,
                        "record_id": str(test_value_record._id),              # ← ADD
                        "is_preliminary": test.get("is_preliminary", False),  # ← ADD
                        "parameter_type": parameter_type,
                        "testname": testname,
                        "department": department,
                        "specimen_type": specimen_type,
                        "is_AG_title": is_AG_title,
                        "remarks": remarks,
                        "colony_count": colony_count,
                        "verified_by": verified_by,
                        "approve_by": approve_by,  # Include approve_by in response
                        "approve_time": approve_time,
                        "dispatch_time": dispatch_time,
                        "samplecollected_time": samplecollected_time,
                        "received_time": received_time
                    }
                    
                    # Handle parameters - extract only essential fields
                    if parameters and len(parameters) > 0 and core_test:
                        simplified_parameters = []
                        
                        # Use index-based matching for accurate parameter retrieval
                        for param_index, param_value in enumerate(parameters):
                            test_code = param_value.get("test_code")
                            value = param_value.get("value", "")
                            result = param_value.get("result", "")
                            param_comment = param_value.get("comment", "")
                            
                            # Get parameter definition using INDEX (most accurate)
                            param_def = get_parameter_from_core(
                                core_test, 
                                parameter_type, 
                                test_code=test_code, 
                                param_index=param_index
                            )
                            
                            if param_def:
                                simplified_param = {
                                    "test_name": param_def.get("test_name", ""),
                                    "test_code": test_code,
                                    "value": value,
                                    "result": result,
                                    "comment": param_comment
                                }
                                simplified_parameters.append(simplified_param)
                            else:
                                # Fallback if parameter definition not found
                                simplified_param = {
                                    "test_name": param_value.get("name", "N/A"),
                                    "test_code": test_code,
                                    "value": value,
                                    "result": result,
                                    "comment": param_comment
                                }
                                simplified_parameters.append(simplified_param)
                        
                        test_detail["parameters"] = simplified_parameters
                    elif parameters and len(parameters) > 0:
                        # No core_test found, use original parameters with simplified fields
                        simplified_parameters = []
                        for param_value in parameters:
                            simplified_param = {
                                "test_name": param_value.get("name", "N/A"),
                                "test_code": param_value.get("test_code", ""),
                                "value": param_value.get("value", ""),
                                "result": param_value.get("result", ""),
                                "comment": param_value.get("comment", "")
                            }
                            simplified_parameters.append(simplified_param)
                        test_detail["parameters"] = simplified_parameters
                    else:
                        # No parameters - single test with value
                        test_detail["value"] = test.get("value", "")
                        test_detail["result"] = test.get("result", "")
                    
                    approved_tests.append(test_detail)
            
            # Only add patient details if there are approved tests
            if approved_tests:
                # CHANGED: Get patient details from Hmsbarcode instead of Patient and Billing models
                patient_details = {
                    "patient_id": barcode_details.patient_id,
                    "patientname": barcode_details.patientname,
                    "age": barcode_details.age,
                    "age_type": barcode_details.age_type if hasattr(barcode_details, 'age_type') else "Years",
                    "gender": barcode_details.gender,
                    "phone": barcode_details.phone,
                    "date": test_value_record.date,
                    "barcode": test_value_record.barcode,
                    "bill_no": barcode_details.billnumber,
                    "barcodes": barcodes,
                    "testdetails": approved_tests,
                    "refby": barcode_details.ref_doctor if hasattr(barcode_details, 'ref_doctor') else "SELF",
                    "B2B": barcode_details.B2B if hasattr(barcode_details, 'B2B') else False,
                    "branch": barcode_details.location_id if hasattr(barcode_details, 'location_id') else "N/A",
                }
                all_results.append(patient_details)
        
        if not all_results:
            return JsonResponse({'error': 'No approved test records found'}, status=404)
        
        # Fetch signature data for all approvers
        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)
        
        # Prepare final response
        response_data = {
            "patient_data": all_results[0] if len(all_results) == 1 else all_results,
            "signatures": signatures_data
        }
        
        return JsonResponse(response_data, safe=False)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def franchise_mb_get_patient_test_details(request):
    barcode = request.GET.get('barcode')
    record_ids_param = request.GET.get('record_ids', '')
    # Check if barcode is provided
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)
    try:
        # MongoDB connection
        mongo_client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        
        # Connect to franchise database
        franchise_db = mongo_client.franchise
        franchise_billing_collection = franchise_db.franchise_billing
        franchise_patient_collection = franchise_db.franchise_patient
        franchise_sample_collection = franchise_db.franchise_sample
        
        # Connect to Diagnostics database
        mongo_db = mongo_client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails
        
        # Connect to global database for employee profiles
        global_db = mongo_client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs = gridfs.GridFS(global_db)
        
        # Get franchise billing data using barcode
        franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
        if not franchise_billing:
            return JsonResponse({'error': 'Franchise billing record not found for the given barcode'}, status=404)
        
        # Extract patient_id from franchise_billing
        patient_id = franchise_billing.get('patient_id')
        if not patient_id:
            return JsonResponse({'error': 'Patient ID not found in billing record'}, status=404)
        
        # Get franchise patient data using patient_id
        franchise_patient = franchise_patient_collection.find_one({"patient_id": patient_id})
        if not franchise_patient:
            return JsonResponse({'error': 'Franchise patient not found for the given patient ID'}, status=404)
        
        # Get franchise sample data using barcode
        franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})
        
        # Get TestValue records using barcode from Django model
        if record_ids_param:
            record_ids = [rid.strip() for rid in record_ids_param.split(',') if rid.strip()]
            test_values = MBTestValue.objects.filter(barcode=barcode, _id__in=record_ids)
        else:
            test_values = MBTestValue.objects.filter(barcode=barcode)
        if not test_values.exists():
            return JsonResponse({'error': 'No test records found for the given barcode'}, status=404)
        
        # Get barcodes information - collect all unique barcodes for this patient
        barcodes = []
        try:
            # Get all barcodes associated with this patient_id
            all_barcodes_for_patient = franchise_billing_collection.find(
                {"patient_id": patient_id}, 
                {"barcode": 1, "_id": 0}
            )
            barcodes = [bc.get("barcode") for bc in all_barcodes_for_patient if bc.get("barcode")]
            # Remove duplicates while preserving order
            barcodes = list(dict.fromkeys(barcodes))
        except Exception:
            barcodes = [barcode]  # Fallback to current barcode
        
        if not barcodes:
            barcodes = [barcode]
        
        # Helper function to get parameter details by index or test_code
        def get_parameter_from_core(core_test, parameter_type, test_code=None, param_index=None):
            """
            Get parameter details from core_testdetails
            Supports both dict (parameter_type-keyed) and list formats
            Uses param_index for accurate matching when available
            """
            core_parameters = core_test.get("parameters", {})
            params_list = []
            
            # Case 1: parameters is a dictionary with device_id keys
            if isinstance(core_parameters, dict):
                # Try to get parameters for the specific device_id
                if parameter_type and parameter_type != "N/A" and parameter_type in core_parameters:
                    params_list = core_parameters[parameter_type]
                else:
                    # Use first available device's parameters
                    if len(core_parameters) > 0:
                        first_device = list(core_parameters.keys())[0]
                        params_list = core_parameters[first_device]
            
            # Case 2: parameters is a list (like PT test)
            elif isinstance(core_parameters, list):
                params_list = core_parameters
            
            # Ensure params_list is actually a list
            if not isinstance(params_list, list):
                return None
            
            # If param_index is provided, use it directly (most accurate)
            if param_index is not None and 0 <= param_index < len(params_list):
                return params_list[param_index]
            
            # Fallback: Find by test_code (may not be unique)
            if test_code:
                matching_params = [p for p in params_list if isinstance(p, dict) and p.get("test_code") == test_code]
                if matching_params:
                    return matching_params[0]
            
            return None
        
        # Helper function to get employee signature data
        def get_employee_signature_data(employee_id):
            """
            Fetch employee profile and signature image from MongoDB
            Returns dict with employeeName, designation, and signature base64
            """
            if not employee_id:
                return None
            
            try:
                # Get employee profile
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None
                
                employee_name = profile.get("employeeName", "")
                designation = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                
                signature_base64 = None
                if signature_file_id:
                    try:
                        # Convert string ID to ObjectId if needed
                        from bson import ObjectId
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        
                        # Fetch signature image from GridFS
                        signature_file = fs.get(signature_file_id)
                        signature_bytes = signature_file.read()
                        
                        # Convert to base64
                        import base64
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"Error fetching signature for employee {employee_id}: {str(e)}")
                
                return {
                    "employeeName": employee_name,
                    "designation": designation,
                    "signatureBase64": signature_base64
                }
            except Exception as e:
                print(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None
        
        # Parse testdetails from franchise_sample for status information
        sample_testdetails = []
        if franchise_sample:
            try:
                sample_testdetails = json.loads(franchise_sample.get('testdetails', '[]'))
            except json.JSONDecodeError:
                sample_testdetails = []
        
        all_results = []
        # Collect all unique approvers across all test values
        all_approvers = set()
        
        # Process each TestValue record
        for test_value_record in test_values:
            # Filter for approved tests only
            approved_tests = []
            
            # Parse testdetails if it's a string
            test_details_list = test_value_record.testdetails
            if isinstance(test_details_list, str):
                try:
                    test_details_list = json.loads(test_details_list)
                except:
                    test_details_list = []
            
            if not isinstance(test_details_list, list):
                test_details_list = []
            
            for test in test_details_list:
                # Check if the test is approved
                if test.get("approve") == True:  # Only include approved tests
                    test_id = test.get("test_id")
                    parameter_type = test.get("parameter_type")
                    parameters = test.get("parameters", [])
                    approve_by = test.get("approve_by", "")
                    
                    # Collect approver ID
                    if approve_by:
                        all_approvers.add(approve_by)
                    
                    # Fetch test details from core_testdetails
                    core_test = core_testdetails_collection.find_one({"test_id": test_id})
                    
                    if not core_test:
                        # Fallback to original data if core_test not found
                        testname = test.get("testname")
                        department = test.get("department", "N/A")
                        specimen_type = test.get("specimen_type", "N/A")
                        is_AG_title = test.get("is_AG_title", False)
                    else:
                        testname = core_test.get("test_name")
                        department = core_test.get("department", "N/A")
                        specimen_type = core_test.get("specimen_type", "N/A")
                        is_AG_title = core_test.get("is_AG_title", False)
                    
                    comment = test.get("comment", "")
                    remarks = test.get("remarks", "")
                    colony_count = test.get("colony_count", "")
                    verified_by = test.get("verified_by", "N/A")
                    approve_time = test.get("approve_time", "N/A")
                    
                    # Get sample status information from franchise_sample
                    status = None
                    if sample_testdetails:
                        status = next(
                            (s for s in sample_testdetails if s.get("test_id") == test_id or s.get("testname") == testname),
                            None
                        )
                    
                    samplecollected_time = status.get("samplecollected_time") if status else None
                    received_time = status.get("received_time") if status else None
                    
                    # Build simplified test_detail with only required fields
                    test_detail = {
                        "test_id": test_id,
                        "parameter_type": parameter_type,
                        "record_id": str(test_value_record._id),
                        "is_preliminary": test.get("is_preliminary", False),
                        "testname": testname,
                        "department": department,
                        "specimen_type": specimen_type,
                        "is_AG_title": is_AG_title,
                        "remarks": remarks,
                        "colony_count": colony_count,
                        "verified_by": verified_by,
                        "approve_by": approve_by,  # Include approve_by in response
                        "approve_time": approve_time,
                        "samplecollected_time": samplecollected_time,
                        "received_time": received_time
                    }
                    
                    # Handle parameters - extract only essential fields
                    if parameters and len(parameters) > 0 and core_test:
                        simplified_parameters = []
                        
                        # Use index-based matching for accurate parameter retrieval
                        for param_index, param_value in enumerate(parameters):
                            test_code = param_value.get("test_code")
                            value = param_value.get("value", "")
                            result = param_value.get("result", "")
                            param_comment = param_value.get("comment", "")
                            
                            # Get parameter definition using INDEX (most accurate)
                            param_def = get_parameter_from_core(
                                core_test, 
                                parameter_type, 
                                test_code=test_code, 
                                param_index=param_index
                            )
                            
                            if param_def:
                                simplified_param = {
                                    "test_name": param_def.get("test_name", ""),
                                    "test_code": test_code,
                                    "value": value,
                                    "result": result,
                                    "comment": param_comment
                                }
                                simplified_parameters.append(simplified_param)
                            else:
                                # Fallback if parameter definition not found
                                simplified_param = {
                                    "test_name": param_value.get("name", "N/A"),
                                    "test_code": test_code,
                                    "value": value,
                                    "result": result,
                                    "comment": param_comment
                                }
                                simplified_parameters.append(simplified_param)
                        
                        test_detail["parameters"] = simplified_parameters
                    elif parameters and len(parameters) > 0:
                        # No core_test found, use original parameters with simplified fields
                        simplified_parameters = []
                        for param_value in parameters:
                            simplified_param = {
                                "test_name": param_value.get("name", "N/A"),
                                "test_code": param_value.get("test_code", ""),
                                "value": param_value.get("value", ""),
                                "result": param_value.get("result", ""),
                                "comment": param_value.get("comment", "")
                            }
                            simplified_parameters.append(simplified_param)
                        test_detail["parameters"] = simplified_parameters
                    else:
                        # No parameters - single test with value
                        test_detail["value"] = test.get("value", "")
                        test_detail["result"] = test.get("result", "")
                    
                    approved_tests.append(test_detail)
            
            # Only add patient details if there are approved tests
            if approved_tests:
                # Get patient details from franchise collections
                patient_details = {
                    "patient_id": patient_id,
                    "patientname": franchise_patient.get("patientname", "N/A"),
                    "age": franchise_patient.get("age", "N/A"),
                    "age_type": franchise_patient.get("age_type", "Years"),
                    "gender": franchise_patient.get("gender", "N/A"),
                    "date": franchise_billing.get("created_date"),
                    "barcode": barcode,
                    "bill_no": franchise_billing.get("bill_no", "N/A"),
                    "barcodes": barcodes,
                    "testdetails": approved_tests,
                    "refby": franchise_billing.get("referredDoctor", "SELF"),
                    "B2B": franchise_billing.get("B2B", False),
                    "branch": franchise_billing.get("franchise_id", "N/A"),
                }
                all_results.append(patient_details)
        
        if not all_results:
            return JsonResponse({'error': 'No approved test records found'}, status=404)
        
        # Fetch signature data for all approvers
        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)
        
        # Close MongoDB connection
        mongo_client.close()
        
        # Prepare final response
        response_data = {
            "patient_data": all_results[0] if len(all_results) == 1 else all_results,
            "signatures": signatures_data
        }
        
        return JsonResponse(response_data, safe=False)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)


@api_view(['PATCH'])
@permission_classes([HasRoleAndDataPermission])
def mb_update_dispatch_status(request, barcode):
    """
    Update dispatch status for a specific test in core_testvalue collection.
    Uses barcode, test_id, and created_date for accurate targeting.
    """
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics  # Database name
    collection = db.core_mbtestvalue
    
    try:
        # Get parameters from request data
        auth_user_id = request.data.get('auth-user-id')
        auth_user_name = request.data.get('auth-user-name')
        test_id = request.data.get('test_id')
        created_date_str = request.data.get('created_date')
        
        if not auth_user_id:
            return Response(
                {"error": "auth-user-id parameter is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not test_id:
            return Response(
                {"error": "test_id parameter is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not created_date_str:
            return Response(
                {"error": "created_date parameter is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Parse the created_date string to datetime object
        try:
            # Parse ISO format datetime string (e.g., "2026-01-07T07:36:50.214000+00:00")
            created_date = parse_datetime(created_date_str)
            if not created_date:
                # Try alternative parsing if parse_datetime fails
                created_date = datetime.fromisoformat(created_date_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError) as e:
            return Response(
                {"error": f"Invalid created_date format: {created_date_str}. Expected ISO format."}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Build the query filter with barcode and created_date
        query_filter = {
            "barcode": barcode,
            "created_date": created_date
        }
        
        # Find the specific document
        test_value_record = collection.find_one(query_filter)
        
        if not test_value_record:
            return Response({
                "error": f"No TestValue record found for barcode: {barcode} and created_date: {created_date}",
                "debug_info": {
                    "barcode": barcode,
                    "created_date_str": created_date_str,
                    "parsed_created_date": str(created_date)
                }
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Parse the testdetails field
        test_details = test_value_record.get("testdetails")
        
        # Handle both string and list formats
        if isinstance(test_details, str):
            test_details = json.loads(test_details)
        elif not isinstance(test_details, list):
            return Response({
                "error": "Invalid testdetails format"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Find and update the specific test
        test_found = False
        tests_updated = 0
        
        for test in test_details:
            if test.get("test_id") == test_id:
                test_found = True
                # Only update if not already dispatched
                if not test.get("dispatch", False):
                    test["dispatch"] = True
                    test["dispatched_by"] = auth_user_name
                    test["dispatch_time"] = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
                    tests_updated += 1
                break
        
        if not test_found:
            return Response({
                "error": f"Test with test_id {test_id} not found in the document"
            }, status=status.HTTP_404_NOT_FOUND)
        
        if tests_updated == 0:
            return Response({
                "message": f"Test is already dispatched",
                "test_id": test_id,
                "barcode": barcode,
                "created_date": created_date_str
            }, status=status.HTTP_200_OK)
        
        # Convert the updated testdetails back to a JSON string
        updated_test_details = json.dumps(test_details)
        
        # Update the document in MongoDB
        result = collection.update_one(
            {"_id": test_value_record["_id"]},
            {"$set": {
                "testdetails": updated_test_details,
                "lastmodified_by": auth_user_id,
                "lastmodified_date": datetime.now(IST)
            }}
        )
        
        if result.matched_count > 0:
            return Response({
                "message": "Dispatch status updated successfully",
                "barcode": barcode,
                "created_date": created_date_str,
                "test_id": test_id,
                "tests_updated": tests_updated,
                "modified_by": auth_user_id,
                "document_id": str(test_value_record["_id"])
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "Failed to update the document"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        import traceback
        return Response({
            "error": str(e),
            "traceback": traceback.format_exc()
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        client.close()
