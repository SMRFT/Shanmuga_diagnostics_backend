from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from core.mongo_client import get_client
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from core.models import TestValue, MBTestValue
load_dotenv()
logger = logging.getLogger(__name__)
import gridfs
import re
from core.pagination import paginate_queryset
from bson import json_util

# core/Views/franchise/reports.py
#
# Franchise reporting views: the overall (list) report across franchise
# patients, the single-patient/barcode test-detail report, and the
# franchise TestValue lookup by date range. Also carries
# get_department_status_franchise, a helper used only by
# franchise_overall_report.
#
# Split out of the original core/Views/franchise.py — see
# core/Views/franchise/__init__.py for the full re-export list.


def get_department_status_franchise(test_list, barcode, sample_status_map, test_value_map, mb_test_value_map):
    """
    Determine status for each department based on test details
    Returns dict: {department_name: status}
    """
    department_status = {}

    # Group tests by department
    tests_by_dept = {}
    for test in test_list:
        dept = test.get('department', 'N/A')
        if dept and dept != 'N/A':  # Skip N/A departments
            if dept not in tests_by_dept:
                tests_by_dept[dept] = []
            tests_by_dept[dept].append(test)

    # If no valid departments found, return empty dict
    if not tests_by_dept:
        return {}

    # Determine status for each department
    for dept, tests in tests_by_dept.items():
        dept_test_ids = {int(t.get('test_id')) for t in tests if t.get('test_id')}

        # Get test values for this barcode
        all_test_values = []
        if barcode in test_value_map:
            all_test_values.extend(test_value_map[barcode].get('testdetails', []))
        if barcode in mb_test_value_map:
            all_test_values.extend(mb_test_value_map[barcode].get('testdetails', []))

        # Filter test values for this department - match by test_id
        dept_test_values = [
    tv for tv in all_test_values
    if tv.get('test_id') and int(tv.get('test_id')) in dept_test_ids
    and not tv.get('rerun', False)
]

        # Check sample collection status
        sample_tests = sample_status_map.get(barcode, [])
        dept_samples = [st for st in sample_tests
                       if st.get('test_id') in dept_test_ids]

        # Determine department status
        if not dept_samples:
            department_status[dept] = 'Pending'
        else:
            all_collected = all(t.get('samplestatus') == 'Sample Collected' for t in dept_samples)
            all_received = all(t.get('samplestatus') == 'Received' for t in dept_samples)

            if not dept_test_values:
                if all_received:
                    department_status[dept] = 'Received'
                elif all_collected:
                    department_status[dept] = 'Collected'
                else:
                    department_status[dept] = 'Pending'
            else:
                # Check if tests have values
                def has_test_values(test):
                    parameters = test.get("parameters", [])

                    if parameters:
                        return any(
                            param.get("value") is not None and str(param.get("value")).strip() != ""
                            for param in parameters
                        )

                    # Biochemistry
                    if test.get("value") is not None and str(test.get("value")).strip() != "":
                        return True

                    # ✅ Microbiology FIX
                    if test.get("remarks") or test.get("parameter_type"):
                        return True

                    return False

                any_tested = any(has_test_values(tv) for tv in dept_test_values)
                # Collect statuses from all test values
                any_dispatched = any(tv.get('dispatch', False) for tv in dept_test_values)
                any_approved = any(tv.get('approve', False) for tv in dept_test_values)
                any_tested = any(has_test_values(tv) for tv in dept_test_values)

                if any_dispatched:
                    department_status[dept] = 'Dispatched'

                elif any_approved:
                    department_status[dept] = 'Approved'

                elif any_tested:
                    department_status[dept] = 'Tested'

                elif all_received:
                    department_status[dept] = 'Received'

                elif all_collected:
                    department_status[dept] = 'Collected'

                else:
                    department_status[dept] = 'Pending'

    return department_status


