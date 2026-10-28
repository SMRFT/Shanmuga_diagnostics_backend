from rest_framework.response import Response
from django.http import JsonResponse
from datetime import datetime, timedelta, date
from rest_framework.decorators import api_view, permission_classes
from rest_framework import status
from pymongo import MongoClient
from bson import ObjectId
import os, json, re, pytz, base64, gridfs
from dotenv import load_dotenv
load_dotenv()
from urllib.parse import unquote_plus
from django.views.decorators.csrf import csrf_exempt
from pyauth.auth import HasRoleAndDataPermission
from ..models import Patient, BarcodeTestDetails, SampleStatus, Hmssamplestatus, Hmsbarcode, Billing, TestValue, PathologyTestValue

# Timezone definition
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)

_pathology_mongo_client = None
_pathology_test_details_cache = None
_pathology_test_details_cache_time = 0

def get_pathology_mongo_client():
    global _pathology_mongo_client
    if _pathology_mongo_client is None:
        _pathology_mongo_client = MongoClient(
            os.getenv('GLOBAL_DB_HOST'),
            maxPoolSize=50,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000
        )
    return _pathology_mongo_client

def get_cached_pathology_test_details(db):
    global _pathology_test_details_cache, _pathology_test_details_cache_time
    import time as pytime
    now = pytime.time()
    if _pathology_test_details_cache is None or (now - _pathology_test_details_cache_time > 1800):
        cache = {}
        for td_doc in db.core_testdetails.find({}, {
            "_id": 0, "test_id": 1, "test_name": 1, "department": 1,
            "collection_container": 1, "TAT_Time": 1, "is_pathology": 1
        }):
            tid = td_doc.get('test_id')
            cache[tid] = td_doc
            cache[str(tid)] = td_doc
            if str(tid).isdigit():
                cache[int(tid)] = td_doc
        _pathology_test_details_cache = cache
        _pathology_test_details_cache_time = now
    return _pathology_test_details_cache

def normalize_testname(name):
    if not name:
        return ""
    normalized = re.sub(r'\s+', ' ', name.strip().lower())
    normalized = normalized.replace('&', 'and').replace('/', ' ')
    return normalized

def safe_datetime_to_string(dt_obj):
    if not dt_obj:
        return None
    if isinstance(dt_obj, str):
        return dt_obj
    if isinstance(dt_obj, datetime):
        return dt_obj.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt_obj)

