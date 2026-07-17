from rest_framework.response import Response
from django.http import JsonResponse , HttpResponse
from datetime import datetime
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from urllib.parse import quote_plus
from core.mongo_client import get_client
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
from ...models import Patient,Hmssamplestatus
from ...models import SampleStatus
from ...models import TestValue
from ...models import SampleStatus
from ...models import BarcodeTestDetails
from django.http import JsonResponse
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ...models import SampleStatus, TestValue, Hmssamplestatus, Hmsbarcode, HmspatientBilling
from ...serializers import SampleStatusSerializer
from ...serializers import TestValueSerializer
import os
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv
from core.pagination import paginate_queryset
load_dotenv()

logger = logging.getLogger(__name__)


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
        client = get_client()
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
                        "collection_container": 1
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
            return test

        def match_test_values(test, barcode, test_values_by_barcode):
                """Optimized test value matching with pre-fetched data using test_id"""
                test_id = test.get('test_id')

                # Initialize default values
                test.update({
                    'rerun': False,
                    'approve': False,
                    'test_value_exists': False,
                    'approve_time': None,
                    'rerun_time': None,
                    'approve_by': None,
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
                                'approve_time': tv_test.get('approve_time'),
                                'rerun_time': tv_test.get('rerun_time'),
                                'approve_by': tv_test.get('approve_by'),
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

                ALLOWED_DEPARTMENTS = ["Haematology","Coagulation", "Biochemistry", "Immunology", "Immunoassay", "Serology", "Clinical Pathology","Molecular Biology"]
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

                ALLOWED_DEPARTMENTS = ["Haematology","Coagulation", "Biochemistry", "Immunology", "Immunoassay", "Serology", "Clinical Pathology","Molecular Biology"]
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

                    ALLOWED_DEPARTMENTS = ["Haematology","Coagulation", "Biochemistry", "Immunology", "Immunoassay", "Serology", "Clinical Pathology","Molecular Biology"]
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
                logger.error(f"CHC MongoDB error: {str(e)}")

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

                    ALLOWED_DEPARTMENTS = ["Haematology","Coagulation", "Biochemistry", "Immunology", "Immunoassay", "Serology", "Clinical Pathology","Molecular Biology"]
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
                logger.error(f"MongoDB error: {str(e)}")

        # Note: `client` is the shared, pooled MongoClient — do not close it here.

        # Sort results
        combined_results.sort(key=lambda x: x.get('date', ''), reverse=True)

        # NOTE: response shape changed from a bare JSON array to a
        # paginated object ({"data": [...], total_count, total_pages,
        # current_page}) to bound the payload for wide date ranges;
        # update any frontend caller that expected a raw array here.
        total_count = len(combined_results)
        page_obj, page_meta = paginate_queryset(combined_results, request)
        return Response({
            "data": list(page_obj),
            "total_count": total_count,
            **page_meta
        }, status=status.HTTP_200_OK)

    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
        mongo_client = get_client()
        core_collection = mongo_client.Diagnostics.core_testdetails

        test_info_map = {}  # test_id -> test_name
        if all_test_ids:
            cursor = core_collection.find(
                {"test_id": {"$in": list(all_test_ids)}},
                {"_id": 0, "test_id": 1, "test_name": 1},
            )
            for doc in cursor:
                test_info_map[doc["test_id"]] = doc.get("test_name", "N/A")

        # Note: `mongo_client` is the shared, pooled MongoClient — do not close it here.

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
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(exc)}, status=500)
