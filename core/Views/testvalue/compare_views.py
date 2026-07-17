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
    client = get_client()
    db = client.Diagnostics
    core_testdetails_collection = db.core_testdetails
    interface_testvalue_collection = db.interface_testvalue

    franchise_client = get_client()
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
            logger.error(f"Franchise MongoDB connection error: {str(franchise_error)}")

        if regular_test_list:
            regular_test_data = process_test_data(
                regular_test_list, regular_sample_status_map,
                barcode, device_id, core_testdetails_collection,
                interface_testvalue_collection, 'regular', test_id_filter,
                gender=gender,
            )
            final_test_data.extend(regular_test_data['test_data'])
            processed_records.extend(regular_test_data['processed_records'])

    # Note: `client`/`franchise_client` are the shared, pooled MongoClient — do not close them here.

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

    logger.debug(f"DEBUG [{data_source_type}]: Pre-fetched {len(all_interface_records)} interface records "
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

    logger.debug(f"DEBUG [{data_source_type}]: interface_map keys={list(interface_map.keys())}")
    # ─────────────────────────────────────────────────────────────────────────

    for test_item in test_list:
        test_id = test_item.get("test_id")

        if test_id_filter and str(test_id) != str(test_id_filter):
            continue

        test_detail_doc = core_testdetails_collection.find_one({"test_id": test_id})
        if not test_detail_doc:
            logger.debug(f"DEBUG [{data_source_type}]: No core_testdetails for test_id={test_id}, skipping.")
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

        logger.debug(f"DEBUG [{data_source_type}]: test_id={test_id}, test_name={test_name}, "
              f"core_suffix={core_suffix}, lookup_barcode={lookup_barcode}")
        # ─────────────────────────────────────────────────────────────────

        sample_status_info = sample_status_map.get(str(test_id), {"status": "Unknown", "source": "none"})
        sample_status      = sample_status_info["status"]
        data_source        = sample_status_info["source"]

        logger.debug(f"DEBUG [{data_source_type}]: sample_status={sample_status}, data_source={data_source}")

        if sample_status in ("Rejected", "Cancelled"):
            logger.debug(f"DEBUG [{data_source_type}]: Skipping test_id={test_id}, status={sample_status}")
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

                logger.debug(f"DEBUG [{data_source_type}]: Looking up interface_map key={interface_key}, "
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

            logger.debug(f"DEBUG [{data_source_type}]: Found {len(all_barcode_records)} records "
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