@api_view(['GET', 'PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def franchise_overall_report(request):
    try:
        client = get_client()
        db = client.franchise
        patients_collection          = db.franchise_billing
        sample_status_collection     = db.franchise_sample
        franchise_patient_collection = db.franchise_patient

        diagnostics_db          = client.Diagnostics
        test_details_collection = diagnostics_db.core_testdetails

        from_date      = request.GET.get("from_date")
        to_date        = request.GET.get("to_date")
        selected_date  = request.GET.get("selected_date")
        patient_id_req = request.GET.get("patient_id")


        # ── Helpers ───────────────────────────────────────────────────────────

        def parse_tat_format(tat_str):
            """Parse TAT format like '2D 3H 45M' and return total seconds."""
            if not tat_str or tat_str == 'N/A':
                return None
            try:
                total_seconds = 0
                days    = re.search(r'(\d+)D', str(tat_str))
                hours   = re.search(r'(\d+)H', str(tat_str))
                minutes = re.search(r'(\d+)M', str(tat_str))
                if days:    total_seconds += int(days.group(1))    * 86400
                if hours:   total_seconds += int(hours.group(1))   * 3600
                if minutes: total_seconds += int(minutes.group(1)) * 60
                return total_seconds if total_seconds > 0 else None
            except Exception as e:
                logger.error(f"Failed to parse TAT format '{tat_str}': {e}")
                return None

        def parse_datetime_str(dt_str):
            """
            Parse datetime strings including:
            - Z suffix:           2026-01-29T05:39:12.053Z
            - +00:00 suffix:      2026-01-29T05:39:12.053000+00:00
            - microseconds:       2026-01-29T05:39:12.053000
            - plain:              2026-01-29 05:39:12
            Always returns naive datetime (tz stripped) or None.
            """
            if not dt_str or dt_str == 'null':
                return None
            try:
                dt_str = str(dt_str).strip()

                # Replace Z with +00:00 so fromisoformat can handle it on Python < 3.11
                normalized = dt_str.replace('Z', '+00:00')

                try:
                    dt = datetime.fromisoformat(normalized)
                except ValueError:
                    # Fallback for any remaining edge-case formats
                    for fmt in (
                        "%Y-%m-%dT%H:%M:%S.%f",
                        "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d %H:%M:%S.%f",
                        "%Y-%m-%d %H:%M:%S",
                    ):
                        try:
                            dt = datetime.strptime(dt_str, fmt)
                            break
                        except ValueError:
                            continue
                    else:
                        logger.debug(f"parse_datetime_str: could not parse '{dt_str}'")
                        return None

                # Always strip timezone — work in naive UTC throughout
                if dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
                return dt

            except Exception as e:
                logger.error(f"parse_datetime_str error for '{dt_str}': {e}")
                return None

        # ── Date range ────────────────────────────────────────────────────────

        try:
            if selected_date:
                selected_date_parsed = datetime.strptime(selected_date, "%Y-%m-%d")
                from_date = selected_date_parsed
                to_date   = selected_date_parsed + timedelta(days=1)
            elif from_date and to_date:
                from_date = datetime.strptime(from_date, "%Y-%m-%d")
                to_date   = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
            else:
                return JsonResponse(
                    {"error": "Either 'selected_date' or both 'from_date' and 'to_date' are required"},
                    status=400,
                )
        except ValueError:
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        # ── Fetch billing records ─────────────────────────────────────────────

        query = {"created_date": {"$gte": from_date, "$lt": to_date}}
        if patient_id_req:
            query["patient_id"] = patient_id_req

        patients = list(patients_collection.find(query))

        if not patients:
            return JsonResponse([], safe=False)

        patient_ids = [p.get("patient_id") for p in patients if p.get("patient_id")]
        barcodes    = [p.get("barcode")    for p in patients if p.get("barcode")]

        # ── Patient details ───────────────────────────────────────────────────

        patient_details_map = {}
        if patient_ids:
            for pd in franchise_patient_collection.find({"patient_id": {"$in": patient_ids}}):
                patient_details_map[pd.get("patient_id")] = pd

        # ── barcode → patient_id ──────────────────────────────────────────────

        barcode_to_patient_map = {}
        for patient in patients:
            if patient.get("barcode") and patient.get("patient_id"):
                barcode_to_patient_map[patient["barcode"]] = patient["patient_id"]

        # ── Sample status keyed by barcode ────────────────────────────────────

        sample_status_records = list(sample_status_collection.find({"barcode": {"$in": barcodes}}))
        sample_status_map = {}
        for record in sample_status_records:
            if not isinstance(record, dict):
                continue
            rec_barcode = record.get("barcode")
            testdetails = record.get("testdetails")
            if not rec_barcode or not testdetails:
                continue
            parsed = []
            if isinstance(testdetails, str):
                try:
                    parsed = json.loads(testdetails)
                except json.JSONDecodeError:
                    parsed = []
            elif isinstance(testdetails, list):
                parsed = testdetails
            if parsed:
                sample_status_map.setdefault(rec_barcode, []).extend(parsed)

        # ── TestValue records keyed by barcode ────────────────────────────────

        test_value_records = TestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(from_date.date(), to_date.date()),
        ).values("barcode", "testdetails", "created_date")

        test_value_map = {}
        for record in test_value_records:
            if not isinstance(record, dict):
                continue
            rec_barcode  = record.get("barcode")
            created_date = record.get("created_date")
            testdetails  = record.get("testdetails")
            if not rec_barcode:
                continue
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails.strip('"'))
                except json.JSONDecodeError:
                    testdetails = []
            if rec_barcode not in test_value_map:
                test_value_map[rec_barcode] = {"testdetails": [], "created_date": created_date}
            if isinstance(testdetails, list):
                test_value_map[rec_barcode]["testdetails"].extend(testdetails)
            if created_date:
                existing = test_value_map[rec_barcode]["created_date"]
                if not existing or created_date > existing:
                    test_value_map[rec_barcode]["created_date"] = created_date

        # ── MBTestValue records keyed by barcode ──────────────────────────────

        mb_test_value_records = MBTestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(from_date.date(), to_date.date()),
        ).values("barcode", "testdetails", "created_date")

        mb_test_value_map = {}
        for record in mb_test_value_records:
            if not isinstance(record, dict):
                continue
            rec_barcode  = record.get("barcode")
            created_date = record.get("created_date")
            testdetails  = record.get("testdetails")
            if not rec_barcode:
                continue
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails.strip('"'))
                except json.JSONDecodeError:
                    testdetails = []
            if rec_barcode not in mb_test_value_map:
                mb_test_value_map[rec_barcode] = {"testdetails": [], "created_date": created_date}
            if isinstance(testdetails, list):
                mb_test_value_map[rec_barcode]["testdetails"].extend(testdetails)
            if created_date:
                existing = mb_test_value_map[rec_barcode]["created_date"]
                if not existing or created_date > existing:
                    mb_test_value_map[rec_barcode]["created_date"] = created_date

        # ── Build response ────────────────────────────────────────────────────

        formatted_data = []

        for patient in patients:
            if not isinstance(patient, dict):
                continue

            pid            = patient.get("patient_id", "N/A")
            patient_detail = patient_details_map.get(pid, {})
            if not isinstance(patient_detail, dict):
                patient_detail = {}

            # Payment method
            payment_details = {}
            raw = patient.get("paymentMode", "")
            if raw:
                if isinstance(raw, dict):
                    payment_details = raw
                elif isinstance(raw, str):
                    try:
                        cleaned      = raw.strip('"')
                        payment_data = json.loads(cleaned) if cleaned else {}
                        payment_details = payment_data if isinstance(payment_data, dict) else {"paymentmethod": str(payment_data)}
                    except Exception:
                        payment_details = {"paymentmethod": raw}
            else:
                payment_details = {"paymentmethod": "N/A"}

            if payment_details.get("paymentmethod") == "PartialPayment":
                partial_data = patient.get("PartialPayment", "")
                try:
                    if isinstance(partial_data, str):
                        partial_data = json.loads(partial_data.strip('"')) if partial_data.strip('"') else {}
                    if isinstance(partial_data, dict):
                        payment_details.update(partial_data)
                        payment_details["paymentmethod"] = "PartialPayment"
                except Exception:
                    pass

            # Test list with department enrichment
            test_list       = []
            departments_set = set()

            test_field = patient.get("testdetails", [])
            if isinstance(test_field, str):
                try:
                    test_field = json.loads(test_field)
                    if not isinstance(test_field, list):
                        test_field = []
                except Exception:
                    test_field = []
            elif not isinstance(test_field, list):
                test_field = []

            test_ids = [t.get("test_id") for t in test_field if isinstance(t, dict) and t.get("test_id")]

            if test_ids:
                for test_id in test_ids:
                    test_detail_doc = test_details_collection.find_one(
                        {"test_id": test_id},
                        {"_id": 0, "test_id": 1, "test_name": 1, "department": 1},
                    )
                    if test_detail_doc:
                        dept = test_detail_doc.get("department", "N/A")
                        test_list.append({
                            "test_id":    test_id,
                            "testname":   test_detail_doc.get("test_name", "N/A"),
                            "test_name":  test_detail_doc.get("test_name", "N/A"),
                            "department": dept,
                        })
                        if dept and dept != "N/A":
                            departments_set.add(dept)

            if not test_list and test_field:
                test_list = test_field
                for t in test_list:
                    if isinstance(t, dict):
                        if not t.get('testname') and t.get('test_name'):
                            t['testname'] = t['test_name']
                        elif not t.get('test_name') and t.get('testname'):
                            t['test_name'] = t['testname']

            testnames   = ", ".join([
                t.get("test_name", t.get("testname", "")) if isinstance(t, dict) else str(t)
                for t in test_list
            ])
            no_of_tests = len(test_list)
            department  = ", ".join(sorted(departments_set)) if departments_set else "N/A"

            age_value = patient_detail.get("age", "N/A")
            age_type  = patient_detail.get("age_type", "")
            age       = f"{age_value} {age_type}" if age_type else str(age_value)

            try:
                discount = int(float(patient.get('discountPercentage', 0) or 0))
            except Exception:
                discount = 0
            try:
                total_amount = int(float(patient.get("netAmount", 0) or 0))
            except Exception:
                total_amount = 0
            try:
                credit_amount = int(float(patient.get("credit_amount", 0) or 0))
            except Exception:
                credit_amount = 0

            credit_details     = []
            credit_details_raw = patient.get("credit_details")
            if isinstance(credit_details_raw, str):
                try:
                    credit_details = json.loads(credit_details_raw)
                    if not isinstance(credit_details, list):
                        credit_details = []
                except Exception:
                    credit_details = []
            elif isinstance(credit_details_raw, list):
                credit_details = credit_details_raw

            barcode = patient.get("barcode")

            # Test values scoped to this barcode
            latest_test_data  = test_value_map.get(barcode, {})
            all_test_values   = latest_test_data.get("testdetails", []).copy()
            test_created_date = latest_test_data.get("created_date")

            mb_test_data    = mb_test_value_map.get(barcode, {})
            mb_test_values  = mb_test_data.get("testdetails", [])
            mb_created_date = mb_test_data.get("created_date")
            if mb_test_values:
                all_test_values.extend(mb_test_values)
                if mb_created_date and (not test_created_date or mb_created_date > test_created_date):
                    test_created_date = mb_created_date

            valid_test_values = []
            unapproved_tests  = []
            for test_record in all_test_values:
                if not test_record.get("rerun", False):
                    valid_test_values.append(test_record)
                    if not test_record.get("approve", False):
                        unapproved_tests.append(test_record)

            # Sample tests for this barcode
            sample_tests = sample_status_map.get(barcode, [])

            # Sample collection booleans
            all_collected       = bool(sample_tests) and all(
                t.get("samplestatus") == "Sample Collected" for t in sample_tests if isinstance(t, dict)
            )
            partially_collected = any(
                t.get("samplestatus") == "Sample Collected" for t in sample_tests if isinstance(t, dict)
            )
            all_received        = bool(sample_tests) and all(
                t.get("samplestatus") == "Received" for t in sample_tests if isinstance(t, dict)
            )
            partially_received  = any(
                t.get("samplestatus") == "Received" for t in sample_tests if isinstance(t, dict)
            )

            # Collection timestamps
            collection_time_val = "N/A"
            collected_date_val  = "N/A"
            for t in sample_tests:
                st = t.get("samplecollected_time")
                if not st:
                    continue
                dt = parse_datetime_str(st)
                if dt:
                    collection_time_val = dt.strftime("%I:%M %p")
                    collected_date_val  = dt.strftime("%d-%m-%Y")
                else:
                    collection_time_val = str(st)
                break

            # Overall status
            status = "Registered"
            if all_collected:
                status = "Collected"
            elif partially_collected:
                status = "Partially Collected"
            if all_received:
                status = "Received"
            elif partially_received:
                status = "Partially Received"

            # ── Individual test statuses WITH TAT ─────────────────────────────
            individual_test_statuses = []
            for test in test_list:
                test_id   = test.get('test_id')
                test_name = test.get('testname') or test.get('test_name', 'N/A')

                sample_info     = next((t for t in sample_tests      if t.get('test_id') == test_id), {})
                test_value_info = next((t for t in valid_test_values  if t.get('test_id') == test_id), {})

                # TAT_Time from MongoDB
                test_detail_tat = test_details_collection.find_one(
                    {"test_id": test_id},
                    {"_id": 0, "TAT_Time": 1},
                )
                tat_time = test_detail_tat.get("TAT_Time") if test_detail_tat else None

                # Parse sample_collected_time
                sample_collected_time = None
                raw_sct = sample_info.get('samplecollected_time') if sample_info else None
                if raw_sct:
                    sample_collected_time = parse_datetime_str(raw_sct)
                    if sample_collected_time:
                        logger.debug(f"  [TAT] test_id={test_id} sample_collected_time={sample_collected_time}")
                    else:
                        logger.debug(f"  [TAT] test_id={test_id} FAILED to parse sample_collected_time: {raw_sct!r}")

                # Parse approve_time
                approve_time = None
                raw_at = test_value_info.get('approve_time') if test_value_info else None
                if raw_at:
                    approve_time = parse_datetime_str(raw_at)
                    if approve_time:
                        logger.debug(f"  [TAT] test_id={test_id} approve_time={approve_time}")
                    else:
                        logger.debug(f"  [TAT] test_id={test_id} FAILED to parse approve_time: {raw_at!r}")

                # TAT calculation
                tat_status       = None
                seconds_left     = None
                tat_deadline_iso = None

                if tat_time and sample_collected_time:
                    tat_seconds = parse_tat_format(tat_time)
                    if tat_seconds:
                        tat_deadline     = sample_collected_time + timedelta(seconds=tat_seconds)
                        tat_deadline_iso = tat_deadline.isoformat()
                        if approve_time:
                            # Completed — positive = finished within TAT, negative = overdue
                            time_taken = (approve_time - sample_collected_time).total_seconds()
                            seconds_left = tat_seconds - time_taken
                            tat_status   = "completed"
                        else:
                            # Pending — positive = time remaining, negative = overdue
                            seconds_left = (tat_deadline - datetime.now()).total_seconds()
                            tat_status   = "pending"
                        logger.debug(f"  [TAT] test_id={test_id} tat_time={tat_time} tat_seconds={tat_seconds} "
                              f"tat_status={tat_status} seconds_left={seconds_left}")
                    else:
                        logger.debug(f"  [TAT] test_id={test_id} parse_tat_format returned None for tat_time={tat_time!r}")
                else:
                    logger.debug(f"  [TAT] test_id={test_id} skipped — "
                          f"tat_time={tat_time!r} sample_collected_time={sample_collected_time!r}")

                # Individual test status
                test_status = "Registered"
                if sample_info:
                    ss = sample_info.get('samplestatus', '')
                    if ss in ('Sample Collected', 'Collected'): test_status = "Collected"
                    if ss == 'Transferred':                     test_status = "Transferred"
                    if ss == 'Received':                        test_status = "Received"
                    if ss == 'Rejected':                        test_status = "Rejected"
                    if ss in ('Outsource', 'Outsourced'):       test_status = "Outsourced"

                if test_value_info:
                    has_values = False
                    parameters = test_value_info.get("parameters", [])
                    if parameters:
                        has_values = any(
                            param.get("value") is not None and str(param.get("value")).strip() != ""
                            for param in parameters
                        )
                    elif (
                        test_value_info.get("value") is not None
                        and str(test_value_info.get("value")).strip() != ""
                    ):
                        has_values = True
                    elif test_value_info.get("remarks") or test_value_info.get("parameter_type"):
                        has_values = True  # Microbiology

                    if has_values:                          test_status = "Tested"
                    if test_value_info.get('approve'):      test_status = "Approved"
                    if test_value_info.get('dispatch'):     test_status = "Dispatched"

                individual_test_statuses.append({
                    'test_id':               test_id,
                    'test_name':             test_name,
                    'status':                test_status,
                    'tat_time':              tat_time,
                    'seconds_left':          int(seconds_left) if seconds_left is not None else None,
                    'tat_status':            tat_status,
                    'tat_deadline':          tat_deadline_iso,
                    'sample_collected_time': sample_collected_time.isoformat() if sample_collected_time else None,
                    'approve_time':          approve_time.isoformat() if approve_time else None,
                })

            # ── Overall approval / dispatch status ────────────────────────────
            if valid_test_values:
                def has_test_values(test):
                    parameters = test.get("parameters", [])
                    if not parameters:
                        return bool(test.get("value"))
                    return any(
                        p.get("value") is not None and str(p.get("value")).strip() != ""
                        for p in parameters
                    )

                all_tested       = all(has_test_values(t) for t in valid_test_values)
                partially_tested = any(has_test_values(t) for t in valid_test_values)

                all_ordered_test_ids = {
                    str(t.get("test_id", "")).strip()
                    for t in test_list if isinstance(t, dict) and t.get("test_id")
                }
                approved_test_ids = {
                    str(t.get("test_id", "")).strip()
                    for t in valid_test_values if t.get("approve", False) and t.get("test_id")
                }
                dispatch_test_ids = {
                    str(t.get("test_id", "")).strip()
                    for t in valid_test_values if t.get("dispatch", False) and t.get("test_id")
                }

                all_approved = partially_approved = False
                if all_ordered_test_ids:
                    if all_ordered_test_ids.issubset(approved_test_ids) and len(approved_test_ids) == len(all_ordered_test_ids):
                        all_approved = True
                    elif approved_test_ids:
                        partially_approved = True
                    if not all_approved:
                        approved_count = sum(1 for t in valid_test_values if t.get("approve", False))
                        if approved_count == no_of_tests and approved_count > 0:
                            all_approved = True
                            partially_approved = False
                        elif approved_count > 0:
                            partially_approved = True

                all_dispatched = partially_dispatched = False
                if all_ordered_test_ids:
                    if all_ordered_test_ids.issubset(dispatch_test_ids) and len(dispatch_test_ids) == len(all_ordered_test_ids):
                        all_dispatched = True
                    elif dispatch_test_ids:
                        partially_dispatched = True
                    if not all_dispatched:
                        dispatch_count = sum(1 for t in valid_test_values if t.get("dispatch", False))
                        if dispatch_count == no_of_tests and dispatch_count > 0:
                            all_dispatched = True
                            partially_dispatched = False
                        elif dispatch_count > 0:
                            partially_dispatched = True

                if all_tested:             status = "Tested"
                elif partially_tested:     status = "Partially Tested"
                if all_approved:           status = "Approved"
                elif partially_approved:   status = "Partially Approved"
                if all_dispatched:         status = "Dispatched"
                elif partially_dispatched: status = "Partially Dispatched"

            # Department statuses
            department_statuses = {}
            if pid and test_list:
                department_statuses = get_department_status_franchise(
                    test_list, barcode, sample_status_map, test_value_map, mb_test_value_map
                )

            # Date formatting
            created_date      = patient.get("created_date")
            formatted_date    = "N/A"
            registration_date = "N/A"
            if created_date:
                if isinstance(created_date, datetime):
                    formatted_date    = created_date.strftime("%Y-%m-%d")
                    registration_date = created_date.isoformat()
                else:
                    try:
                        parsed_date       = datetime.strptime(str(created_date), "%Y-%m-%d")
                        formatted_date    = parsed_date.strftime("%Y-%m-%d")
                        registration_date = parsed_date.isoformat()
                    except Exception:
                        formatted_date = registration_date = str(created_date)

            test_created_date_formatted = None
            if test_created_date:
                test_created_date_formatted = (
                    test_created_date.isoformat()
                    if isinstance(test_created_date, datetime)
                    else str(test_created_date)
                )

            formatted_data.append({
                "date":                formatted_date,
                "registration_date":   registration_date,
                "patient_id":          pid,
                "patient_name":        patient_detail.get("patientname", "N/A"),
                "gender":              patient_detail.get("gender", "N/A"),
                "refby":               patient.get("referredDoctor", "N/A"),
                "age":                 age,
                "age_type":            age_type,
                "email":               patient_detail.get("email", "N/A"),
                "branch":              patient.get("franchise_id", "N/A"),
                "total_amount":        total_amount,
                "credit_amount":       credit_amount,
                "credit_details":      credit_details,
                "discount":            discount,
                "payment_method":      payment_details,
                "test_names":          testnames,
                "department":          department,
                "department_statuses": department_statuses,
                "test_statuses":       individual_test_statuses,
                "no_of_tests":         no_of_tests,
                "bill_no":             patient.get("bill_no", "N/A"),
                "registeredby":        patient.get("registeredBy", "N/A"),
                "barcode":             barcode,
                "status":              status,
                "test_created_date":   test_created_date_formatted,
                "collection_time":     collection_time_val,
                "collected_date":      collected_date_val,
            })

        # NOTE: response shape changed from a bare JSON array to a
        # paginated object ({"data": [...], total_count, total_pages,
        # current_page}) to bound the payload for wide date ranges;
        # update any frontend caller that expected a raw array here.
        total_count = len(formatted_data)
        page_obj, page_meta = paginate_queryset(formatted_data, request)
        return JsonResponse({
            "data": list(page_obj),
            "total_count": total_count,
            **page_meta
        }, safe=False)

    except Exception as e:
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def franchise_patient_test_details(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)

    try:
        client = get_client()
        db = client.franchise
        franchise_billing_collection = db.franchise_billing
        franchise_sample_collection  = db.franchise_sample
        franchise_patient_collection = db.franchise_patient

        mongo_db = client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

        franchise_billing = franchise_billing_collection.find_one({"barcode": barcode})
        if not franchise_billing:
            return JsonResponse({'error': 'Franchise billing record not found for the given barcode'}, status=404)

        patient_id = franchise_billing.get('patient_id')
        if not patient_id:
            return JsonResponse({'error': 'Patient ID not found in billing record'}, status=404)

        franchise_patient = franchise_patient_collection.find_one({"patient_id": patient_id})
        if not franchise_patient:
            return JsonResponse({'error': 'Franchise patient not found for the given patient ID'}, status=404)

        franchise_sample = franchise_sample_collection.find_one({"barcode": barcode})

        barcodes = []
        try:
            all_barcodes_for_patient = franchise_billing_collection.find(
                {"patient_id": patient_id},
                {"barcode": 1, "_id": 0}
            )
            barcodes = [bc.get("barcode") for bc in all_barcodes_for_patient if bc.get("barcode")]
            barcodes = list(dict.fromkeys(barcodes))
        except Exception:
            barcodes = []

        test_values = TestValue.objects.filter(barcode=barcode)

        # ── Resolve patient gender for reference range selection ──────────────
        # franchise_patient has a 'gender' field
        patient_gender = (franchise_patient.get('gender') or '').strip()

        # ── Gender-aware reference_range resolver ─────────────────────────────
        def resolve_reference_range(meta, gender):
            """
            Pick reference_range from male/female fields based on patient gender.
            Falls back to generic reference_range when gender-specific value absent.
            """
            gender_key = (gender or '').strip().lower()   # 'male', 'female', or ''

            if gender_key == 'male' and meta.get('male'):
                return meta['male']
            if gender_key == 'female' and meta.get('female'):
                return meta['female']
            return meta.get('reference_range', '') or ''

        # ── Parameter helper ──────────────────────────────────────────────────
        def get_parameter_from_core(core_test, device_id, test_code=None, param_index=None):
            core_parameters = core_test.get("parameters", {})
            params_list = []

            if isinstance(core_parameters, dict):
                if device_id and device_id != "N/A" and device_id in core_parameters:
                    params_list = core_parameters[device_id]
                else:
                    if len(core_parameters) > 0:
                        first_device = list(core_parameters.keys())[0]
                        params_list = core_parameters[first_device]
            elif isinstance(core_parameters, list):
                params_list = core_parameters

            if not isinstance(params_list, list):
                return None

            if param_index is not None and 0 <= param_index < len(params_list):
                return params_list[param_index]

            if test_code:
                matching_params = [p for p in params_list if isinstance(p, dict) and p.get("test_code") == test_code]
                if matching_params:
                    return matching_params[0]

            return None

        # ── Signature helper ──────────────────────────────────────────────────
        def get_employee_signature_data(employee_id):
            if not employee_id:
                return None
            try:
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None

                employee_name     = profile.get("employeeName", "")
                designation       = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")
                signature_base64  = None

                if signature_file_id:
                    try:
                        from bson import ObjectId
                        import base64
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)
                        signature_file   = fs.get(signature_file_id)
                        signature_bytes  = signature_file.read()
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        logger.error(f"Error fetching signature for employee {employee_id}: {str(e)}")

                return {
                    "employeeName":    employee_name,
                    "designation":     designation,
                    "signatureBase64": signature_base64,
                }
            except Exception as e:
                logger.error(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None

        try:
            billing_testdetails = json.loads(franchise_billing.get('testdetails', '[]'))
        except json.JSONDecodeError:
            billing_testdetails = []

        sample_testdetails = []
        if franchise_sample:
            try:
                sample_testdetails = json.loads(franchise_sample.get('testdetails', '[]'))
            except json.JSONDecodeError:
                sample_testdetails = []

        patient_details = {
            "patient_id":  patient_id,
            "patientname": franchise_patient.get("patientname", "N/A"),
            "age":         franchise_patient.get("age", "N/A"),
            "age_type":    franchise_patient.get("age_type", "Years"),
            "gender":      franchise_patient.get("gender", "N/A"),
            "date":        franchise_billing.get("created_date"),
            "barcode":     franchise_billing.get("barcode", "N/A"),
            "bill_no":     franchise_billing.get("bill_no", "N/A"),
            "barcodes":    barcodes,
            "refby":       franchise_billing.get("referredDoctor", "N/A"),
            "branch":      franchise_billing.get("franchise_id", "N/A"),
            "testdetails": [],
        }

        all_approvers = set()

        for billing_test in billing_testdetails:
            test_id  = billing_test.get("test_id")
            testname = billing_test.get("test_name")

            core_test = core_testdetails_collection.find_one({"test_id": test_id})
            if not core_test:
                continue

            sample_status = None
            for sample_test in sample_testdetails:
                if sample_test.get("testname") == testname or sample_test.get("test_id") == test_id:
                    sample_status = sample_test
                    break

            test_value_details = None
            device_id          = "N/A"
            if test_values.exists():
                for test_value in test_values:
                    test_details_list = test_value.testdetails
                    if isinstance(test_details_list, str):
                        try:
                            test_details_list = json.loads(test_details_list)
                        except Exception:
                            test_details_list = []
                    if not isinstance(test_details_list, list):
                        test_details_list = []
                    for test_detail_item in test_details_list:
                        if test_detail_item.get("test_id") == test_id:
                            test_value_details = test_detail_item
                            device_id          = test_detail_item.get("device_id", "N/A")
                            break
                    if test_value_details:
                        break

            department    = core_test.get("department", "N/A")
            NABL          = core_test.get("NABL", False)
            interpretation = core_test.get("interpretation", "")
            critical_range = core_test.get("critical_range", "")
            lod           = core_test.get("lod", "")
            labels         = core_test.get("labels", "")
            specimen_type = test_value_details.get("specimen_type") or core_test.get("specimen_type", "N/A")

            test_detail = {
                "test_id":               test_id,
                "testname":              core_test.get("test_name", testname),
                "department":            department,
                "NABL":                  NABL,
                "interpretation":        interpretation,
                "critical_range":        critical_range,
                "lod":                   lod,
                "labels":                labels,
                "MRP":                   billing_test.get("MRP", "N/A"),
                "samplestatus":          sample_status.get("samplestatus", "N/A")          if sample_status else "N/A",
                "samplecollected_time":  sample_status.get("samplecollected_time")          if sample_status else None,
                "collected_by":          sample_status.get("collected_by", "N/A")           if sample_status else "N/A",
                "sampletransferred_time":sample_status.get("sampletransferred_time")         if sample_status else None,
                "transferred_by":        sample_status.get("transferred_by", "N/A")         if sample_status else "N/A",
                "received_time":         sample_status.get("received_time")                  if sample_status else None,
                "received_by":           sample_status.get("received_by", "N/A")            if sample_status else "N/A",
                "batch_number":          sample_status.get("batch_number", "N/A")           if sample_status else "N/A",
                "remarks":               sample_status.get("remarks")                        if sample_status else None,
                "notes":                 core_test.get("notes", "") if core_test else "",
            }

            if test_value_details:
                outsourced   = test_value_details.get("outsourced", False)
                comment      = test_value_details.get("comment", "")
                verified_by  = test_value_details.get("verified_by", "N/A")
                approve_by   = test_value_details.get("approve_by", "N/A")
                approve_time = test_value_details.get("approve_time", "N/A")

                if approve_by:
                    all_approvers.add(approve_by)

                test_detail.update({
                    "outsourced":   outsourced,
                    "comment":      comment,
                    "verified_by":  verified_by,
                    "approve_by":   approve_by,
                    "approve_time": approve_time,
                })

                parameters = test_value_details.get("parameters", [])

                if parameters and len(parameters) > 0:
                    # ── Parameterised test ────────────────────────────────────
                    enriched_parameters = []
                    for param_index, param_value in enumerate(parameters):
                        test_code     = param_value.get("test_code")
                        value         = param_value.get("value", "")
                        param_comment = param_value.get("comment", "")

                        param_def = get_parameter_from_core(
                            core_test, device_id,
                            test_code=test_code,
                            param_index=param_index,
                        )

                        if param_def:
                            # Gender-resolved reference_range
                            ref_range = resolve_reference_range(param_def, patient_gender)

                            enriched_param = {
                                "name":            param_def.get("test_name", ""),
                                "test_code":       test_code,
                                "value":           value,
                                "unit":            param_def.get("unit", ""),
                                "reference_range": ref_range,   # gender-resolved
                                "method":          param_def.get("method", ""),
                                "specimen_type":   specimen_type,
                                "sub_title":       param_def.get("sub_title", ""),
                                "value_option":    param_def.get("value_option", []),
                                "comment":         param_comment,
                                "notes":           param_def.get("notes", "") or "",
                            }
                        else:
                            # Fallback: no core definition found
                            enriched_param = {
                                "name":            "N/A",
                                "test_code":       test_code,
                                "value":           value,
                                "unit":            "N/A",
                                "reference_range": "N/A",
                                "method":          "N/A",
                                "specimen_type":   specimen_type,
                                "sub_title":       "",
                                "value_option":    [],
                                "comment":         param_comment,
                                "notes":           "",
                            }

                        enriched_parameters.append(enriched_param)
                    test_detail["parameters"] = enriched_parameters

                else:
                    # ── Single-value test (with test value details) ───────────
                    # Gender-resolved reference_range from core_test
                    ref_range = resolve_reference_range(core_test, patient_gender)

                    test_detail.update({
                        "method":          core_test.get("method", ""),
                        "value":           test_value_details.get("value", ""),
                        "unit":            core_test.get("unit", ""),
                        "reference_range": ref_range,   # gender-resolved
                        "sub_title":       test_value_details.get("sub_title", ""),
                    })

            else:
                # ── No test value details — use core test defaults ────────────
                # Still resolve gender-based reference_range for consistency
                ref_range = resolve_reference_range(core_test, patient_gender)

                test_detail.update({
                    "method":          core_test.get("method", "N/A"),
                    "unit":            core_test.get("unit", "N/A"),
                    "reference_range": ref_range,   # gender-resolved
                    "value":           "N/A",
                    "verified_by":     "N/A",
                    "approve_by":      "N/A",
                    "approve_time":    "N/A",
                    "outsourced":      False,
                    "comment":         "",
                })

            patient_details["testdetails"].append(test_detail)

        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)

        # Note: `client` is the shared, pooled MongoClient — do not close it here.

        response_data = {
            "patient_data": patient_details,
            "signatures":   signatures_data,
        }
        return JsonResponse(response_data, safe=False)

    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)