def get_sample_field(obj, field, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def pathology_testvalue(request):
    """
    Returns samples and their tests where is_pathology is True.
    Separate report generation for Pathology tests.
    """
    try:
        from_date_str = request.query_params.get('from_date', None)
        to_date_str = request.query_params.get('to_date', None)
        date_str = request.query_params.get('date', None)
        source = request.query_params.get('source', 'all')
        pending_type = request.query_params.get('pending_type', None)
        scope = request.query_params.get('scope', 'date_range')
        exclude_from_date_str = request.query_params.get('exclude_from_date', None)
        exclude_to_date_str = request.query_params.get('exclude_to_date', None)
        exclude_dispatched = request.query_params.get('exclude_dispatched', 'true' if pending_type else 'false').lower() == 'true'

        start_of_range = None
        end_of_range = None
        exclude_start = None
        exclude_end = None

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

        client = get_pathology_mongo_client()
        db = client.Diagnostics
        test_details_cache = get_cached_pathology_test_details(db)

        def get_test_details(test_id):
            if test_id is None:
                return None
            return test_details_cache.get(test_id) or test_details_cache.get(str(test_id)) or (test_details_cache.get(int(test_id)) if str(test_id).isdigit() else None)

        sample_data_by_barcode = {}
        all_candidate_barcodes = set()

        # 1. HMS Samples
        if source in ['hms', 'all']:
            hms_query = {
                'testdetails': {'$exists': True, '$ne': '[]', '$ne': []}
            }
            if scope == 'date_range':
                hms_query['date'] = {'$gte': start_of_range, '$lte': end_of_range}
            elif scope == 'split':
                two_months_ago = datetime.combine(start_of_range.date() - timedelta(days=60), datetime.min.time())
                hms_query['date'] = {'$gte': two_months_ago, '$lte': end_of_range}
            elif scope == 'all_days' and exclude_start and exclude_end:
                hms_query['date'] = {'$not': {'$gte': exclude_start, '$lte': exclude_end}}

            for doc in db.core_hmssamplestatus.find(
                hms_query,
                {'barcode': 1, 'date': 1, 'created_date': 1, 'created_by': 1, 'location_id': 1, 'testdetails': 1, 'patient_id': 1}
            ):
                bc = doc.get('barcode')
                if bc:
                    sample_data_by_barcode[bc] = {'source': 'hms', 'sample': doc}
                    all_candidate_barcodes.add(bc)

        # 2. Regular Samples
        if source in ['regular', 'all']:
            reg_query = {
                'testdetails': {'$exists': True, '$ne': '[]', '$ne': []}
            }
            if scope == 'date_range':
                reg_query['date'] = {'$gte': start_of_range, '$lte': end_of_range}
            elif scope == 'split':
                two_months_ago = datetime.combine(start_of_range.date() - timedelta(days=60), datetime.min.time())
                reg_query['date'] = {'$gte': two_months_ago, '$lte': end_of_range}
            elif scope == 'all_days' and exclude_start and exclude_end:
                reg_query['date'] = {'$not': {'$gte': exclude_start, '$lte': exclude_end}}

            for doc in db.core_samplestatus.find(
                reg_query,
                {'barcode': 1, 'date': 1, 'created_date': 1, 'created_by': 1, 'locationId': 1, 'location_id': 1, 'testdetails': 1, 'patient_id': 1}
            ):
                bc = doc.get('barcode')
                if bc and bc not in sample_data_by_barcode:
                    sample_data_by_barcode[bc] = {'source': 'regular', 'sample': doc}
                    all_candidate_barcodes.add(bc)

        # 3. Pre-fetch test values for candidate barcodes from core_pathologytestvalue
        test_values_by_barcode = {}
        if all_candidate_barcodes:
            tv_projection = {'barcode': 1, 'testdetails': 1, 'created_date': 1, 'lastmodified_date': 1}
            for tv in db.core_pathologytestvalue.find({'barcode': {'$in': list(all_candidate_barcodes)}}, tv_projection):
                bc = tv.get('barcode')
                if bc not in test_values_by_barcode:
                    test_values_by_barcode[bc] = []
                test_values_by_barcode[bc].append(tv)

            # Fallback to core_testvalue for any barcodes not yet in core_pathologytestvalue
            remaining_bc = [b for b in all_candidate_barcodes if b not in test_values_by_barcode]
            if remaining_bc:
                for tv in db.core_testvalue.find({'barcode': {'$in': remaining_bc}}, tv_projection):
                    bc = tv.get('barcode')
                    if bc not in test_values_by_barcode:
                        test_values_by_barcode[bc] = []
                    test_values_by_barcode[bc].append(tv)

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
                    test['is_pathology'] = test_detail.get('is_pathology', False)
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
            test_name = test.get('testname') or test.get('test_name')
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
            })

            if not test_id and not test_name:
                return test

            test_values = test_values_by_barcode.get(barcode, [])
            for tv in test_values:
                raw_td = tv.get('testdetails') if isinstance(tv, dict) else getattr(tv, 'testdetails', [])
                tv_details = parse_testdetails(raw_td)

                for tv_detail in tv_details:
                    is_match = False
                    if test_id and tv_detail.get('test_id'):
                        is_match = str(tv_detail.get('test_id')) == str(test_id)
                    elif test_name and tv_detail.get('testname'):
                        is_match = normalize_testname(tv_detail.get('testname')) == normalize_testname(test_name)

                    if is_match:
                        raw_appr = tv_detail.get('approve_time')
                        raw_rr = tv_detail.get('rerun_time')
                        raw_disp = tv_detail.get('dispatch_time')
                        appr_time = None if raw_appr in ['null', 'undefined', 'None', '', None] else raw_appr
                        rr_time = None if raw_rr in ['null', 'undefined', 'None', '', None] else raw_rr
                        disp_time = None if raw_disp in ['null', 'undefined', 'None', '', None] else raw_disp

                        test.update({
                            'rerun': bool(tv_detail.get('rerun', False)),
                            'approve': bool(tv_detail.get('approve', False)),
                            'dispatch': bool(tv_detail.get('dispatch', False)),
                            'is_dispatched': bool(tv_detail.get('dispatch', False)),
                            'test_value_exists': True,
                            'approve_time': appr_time,
                            'rerun_time': rr_time,
                            'dispatch_time': disp_time,
                            'approve_by': tv_detail.get('approve_by'),
                            'dispatch_by': tv_detail.get('dispatch_by'),
                            'value': tv_detail.get('value'),
                            'remarks': tv_detail.get('remarks'),
                            'comment': tv_detail.get('comment'),
                            'verified_by': tv_detail.get('verified_by'),
                        })
                        return test
            return test

        combined_results = []
        all_tech_barcodes = set()
        all_doc_barcodes = set()
        all_dispatch_barcodes = set()

        # Step 3A: Identify qualifying HMS samples
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
                    # ONLY PATHOLOGY TESTS
                    if not enriched_test.get('is_pathology'):
                        continue

                    matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                    if matched_test.get('dispatch') or (exclude_dispatched and matched_test.get('dispatch')):
                        continue

                    location_id = get_sample_field(sample_status, 'location_id') or "Shanmuga Reference Lab"

                    if not matched_test.get('test_value_exists') or matched_test.get('rerun'):
                        all_tech_barcodes.add(barcode)
                    elif not matched_test.get('approve'):
                        all_doc_barcodes.add(barcode)
                    elif not matched_test.get('dispatch'):
                        loc_lower = str(location_id).lower()
                        if 'hms' in loc_lower or 'shanmuga' in loc_lower or 'reference' in loc_lower:
                            all_dispatch_barcodes.add(barcode)

                    if pending_type == 'technician':
                        if not (not matched_test.get('test_value_exists') or matched_test.get('rerun')):
                            continue
                    elif pending_type == 'doctor':
                        if not (matched_test.get('test_value_exists') and not matched_test.get('approve') and not matched_test.get('rerun')):
                            continue
                    elif pending_type == 'dispatch':
                        if not (matched_test.get('approve') and not matched_test.get('dispatch') and not matched_test.get('rerun')):
                            continue
                        loc_lower = str(location_id).lower()
                        if not ('hms' in loc_lower or 'shanmuga' in loc_lower or 'reference' in loc_lower):
                            continue

                    updated_tests.append(matched_test)

                if updated_tests:
                    qualifying_hms.append((sample_status, barcode, updated_tests))
                    needed_hms_barcodes.append(barcode)

        # Step 3B: Identify qualifying Regular samples
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
                    # ONLY PATHOLOGY TESTS
                    if not enriched_test.get('is_pathology'):
                        continue

                    matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                    if matched_test.get('dispatch') or (exclude_dispatched and matched_test.get('dispatch')):
                        continue

                    location_id = get_sample_field(sample_status, 'locationId') or get_sample_field(sample_status, 'location_id') or "Shanmuga Reference Lab"

                    if not matched_test.get('test_value_exists') or matched_test.get('rerun'):
                        all_tech_barcodes.add(barcode)
                    elif not matched_test.get('approve'):
                        all_doc_barcodes.add(barcode)
                    elif not matched_test.get('dispatch'):
                        all_dispatch_barcodes.add(barcode)

                    if pending_type == 'technician':
                        if not (not matched_test.get('test_value_exists') or matched_test.get('rerun')):
                            continue
                    elif pending_type == 'doctor':
                        if not (matched_test.get('test_value_exists') and not matched_test.get('approve') and not matched_test.get('rerun')):
                            continue
                    elif pending_type == 'dispatch':
                        if not (matched_test.get('approve') and not matched_test.get('dispatch') and not matched_test.get('rerun')):
                            continue

                    updated_tests.append(matched_test)

                if updated_tests:
                    qualifying_regular.append((sample_status, barcode, updated_tests))
                    needed_regular_barcodes.append(barcode)

        # Step 3C: TARGETED fetch for patient metadata
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
                'lastmodified_by': get_sample_field(sample_status, 'lastmodified_by'),
                'lastmodified_date': safe_datetime_to_string(get_sample_field(sample_status, 'lastmodified_date')),
                'date': safe_datetime_to_string(get_sample_field(sample_status, 'date')),
                'barcode': barcode,
                'location_id': location_id,
                'testdetails': updated_tests,
                'patientname': patient_name,
                'patient_id': patient_id,
                'age': age,
                'gender': gender,
                'IPOPType': IPOPType,
                'phone': phone,
                'ref_doctor': ref_doctor,
                'barcode_by': barcode_by,
                'barcode_date': safe_datetime_to_string(barcode_date),
            })

        # Assemble Regular Results
        for sample_status, barcode, updated_tests in qualifying_regular:
            patient_name = "Unknown Patient"
            patient_id = "Unknown ID"
            age = "Unknown"
            gender = "Unknown"
            location_id = get_sample_field(sample_status, 'locationId') or get_sample_field(sample_status, 'location_id') or "Shanmuga Reference Lab"
            phone = "Unknown"
            ref_doctor = "Unknown"
            barcode_by = "Unknown"
            barcode_date = "Unknown"

            barcode_details = regular_barcodes_dict.get(barcode)
            if barcode_details:
                patient_id = get_sample_field(barcode_details, 'patient_id') or "Unknown ID"
                patient_name = get_sample_field(barcode_details, 'patientname') or "Unknown Patient"
                age = get_sample_field(barcode_details, 'age') or "Unknown"
                gender = get_sample_field(barcode_details, 'gender') or "Unknown"
                phone = get_sample_field(barcode_details, 'phone') or "Unknown"
                ref_doctor = get_sample_field(barcode_details, 'ref_doctor') or "Unknown"
                barcode_by = get_sample_field(barcode_details, 'created_by') or "Unknown"
                barcode_date = get_sample_field(barcode_details, 'created_date') or "Unknown"
            elif get_sample_field(sample_status, 'patient_id'):
                patient_id = get_sample_field(sample_status, 'patient_id')

            combined_results.append({
                'id': str(get_sample_field(sample_status, 'id') or get_sample_field(sample_status, '_id')),
                'created_by': get_sample_field(sample_status, 'created_by'),
                'created_date': safe_datetime_to_string(get_sample_field(sample_status, 'created_date')),
                'lastmodified_by': get_sample_field(sample_status, 'lastmodified_by'),
                'lastmodified_date': safe_datetime_to_string(get_sample_field(sample_status, 'lastmodified_date')),
                'date': safe_datetime_to_string(get_sample_field(sample_status, 'date')),
                'barcode': barcode,
                'location_id': location_id,
                'testdetails': updated_tests,
                'patientname': patient_name,
                'patient_id': patient_id,
                'age': age,
                'gender': gender,
                'phone': phone,
                'ref_doctor': ref_doctor,
                'barcode_by': barcode_by,
                'barcode_date': safe_datetime_to_string(barcode_date),
            })

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