@api_view(['GET'])
def get_test_value_for_franchise(request):

    franchise_id = request.GET.get('franchise_id')
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')

    if not franchise_id or not from_date or not to_date:
        return JsonResponse(
            {'error': 'franchise_id, from_date and to_date are required'},
            status=400
        )

    try:
        # Convert string to date
        from_date_obj = datetime.strptime(from_date, "%Y-%m-%d").date()
        to_date_obj = datetime.strptime(to_date, "%Y-%m-%d").date()

        # 🔥 Step 1: Filter only by locationId (Mongo works fine for equality)
        test_values = TestValue.objects.filter(
            locationId=franchise_id
        )

        result = []

        for test_value in test_values:

            # 🔥 Step 2: Manual date filtering (Mongo-safe)
            record_date = test_value.date

            if from_date_obj <= record_date <= to_date_obj:

                try:
                    testdetails = (
                        json.loads(test_value.testdetails)
                        if isinstance(test_value.testdetails, str)
                        else test_value.testdetails
                    )
                except Exception:
                    testdetails = test_value.testdetails

                result.append({
                    'franchise_id': test_value.locationId,
                    'barcode': test_value.barcode,
                    'date': str(test_value.date),
                    'testdetails': testdetails,
                })

        if not result:
            return JsonResponse(
                {'message': 'No test values found'},
                status=404
            )

        return JsonResponse(
            {'status': 'success', 'data': result},
            status=200,
            safe=False,
            json_dumps_params={'default': json_util.default}
        )

    except Exception as e:
        return JsonResponse(
            {'error': 'Internal server error', 'details': str(e)},
            status=500
        )

    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