# Alias for delegation
get_pathology_testvalue = pathology_testvalue


@api_view(["GET"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def pathology_get_test_values(request):
    """
    Returns patient records with test values specifically for Pathology Report Authorization.
    Only includes tests where is_pathology: True.
    """
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    db = client.franchise
    billing_col = db.franchise_billing
    patient_col = db.franchise_patient

    corp = client.Corporatehealthcheckup
    billing_collection = corp.core_billing
    patient_collection = corp.core_chcregistration

    diagnostics = client.Diagnostics
    test_details_col = diagnostics.core_testdetails

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    patient_id_filter = request.GET.get('patient_id')
    testname_filter = request.GET.get('testname')
    emergency_filter = request.GET.get('emergency')
    date_filter = request.GET.get('date')
    barcode_filter = request.GET.get('barcode')
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
        # Delegate to pathology_testvalue to obtain active doctor-pending barcodes
        sub_request = request._request if hasattr(request, '_request') else request
        orig_get = sub_request.GET
        sub_get = orig_get.copy()
        sub_get['pending_type'] = 'doctor'
        sub_get['scope'] = 'split'
        sub_get['exclude_dispatched'] = 'true'
        sub_request.GET = sub_get

        try:
            view_func = pathology_testvalue.cls.as_view(permission_classes=[])
            res = view_func(sub_request)
            res_data = getattr(res, 'data', {})
        finally:
            sub_request.GET = orig_get

        sel_barcodes = [str(p['barcode']) for p in res_data.get('selected_date', []) if p.get('barcode')]
        bak_barcodes = [str(p['barcode']) for p in res_data.get('all_days', []) if p.get('barcode')]

        selected_docs = list(diagnostics.core_pathologytestvalue.find({
            'barcode': {'$in': sel_barcodes}
        }).sort('created_date', -1)) if sel_barcodes else []
        found_sel = {d.get('barcode') for d in selected_docs}
        missing_sel = [b for b in sel_barcodes if b not in found_sel]
        if missing_sel:
            selected_docs += list(diagnostics.core_testvalue.find({'barcode': {'$in': missing_sel}}).sort('created_date', -1))

        for d in selected_docs:
            d['_scope_group'] = 'selected'

        backlog_docs = list(diagnostics.core_pathologytestvalue.find({
            'barcode': {'$in': bak_barcodes}
        }).sort('created_date', -1)) if bak_barcodes else []
        found_bak = {d.get('barcode') for d in backlog_docs}
        missing_bak = [b for b in bak_barcodes if b not in found_bak]
        if missing_bak:
            backlog_docs += list(diagnostics.core_testvalue.find({'barcode': {'$in': missing_bak}}).sort('created_date', -1))

        for d in backlog_docs:
            d['_scope_group'] = 'backlog'

        patients_list = selected_docs + backlog_docs
    else:
        # Direct lookup (e.g., DoctorForm or specific date/patient query)
        query = {}
        if barcode_filter:
            query['barcode'] = barcode_filter
        elif patient_id_filter:
            # Find barcodes for this patient
            p_barcodes = list(diagnostics.core_hmsbarcode.distinct('barcode', {'patient_id': patient_id_filter}))
            if not p_barcodes:
                p_barcodes = list(diagnostics.core_barcodetestdetails.distinct('barcode', {'patient_id': patient_id_filter}))
            if p_barcodes:
                query['barcode'] = {'$in': p_barcodes}
            else:
                query['patient_id'] = patient_id_filter
        elif date_filter:
            try:
                dt = datetime.strptime(date_filter, "%Y-%m-%d").date()
                query['date'] = {'$gte': datetime.combine(dt, datetime.min.time()), '$lte': datetime.combine(dt, datetime.max.time())}
            except Exception:
                pass

        patients_list = list(diagnostics.core_pathologytestvalue.find(query).sort('created_date', -1))
        if not patients_list:
            patients_list = list(diagnostics.core_testvalue.find(query).sort('created_date', -1))

    if not patients_list:
        if scope == 'split':
            return JsonResponse({
                'success': True,
                'selected_date': [],
                'all_days': [],
                'data': {'selected_date': [], 'all_days': []}
            })
        return JsonResponse([], safe=False)

    barcodes = [get_val(p, 'barcode') for p in patients_list if get_val(p, 'barcode')]

    # 1. Fetch from Hmsbarcode
    hms_barcodes_dict = {
        b.get('barcode'): {
            'patient_id': b.get('patient_id'),
            'name': b.get('patientname'),
            'age': b.get('age'),
            'age_type': b.get('age_type', ''),
            'billnumber': b.get('billnumber', ''),
            'gender': b.get('gender'),
            'phone': b.get('phone', ''),
            'ref_doctor': b.get('ref_doctor', ''),
            'testdetails': b.get('testdetails', []),
        }
        for b in diagnostics.core_hmsbarcode.find({'barcode': {'$in': barcodes}})
        if b.get('barcode')
    }

    # 2. Fetch from BarcodeTestDetails
    barcode_details_dict = {
        b.get('barcode'): {
            'patient_id': b.get('patient_id'),
            'name': b.get('patientname'),
            'age': b.get('age'),
            'gender': b.get('gender'),
            'phone': b.get('phone', ''),
            'ref_doctor': b.get('ref_doctor', ''),
            'emergency': b.get('is_emergency', False) or b.get('emergency', False),
            'patient_history': b.get('patient_history', '') or ''
        }
        for b in diagnostics.core_barcodetestdetails.find({'barcode': {'$in': barcodes}})
        if b.get('barcode')
    }

    # Collect all test_ids to bulk fetch metadata
    all_test_ids = set()
    for patient in patients_list:
        try:
            raw_td = get_val(patient, 'testdetails')
            test_details = json.loads(raw_td) if isinstance(raw_td, str) else raw_td
            for test in (test_details or []):
                if test.get('test_id'):
                    all_test_ids.add(test['test_id'])
        except (json.JSONDecodeError, TypeError):
            continue

    test_metadata_docs = list(test_details_col.find(
        {"test_id": {"$in": list(all_test_ids)}},
        {
            "test_id": 1,
            "test_name": 1,
            "specimen_type": 1,
            "collection_container": 1,
            "department": 1,
            "critical_range": 1,
            "interpretation": 1,
            "lod": 1,
            "device_id": 1,
            "parameters": 1,
            "unit": 1,
            "method": 1,
            "reference_range": 1,
            "is_pathology": 1,
            "_id": 0
        }
    ))
    test_metadata_dict = {doc['test_id']: doc for doc in test_metadata_docs}

    def resolve_reference_range(param, gender):
        gender_key = (gender or '').strip().lower()
        if gender_key == 'male' and param.get('male'):
            return param['male']
        if gender_key == 'female' and param.get('female'):
            return param['female']
        return param.get('reference_range') or ''

    def get_parameter_details(test_id, test_code, device_id=None, param_index=None, gender=None):
        test_meta = test_metadata_dict.get(test_id)
        if not test_meta:
            return None
        params_data = test_meta.get('parameters', [])
        if isinstance(params_data, dict):
            if device_id and device_id in params_data:
                params_list = params_data[device_id]
            else:
                device_ids = test_meta.get('device_id', [])
                if device_ids and len(device_ids) > 0:
                    params_list = params_data.get(device_ids[0], [])
                else:
                    params_list = next(iter(params_data.values())) if params_data else []
        elif isinstance(params_data, list):
            params_list = params_data
        else:
            params_list = []

        if param_index is not None and 0 <= param_index < len(params_list):
            param = params_list[param_index]
            return {
                'parameter_name': param.get('test_name'),
                'unit': param.get('unit'),
                'reference_range': resolve_reference_range(param, gender),
                'method': param.get('method'),
                'department': param.get('department') or test_meta.get('department'),
                'sub_title': param.get('sub_title', ''),
                'value_option': param.get('value_option', [])
            }

        matching = [p for p in params_list if isinstance(p, dict) and p.get('test_code') == test_code]
        if matching:
            param = matching[0]
            return {
                'parameter_name': param.get('test_name'),
                'unit': param.get('unit'),
                'reference_range': resolve_reference_range(param, gender),
                'method': param.get('method'),
                'department': param.get('department') or test_meta.get('department'),
                'sub_title': param.get('sub_title', ''),
                'value_option': param.get('value_option', [])
            }
        return None

    patient_data = []

    for patient in patients_list:
        barcode_val = str(get_val(patient, 'barcode'))

        try:
            raw_td = get_val(patient, 'testdetails')
            test_details = json.loads(raw_td) if isinstance(raw_td, str) else raw_td
        except (json.JSONDecodeError, TypeError):
            test_details = []

        patient_name = "N/A"
        patient_age = "N/A"
        current_patient_id = None
        is_emergency = False
        patient_history = ""
        age_type = ""
        gender = ""
        phone = ""
        ref_doctor = ""

        if barcode_val in hms_barcodes_dict:
            hms_info = hms_barcodes_dict[barcode_val]
            current_patient_id = hms_info['patient_id']
            patient_name = hms_info['name']
            patient_age = hms_info['age']
            age_type = hms_info.get('age_type', '')
            gender = hms_info.get('gender', '')
            phone = hms_info.get('phone', '')
            ref_doctor = hms_info.get('ref_doctor', '')

        if barcode_val in barcode_details_dict:
            bc_info = barcode_details_dict[barcode_val]
            if not current_patient_id:
                current_patient_id = bc_info['patient_id']
            if patient_name == "N/A":
                patient_name = bc_info['name']
                patient_age = bc_info['age']
            if not gender and bc_info.get('gender'):
                gender = bc_info['gender']
            if not phone and bc_info.get('phone'):
                phone = bc_info['phone']
            if not ref_doctor and bc_info.get('ref_doctor'):
                ref_doctor = bc_info['ref_doctor']
            is_emergency = bc_info.get('emergency', False)
            patient_history = bc_info.get('patient_history', '')

        if not current_patient_id:
            current_patient_id = get_val(patient, 'patient_id')

        if patient_id_filter and str(current_patient_id) != str(patient_id_filter):
            continue

        enriched_test_details = []
        for test in test_details:
            test_id = test.get('test_id')
            test_meta = test_metadata_dict.get(test_id, {})

            # ONLY PATHOLOGY TESTS
            if not test_meta.get('is_pathology'):
                continue

            test_code = test.get('test_code')
            device_id = test.get('device_id')
            parameters = test.get('parameters', [])

            if parameters and isinstance(parameters, list) and len(parameters) > 0:
                enriched_parameters = []
                for param_index, param in enumerate(parameters):
                    param_test_code = param.get('test_code')
                    param_details = get_parameter_details(test_id, param_test_code, device_id, param_index=param_index, gender=gender)
                    if param_details:
                        enriched_param = {
                            'test_code': param_test_code,
                            'parameter_name': param_details.get('parameter_name'),
                            'value': param.get('value'),
                            'unit': param_details.get('unit', 'N/A'),
                            'reference_range': param_details.get('reference_range', 'N/A'),
                            'method': param_details.get('method', 'N/A'),
                            'department': param_details.get('department', 'N/A'),
                            'sub_title': param_details.get('sub_title', ''),
                            'value_option': param_details.get('value_option', []),
                            'comment': param.get('comment', ''),
                            'status': param.get('status'),
                        }
                    else:
                        enriched_param = {
                            'test_code': param_test_code,
                            'parameter_name': None,
                            'value': param.get('value'),
                            'unit': 'N/A',
                            'reference_range': 'N/A',
                            'method': 'N/A',
                            'department': 'N/A',
                            'sub_title': '',
                            'value_option': [],
                            'comment': param.get('comment', ''),
                            'status': param.get('status'),
                        }
                    enriched_parameters.append(enriched_param)

                enriched_test = {
                    **test,
                    'test_name': test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type': test.get('specimen_type') or test_meta.get('specimen_type', 'N/A'),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department': test_meta.get('department', test.get('department', 'N/A')),
                    'interpretation': test_meta.get('interpretation', test.get('interpretation', '')),
                    'critical_range': test_meta.get('critical_range', test.get('critical_range', '')),
                    'lod': test_meta.get('lod', test.get('lod', '')),
                    'parameters': enriched_parameters,
                    'is_pathology': True,
                    'lab_id': test.get('lab_id') or get_val(patient, 'lab_id') or '',
                }
            else:
                enriched_test = {
                    **test,
                    'test_name': test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type': test.get('specimen_type') or test_meta.get('specimen_type', 'N/A'),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department': test_meta.get('department', test.get('department', 'N/A')),
                    'unit': test_meta.get('unit', test.get('unit', 'N/A')),
                    'method': test_meta.get('method', test.get('method', 'N/A')),
                    'reference_range': test_meta.get('reference_range', test.get('reference_range', 'N/A')),
                    'interpretation': test_meta.get('interpretation', test.get('interpretation', '')),
                    'critical_range': test_meta.get('critical_range', test.get('critical_range', '')),
                    'lod': test_meta.get('lod', test.get('lod', '')),
                    'is_pathology': True,
                    'lab_id': test.get('lab_id') or get_val(patient, 'lab_id') or '',
                }
            enriched_test_details.append(enriched_test)

        if not enriched_test_details:
            continue

        # In split mode (authorization list), filter for doctor approval pending
        if scope == 'split':
            filtered_tests = [
                t for t in enriched_test_details
                if not t.get('approve', False) and not t.get('rerun', False)
            ]
            if not filtered_tests:
                continue
        else:
            filtered_tests = enriched_test_details

        loc_val = get_val(patient, 'locationId') or get_val(patient, 'location_id') or 'Shanmuga Reference Lab'

        item = {
            "patient_id": current_patient_id,
            "patientname": patient_name,
            "age": patient_age,
            "age_type": age_type,
            "gender": gender,
            "phone": phone,
            "ref_doctor": ref_doctor,
            "locationId": loc_val,
            "barcode": barcode_val,
            "lab_id": get_val(patient, 'lab_id') or (filtered_tests[0].get('lab_id') if filtered_tests else '') or '',
            "date": get_val(patient, 'date'),
            "created_date": get_val(patient, 'created_date'),
            "is_emergency": is_emergency,
            "patient_history": patient_history,
            "testdetails": filtered_tests,
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


@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def pathology_save_test_value(request):
    """
    Saves Pathology test values into the separate collection: core_pathologytestvalue
    """
    payload = request.data
    employee_id = payload.get('auth-user-id') or payload.get('created_by')
    try:
        test_details_json = payload.get("testdetails", [])
        barcode = payload.get("barcode")
        locationId = payload.get("locationId")
        processed_records = payload.get("processed_records", [])

        if not isinstance(test_details_json, list) or not test_details_json:
            return Response({"error": "Invalid test details format"}, status=status.HTTP_400_BAD_REQUEST)

        for test in test_details_json:
            if not test.get('test_id'):
                return Response({"error": "Missing test_id in test details"}, status=status.HTTP_400_BAD_REQUEST)

        client = get_pathology_mongo_client()
        db = client.Diagnostics
        pathology_col = db.core_pathologytestvalue

        # Check existing records in core_pathologytestvalue
        existing_records = list(pathology_col.find({"barcode": barcode}))
        blocked_tests = []

        for incoming_test in test_details_json:
            incoming_test_id = incoming_test.get('test_id')
            for ex in existing_records:
                ex_td = ex.get('testdetails', [])
                if isinstance(ex_td, str):
                    try:
                        ex_td = json.loads(ex_td)
                    except Exception:
                        ex_td = []
                if not isinstance(ex_td, list):
                    continue
                for existing_test in ex_td:
                    if str(existing_test.get('test_id')) != str(incoming_test_id):
                        continue
                    approve = existing_test.get('approve', False)
                    rerun = existing_test.get('rerun', False)
                    if approve is True:
                        blocked_tests.append({'test_id': incoming_test_id, 'reason': 'already approved'})
                        break

        if blocked_tests:
            reasons = "; ".join(f"test_id {b['test_id']}: {b['reason']}" for b in blocked_tests)
            return Response({"error": f"Save blocked for the following tests — {reasons}", "blocked_tests": blocked_tests}, status=status.HTTP_409_CONFLICT)

        doc_date = payload.get('date')
        if isinstance(doc_date, str):
            try:
                doc_date = datetime.strptime(doc_date[:10], '%Y-%m-%d')
            except Exception:
                pass

        lab_id = (payload.get('lab_id') or payload.get('labId') or payload.get('labID') or '').strip()
        if not lab_id:
            return Response({"error": "Lab ID is mandatory. Please enter a valid Lab ID."}, status=status.HTTP_400_BAD_REQUEST)

        if isinstance(test_details_json, list):
            for test_item in test_details_json:
                test_item['lab_id'] = lab_id
                test_item['device_id'] = "" 

        serialized_td = json.dumps(test_details_json) if isinstance(test_details_json, list) else test_details_json

        if existing_records:
            existing_id = existing_records[0].get('_id')
            pathology_col.update_one(
                {'_id': existing_id},
                {'$set': {
                    'lastmodified_by': employee_id,
                    'lastmodified_date': datetime.now(),
                    'date': doc_date or existing_records[0].get('date') or datetime.now(),
                    'locationId': locationId or existing_records[0].get('locationId'),
                    'lab_id': lab_id or existing_records[0].get('lab_id'),
                    'testdetails': serialized_td
                }}
            )
            message = "Pathology test details updated successfully."
            status_code = status.HTTP_200_OK
        else:
            new_record = {
                '_id': str(ObjectId()),
                'created_by': employee_id,
                'created_date': datetime.now(),
                'lastmodified_by': None,
                'lastmodified_date': None,
                'date': doc_date or datetime.now(),
                'barcode': barcode,
                'locationId': locationId,
                'lab_id': lab_id,
                'testdetails': serialized_td
            }
            pathology_col.insert_one(new_record)
            message = "Pathology test details saved successfully."
            status_code = status.HTTP_201_CREATED

        # Remove from core_testvalue if barcode was previously saved there
        db.core_testvalue.delete_many({'barcode': barcode})

        return Response({
            "message": message
        }, status=status_code)

    except Exception as e:
        return Response({"error": f"An error occurred: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def pathology_compare_test_details(request):
    """
    Dedicated endpoint for Pathology test details and existing typed reports.
    """
    barcode = request.GET.get('barcode')
    test_id = request.GET.get('test_id')
    test_name_filter = request.GET.get('test_name')
    gender = request.GET.get('gender', '')

    if not barcode:
        return JsonResponse({'error': 'Barcode parameter is required'}, status=400)

    try:
        client = get_pathology_mongo_client()
        db = client.Diagnostics
        core_testdetails_col = db.core_testdetails
        pathology_col = db.core_pathologytestvalue

        # Check existing saved record in core_pathologytestvalue
        existing_rec = pathology_col.find_one({"barcode": barcode})
        existing_tests_map = {}
        saved_lab_id = ""
        if existing_rec:
            saved_lab_id = existing_rec.get("lab_id") or ""
            ex_td = existing_rec.get("testdetails", [])
            if isinstance(ex_td, str):
                try:
                    ex_td = json.loads(ex_td)
                except Exception:
                    ex_td = []
            if isinstance(ex_td, list):
                for t in ex_td:
                    tid = str(t.get("test_id") or "")
                    tname = t.get("test_name") or t.get("testname") or ""
                    if not saved_lab_id and t.get("lab_id"):
                        saved_lab_id = t.get("lab_id")
                    if tid:
                        existing_tests_map[tid] = t
                    if tname:
                        existing_tests_map[tname.strip().upper()] = t

        # Query core_testdetails
        query = {}
        if test_id:
            try:
                query["test_id"] = int(test_id)
            except (ValueError, TypeError):
                pass
        elif test_name_filter:
            query["test_name"] = test_name_filter

        test_docs = list(core_testdetails_col.find(query)) if query else []
        if not test_docs and not query:
            test_docs = list(core_testdetails_col.find())

        final_test_data = []
        for td in test_docs:
            tid = td.get("test_id")
            tname = td.get("test_name")
            saved_match = existing_tests_map.get(str(tid)) or existing_tests_map.get((tname or "").strip().upper()) or {}
            saved_val = saved_match.get("value") or saved_match.get("report") or saved_match.get("test_value") or ""
            saved_remarks = saved_match.get("remarks") or ""
            saved_comment = saved_match.get("comment") or ""
            t_lab_id = saved_match.get("lab_id") or saved_lab_id

            params_def = td.get("parameters") or []
            saved_params = saved_match.get("parameters") or []
            saved_param_values = {}
            saved_diff_items = saved_match.get("differential_items") or []

            for sp in saved_params:
                sp_name = sp.get("test_name") or sp.get("name") or sp.get("parameter_name") or ""
                if sp_name:
                    saved_param_values[sp_name.strip()] = sp.get("value") or ""
                    saved_param_values[sp_name.strip().upper()] = sp.get("value") or ""
                if not saved_diff_items and sp.get("differential_items"):
                    saved_diff_items = sp.get("differential_items")

            final_test_data.append({
                "barcode": barcode,
                "test_id": tid,
                "testname": tname,
                "test_code": td.get("test_code") or "",
                "department": td.get("department") or "Pathology",
                "specimen_type": td.get("specimen_type") or "",
                "NABL": td.get("NABL", False),
                "parameters": params_def,
                "saved_param_values": saved_param_values,
                "differential_items": saved_diff_items,
                "test_value": saved_val,
                "value": saved_val,
                "report": saved_val,
                "remarks": saved_remarks,
                "comment": saved_comment,
                "lab_id": t_lab_id,
                "device_id": ""
            })

        patient_info = {}
        try:
            from ..models import SampleStatus, Hmssamplestatus
            sample = SampleStatus.objects.filter(barcode=barcode).first()
            if sample:
                patient_info = {
                    "patient_name": sample.patientname,
                    "age": sample.age,
                    "gender": sample.gender,
                    "lab_id": saved_lab_id or getattr(sample, "lab_id", ""),
                }
            else:
                hms_sample = Hmssamplestatus.objects.filter(barcode=barcode).first()
                if hms_sample:
                    patient_info = {
                        "patient_name": hms_sample.patientname,
                        "age": hms_sample.age,
                        "gender": hms_sample.gender,
                        "lab_id": saved_lab_id,
                    }
        except Exception:
            pass

        client.close()

        return Response({
            "success": True,
            "barcode": barcode,
            "lab_id": saved_lab_id,
            "patient_info": patient_info,
            "data": final_test_data,
            "processed_records": []
        }, status=200)

    except Exception as e:
        return Response({"error": f"An error occurred: {str(e)}"}, status=500)


@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def pathology_approve_test_detail(request, barcode):
    client = get_pathology_mongo_client()
    db = client.Diagnostics
    collection = db.core_pathologytestvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode") or barcode
        created_date_str = update_data.get("created_date")
        test_id = update_data.get("test_id")
        approve_time = update_data.get("approve_time")
        status_val = update_data.get("status")

        if not (barcode and test_id):
            return JsonResponse({
                "error": "barcode and test_id required"
            }, status=400)
    except Exception as e:
        return JsonResponse({"error": f"Invalid request format: {str(e)}"}, status=400)

    query = {"barcode": barcode}
    if created_date_str:
        try:
            created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
            query["created_date"] = created_date
        except Exception:
            pass

    test_value = collection.find_one(query, sort=[("created_date", -1)])
    if not test_value and "created_date" in query:
        query.pop("created_date", None)
        test_value = collection.find_one(query, sort=[("created_date", -1)])

    if not test_value:
        return JsonResponse({
            "error": "Patient record not found in pathology test values for given barcode."
        }, status=404)

    raw_td = test_value.get("testdetails", "[]")
    if isinstance(raw_td, str):
        try:
            test_details = json.loads(raw_td)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Failed to decode test details."}, status=500)
    elif isinstance(raw_td, list):
        test_details = raw_td
    else:
        test_details = []

    test_found = False
    for test_detail in test_details:
        if str(test_detail.get("test_id")) == str(test_id):
            if test_detail.get("rerun"):
                return JsonResponse({"error": "Already reruned."}, status=400)
            if test_detail.get("approve"):
                return JsonResponse({"error": "Already approved."}, status=400)

            test_detail["approve"] = update_data.get("approve", False)
            if test_detail["approve"]:
                if approve_time:
                    test_detail["approve_time"] = approve_time
                if "approve_by" in update_data:
                    test_detail["approve_by"] = update_data["approve_by"]
                if status_val in ("Normal", "Abnormal"):
                    test_detail["status"] = status_val
            test_found = True
            break

    if not test_found:
        return JsonResponse({"error": "Test not found with given test_id."}, status=404)

    result = collection.update_one(
        {"_id": test_value["_id"]},
        {"$set": {"testdetails": json.dumps(test_details) if isinstance(raw_td, str) else test_details}}
    )
    if result.modified_count > 0 or result.matched_count > 0:
        return JsonResponse({"message": "Test detail approved successfully."})
    return JsonResponse({"error": "Failed to update test detail."}, status=500)


@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def pathology_rerun_test_detail(request, barcode):
    client = get_pathology_mongo_client()
    db = client.Diagnostics
    collection = db.core_pathologytestvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode") or barcode
        created_date_str = update_data.get("created_date")
        test_id = update_data.get("test_id")
        rerun_time = update_data.get("rerun_time")
        if not (barcode and test_id):
            return JsonResponse({
                "error": "barcode and test_id required"
            }, status=400)
    except Exception as e:
        return JsonResponse({"error": f"Invalid request format: {str(e)}"}, status=400)

    query = {"barcode": barcode}
    if created_date_str:
        try:
            created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
            query["created_date"] = created_date
        except Exception:
            pass

    test_value = collection.find_one(query, sort=[("created_date", -1)])
    if not test_value and "created_date" in query:
        query.pop("created_date", None)
        test_value = collection.find_one(query, sort=[("created_date", -1)])

    if not test_value:
        return JsonResponse({
            "error": "Patient record not found in pathology test values for given barcode."
        }, status=404)

    raw_td = test_value.get("testdetails", "[]")
    if isinstance(raw_td, str):
        try:
            test_details = json.loads(raw_td)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Failed to decode test details."}, status=500)
    elif isinstance(raw_td, list):
        test_details = raw_td
    else:
        test_details = []

    test_found = False
    for test_detail in test_details:
        if str(test_detail.get("test_id")) == str(test_id):
            if test_detail.get("approve"):
                return JsonResponse({"error": "Already approved unable to rerun the approved value."}, status=400)
            if test_detail.get("rerun"):
                return JsonResponse({"error": "Already reruned."}, status=400)

            test_detail["rerun"] = update_data.get("rerun", False)
            if test_detail["rerun"]:
                if rerun_time:
                    test_detail["rerun_time"] = rerun_time
                if "rerun_by" in update_data:
                    test_detail["rerun_by"] = update_data["rerun_by"]
            test_found = True
            break

    if not test_found:
        return JsonResponse({"error": "Test not found with given test_id."}, status=404)

    result = collection.update_one(
        {"_id": test_value["_id"]},
        {"$set": {"testdetails": json.dumps(test_details) if isinstance(raw_td, str) else test_details}}
    )
    if result.modified_count > 0 or result.matched_count > 0:
        return JsonResponse({"message": "Test detail rerun Initiated."})
    return JsonResponse({"error": "Failed to update test detail."}, status=500)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def pathology_patient_test_sorting(request):
    try:
        barcode = request.GET.get('barcode')
        date_param = request.GET.get('date', datetime.now().strftime("%Y-%m-%d"))

        if not barcode:
            return JsonResponse({'error': 'Missing barcode'}, status=400)

        try:
            if '-' in str(date_param):
                parts = str(date_param).split('-')
                if len(parts) == 3 and len(parts[0]) == 4:
                    formatted_date = datetime.strptime(date_param, "%Y-%m-%d").date()
                elif len(parts) == 3 and len(parts[2]) == 4:
                    formatted_date = datetime.strptime(date_param, "%d-%m-%Y").date()
                else:
                    formatted_date = datetime.strptime(date_param, "%Y-%m-%d").date()
            else:
                formatted_date = datetime.strptime(date_param, "%Y-%m-%d").date()
        except ValueError:
            formatted_date = None

        client = get_pathology_mongo_client()
        db = client.Diagnostics
        core_testdetails_collection = db["core_testdetails"]
        pathology_col = db.core_pathologytestvalue

        query = {'barcode': barcode}
        if formatted_date:
            dt_start = datetime.combine(formatted_date, datetime.min.time())
            dt_end = datetime.combine(formatted_date, datetime.max.time())
            query['date'] = {'$gte': dt_start, '$lte': dt_end}

        tests = list(pathology_col.find(query).sort('created_date', -1))
        if not tests and 'date' in query:
            tests = list(pathology_col.find({'barcode': barcode}).sort('created_date', -1))

        test_list = []
        for test in tests:
            test_created_date = test.get("created_date")
            testdetails_data = test.get("testdetails", [])
            if isinstance(testdetails_data, str):
                try:
                    testdetails_list = json.loads(testdetails_data)
                except json.JSONDecodeError:
                    continue
            elif isinstance(testdetails_data, list):
                testdetails_list = testdetails_data
            else:
                continue

            for test_item in testdetails_list:
                if test_item.get('approve') is True:
                    test_id = test_item.get('test_id')
                    test_code = test_item.get('test_code')

                    if test_id:
                        q = {"test_id": test_id}
                        if test_code:
                            q["test_code"] = test_code
                        core_test = core_testdetails_collection.find_one(
                            q,
                            {"test_code": 1, "test_name": 1, "NABL": 1, "department": 1, "parameters": 1, "order_id": 1, "_id": 0}
                        )
                        if not core_test:
                            core_test = core_testdetails_collection.find_one(
                                {"test_id": test_id},
                                {"test_code": 1, "test_name": 1, "NABL": 1, "department": 1, "parameters": 1, "order_id": 1, "_id": 0}
                            )

                        if core_test:
                            test_item['test_code'] = core_test.get('test_code', 'N/A')
                            test_item['test_name'] = core_test.get('test_name', test_item.get('test_name', 'N/A'))
                            test_item['NABL'] = core_test.get('NABL', 'N/A')
                            test_item['department'] = core_test.get('department', 'N/A')
                            test_item['order_id'] = core_test.get('order_id', '')

                            params = test_item.get('parameters', [])
                            core_params = core_test.get('parameters', [])
                            if params and core_params:
                                params_list = []
                                if isinstance(core_params, str):
                                    try:
                                        params_list = json.loads(core_params)
                                    except Exception:
                                        params_list = []
                                elif isinstance(core_params, list):
                                    params_list = core_params

                                if isinstance(params_list, list):
                                    enriched_params = []
                                    for idx, p in enumerate(params):
                                        if isinstance(p, dict):
                                            p_code = p.get('test_code')
                                            match = None
                                            if p_code:
                                                matching = [m for m in params_list if isinstance(m, dict) and m.get('test_code') == p_code]
                                                if matching:
                                                    match = matching[0]
                                            if not match and 0 <= idx < len(params_list):
                                                match = params_list[idx]
                                            p_name = (match.get('test_name') or match.get('name')) if match else None
                                            p_copy = dict(p)
                                            p_copy['name'] = p_name or p_code or f"Parameter {idx + 1}"
                                            enriched_params.append(p_copy)
                                        else:
                                            enriched_params.append(p)
                                    test_item['parameters'] = enriched_params
                    else:
                        test_item['test_code'] = test_item.get('test_code', 'N/A')
                        test_item['test_name'] = test_item.get('test_name', 'N/A')
                        test_item['NABL'] = test_item.get('NABL', 'N/A')
                        test_item['department'] = test_item.get('department', 'N/A')

                    test_item['record_id'] = str(test.get('_id'))
                    test_item['created_date'] = test_created_date.isoformat() if isinstance(test_created_date, datetime) else str(test_created_date or '')
                    test_list.append(test_item)

        if test_list:
            return JsonResponse({barcode: {"testdetails": test_list}})
        return JsonResponse({'error': 'No records found for this barcode'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def pathology_get_patient_test_details(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)

    try:
        barcode_details = next(iter(BarcodeTestDetails.objects.filter(barcode=barcode)), None)
        if not barcode_details:
            return JsonResponse({'error': 'No barcode details found for the given barcode'}, status=404)

        patient_id = barcode_details.patient_id
        bill_no = barcode_details.bill_no

        client = get_pathology_mongo_client()
        db = client.Diagnostics
        pathology_col = db.core_pathologytestvalue

        test_values = list(pathology_col.find({'barcode': barcode}).sort('created_date', -1))
        if not test_values:
            return JsonResponse({'error': 'No test records found for the given barcode'}, status=404)

        patient = next(iter(Patient.objects.filter(patient_id=patient_id)), None) if patient_id else None
        billing = next(iter(Billing.objects.filter(bill_no=bill_no)), None) if bill_no else None
        sample_status = SampleStatus.objects.filter(patient_id=patient_id)
        sample_status_list = []
        if sample_status.exists():
            for s in sample_status:
                s_details = s.testdetails
                if isinstance(s_details, str):
                    try:
                        s_details = json.loads(s_details.strip('"'))
                    except Exception:
                        s_details = []
                if isinstance(s_details, list):
                    sample_status_list.extend(s_details)

        barcodes = []
        try:
            tests = json.loads(barcode_details.testdetails) if isinstance(barcode_details.testdetails, str) else barcode_details.testdetails
            barcodes = [test.get("barcode") for test in tests if test.get("barcode")]
        except Exception:
            barcodes = []

        patient_gender = (patient.gender or '') if patient else ''
        core_testdetails_collection = db.core_testdetails

        global_db = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs = gridfs.GridFS(global_db)

        def resolve_gender_fields(meta, gender):
            gender_key = (gender or '').strip().lower()
            if gender_key == 'male' and meta.get('male'):
                return meta['male'], meta.get('low', ''), meta.get('high', '')
            elif gender_key == 'female' and meta.get('female'):
                return meta['female'], meta.get('low', ''), meta.get('high', '')
            return meta.get('reference_range', '') or '', meta.get('low', '') or '', meta.get('high', '') or ''

        def get_employee_signature_data(employee_id):
            if not employee_id:
                return None
            try:
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None
                employee_name = profile.get("employeeName", "")
                designation = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                signature_base64 = None
                if signature_file_id:
                    try:
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        signature_file = fs.get(signature_file_id)
                        signature_bytes = signature_file.read()
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception:
                        pass
                return {
                    "employeeName": employee_name,
                    "designation": designation,
                    "signatureBase64": signature_base64,
                }
            except Exception:
                return None

        all_results = []
        all_approvers = set()

        for test_value_record in test_values:
            raw_td = test_value_record.get('testdetails', [])
            if isinstance(raw_td, str):
                try:
                    test_details = json.loads(raw_td)
                except Exception:
                    test_details = []
            elif isinstance(raw_td, list):
                test_details = raw_td
            else:
                test_details = []

            approved_tests = []
            for test in test_details:
                if test.get('approve') is True:
                    approver = test.get('approve_by')
                    if approver:
                        all_approvers.add(approver)

                    test_id = test.get('test_id')
                    test_code = test.get('test_code')
                    q = {"test_id": test_id}
                    if test_code:
                        q["test_code"] = test_code
                    meta = core_testdetails_collection.find_one(q) or core_testdetails_collection.find_one({"test_id": test_id})

                    if meta:
                        test['test_name'] = meta.get('test_name', test.get('test_name', 'N/A'))
                        test['department'] = meta.get('department', 'N/A')
                        test['method'] = meta.get('method', 'N/A')
                        test['unit'] = meta.get('unit', 'N/A')
                        test['interpretation'] = meta.get('interpretation', '')
                        test['NABL'] = meta.get('NABL', False)
                        test['order_id'] = meta.get('order_id', '')
                        test['specimen_type'] = meta.get('specimen_type', 'N/A')
                        ref_range, _, _ = resolve_gender_fields(meta, patient_gender)
                        test['reference_range'] = ref_range

                        params = test.get('parameters', [])
                        core_params = meta.get('parameters', [])
                        if params and core_params:
                            c_params_list = []
                            if isinstance(core_params, str):
                                try:
                                    c_params_list = json.loads(core_params)
                                except Exception:
                                    c_params_list = []
                            elif isinstance(core_params, list):
                                c_params_list = core_params

                            enriched_p = []
                            for idx, p in enumerate(params):
                                if isinstance(p, dict):
                                    p_code = p.get('test_code')
                                    m_param = None
                                    if p_code:
                                        m_param = next((cp for cp in c_params_list if isinstance(cp, dict) and cp.get('test_code') == p_code), None)
                                    if not m_param and 0 <= idx < len(c_params_list):
                                        m_param = c_params_list[idx]

                                    p_copy = dict(p)
                                    if m_param:
                                        p_copy['name'] = m_param.get('test_name') or m_param.get('name') or p_code or f"Param {idx+1}"
                                        p_copy['unit'] = m_param.get('unit', '')
                                        p_copy['method'] = m_param.get('method', '')
                                        p_copy['sub_title'] = m_param.get('sub_title', '')
                                        p_ref, _, _ = resolve_gender_fields(m_param, patient_gender)
                                        p_copy['reference_range'] = p_ref
                                    enriched_p.append(p_copy)
                                test['parameters'] = enriched_p
                    test['record_id'] = str(test_value_record.get('_id'))
                    test['lab_id'] = test.get('lab_id') or test_value_record.get('lab_id') or ''
                    status_match = next((s for s in sample_status_list if isinstance(s, dict) and (str(s.get("test_id")) == str(test_id) or s.get("testname") == test.get("test_name"))), {})
                    test['samplecollected_time'] = status_match.get("samplecollected_time") or test.get("samplecollected_time") or ""
                    test['received_time'] = status_match.get("received_time") or test.get("received_time") or ""
                    test['dispatch_time'] = test.get("dispatch_time") or ""
                    approved_tests.append(test)

            if approved_tests:
                order_id_val = None
                if bill_no:
                    try:
                        b_doc = db.core_billing.find_one({"bill_no": bill_no}, {"order_id": 1})
                        if b_doc and b_doc.get("order_id"):
                            order_id_val = b_doc.get("order_id")
                    except Exception:
                        pass
                if not order_id_val:
                    order_id_val = bill_no

                patient_details = {
                    "patient_id": patient_id,
                    "patientname": patient.patientname if patient else "N/A",
                    "age": patient.age if patient else "N/A",
                    "age_type": patient.age_type if patient else "Years",
                    "gender": patient.gender if patient else "N/A",
                    "date": test_value_record.get('date'),
                    "barcode": test_value_record.get('barcode'),
                    "lab_id": test_value_record.get('lab_id') or (test_details[0].get('lab_id') if test_details else '') or '',
                    "bill_no": bill_no,
                    "order_id": order_id_val,
                    "barcodes": barcodes,
                    "testdetails": approved_tests,
                    "sample_status": sample_status_list,
                    "refby": billing.refby if billing else "N/A",
                    "B2B": billing.B2B if billing else False,
                    "branch": billing.branch if billing else "N/A",
                }
                all_results.append(patient_details)

        if not all_results:
            return JsonResponse({'error': 'No approved test records found'}, status=404)

        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)

        response_data = {
            "patient_data": all_results[0] if len(all_results) == 1 else all_results,
            "signatures": signatures_data,
        }
        return JsonResponse(response_data, safe=False)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)


@api_view(['PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def pathology_update_dispatch_status(request, barcode):
    try:
        client = get_pathology_mongo_client()
        db = client.Diagnostics
        collection = db.core_pathologytestvalue

        dispatch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        test_id = request.data.get("test_id")
        created_date_str = request.data.get("created_date")

        query = {"barcode": barcode}
        if created_date_str:
            try:
                created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
                query["created_date"] = created_date
            except Exception:
                pass

        doc = collection.find_one(query, sort=[("created_date", -1)])
        if not doc and "created_date" in query:
            query.pop("created_date", None)
            doc = collection.find_one(query, sort=[("created_date", -1)])

        if not doc:
            return JsonResponse({"error": "Record not found"}, status=404)

        raw_td = doc.get("testdetails", "[]")
        testdetails = json.loads(raw_td) if isinstance(raw_td, str) else raw_td
        for item in testdetails:
            if test_id:
                if str(item.get("test_id")) == str(test_id):
                    item["dispatch"] = True
                    item["dispatch_time"] = dispatch_time
            else:
                if item.get("approve"):
                    item["dispatch"] = True
                    item["dispatch_time"] = dispatch_time

        collection.update_one(
            {"_id": doc["_id"]},
            {"$set": {"testdetails": json.dumps(testdetails) if isinstance(raw_td, str) else testdetails}}
        )

        return JsonResponse({"message": "Pathology dispatch status updated successfully."})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)



