"""
Overall report + department/dispatch status views.

Moved out of the original monolithic core/Views/report.py during the
report.py -> report/ package split. Contains:
  - get_department_status (module-level helper used only by overall_report)
  - overall_report
  - update_dispatch_status
"""
import logging
from rest_framework.response import Response
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework import status
from urllib.parse import quote_plus
from core.mongo_client import get_client
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta
import re
from django.core.mail import EmailMessage
from django.conf import settings
from django.utils.timezone import make_aware
from core.utils import get_employee_name
from pyauth.auth import HasRoleAndDataPermission
from core.models import Patient, SampleStatus, Billing, TestValue, MBTestValue, BarcodeTestDetails
import os, json, traceback
import pytz
from django.utils.dateparse import parse_datetime
import gridfs
from core.pagination import paginate_queryset
from dotenv import load_dotenv

load_dotenv()

# Define IST timezone
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)

logger = logging.getLogger(__name__)


def get_department_status(test_list, barcode, sample_status_map, test_value_map, mb_test_value_map):
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
def overall_report(request):
    try:
        # MongoDB setup for core_billing
        client = get_client()
        db = client.Diagnostics
        billing_collection = db["core_billing"]
        test_details_collection = db.core_testdetails

        # Log collection details
        total_billing_docs = billing_collection.count_documents({})
        sample_billing_doc = billing_collection.find_one()
        if sample_billing_doc:
            logger.debug(f"Sample document from core_billing: {sample_billing_doc}")
        else:
            logger.warning("No documents found in core_billing collection")

        # Date filters
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        selected_date = request.GET.get("selected_date")
        patient_id = request.GET.get("patient_id")

        def parse_tat_format(tat_str):
            """Parse TAT format like '2D 3H 45M' and return total seconds"""
            if not tat_str or tat_str == 'N/A':
                return None
            try:
                total_seconds = 0
                days = re.search(r'(\d+)D', str(tat_str))
                hours = re.search(r'(\d+)H', str(tat_str))
                minutes = re.search(r'(\d+)M', str(tat_str))

                if days:
                    total_seconds += int(days.group(1)) * 86400
                if hours:
                    total_seconds += int(hours.group(1)) * 3600
                if minutes:
                    total_seconds += int(minutes.group(1)) * 60

                return total_seconds if total_seconds > 0 else None
            except Exception as e:
                logger.exception(f"Error parsing TAT format: {e}")
                return None

        # Validate and parse dates
        try:
            if selected_date:
                selected_date_parsed = datetime.strptime(selected_date, "%Y-%m-%d")
                from_date = selected_date_parsed
                to_date = selected_date_parsed + timedelta(days=1)

            elif from_date and to_date:
                from_date = datetime.strptime(from_date, "%Y-%m-%d")
                to_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
            else:
                logger.warning("Missing date parameters")
                return JsonResponse({"error": "Either 'selected_date' or both 'from_date' and 'to_date' are required"}, status=400)
        except ValueError:
            logger.warning("Invalid date format received")
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        # Query core_billing
        billing_query = {"date": {"$gte": from_date, "$lt": to_date}}
        if patient_id:
            billing_query["patient_id"] = patient_id

        billing_records = list(billing_collection.find(billing_query))
        if billing_records:
            logger.debug(f"Sample core_billing record: {billing_records[0]}")

        if not billing_records:
            test_query = {"patient_id": "SD0009"}
            test_result = list(billing_collection.find(test_query))
            if test_result:
                logger.debug(f"Test query result: {test_result[0]}")
            return JsonResponse([], safe=False)

        def fetch_in_chunks(collection, query_field, param_list, additional_query=None, projection=None, chunk_size=50):
            results = []
            if not param_list:
                return results
            for i in range(0, len(param_list), chunk_size):
                chunk = param_list[i:i+chunk_size]
                query = {query_field: {"$in": chunk}}
                if additional_query:
                    query.update(additional_query)
                if projection:
                    results.extend(list(collection.find(query, projection)))
                else:
                    results.extend(list(collection.find(query)))
            return results

        # Fetch barcode from BarcodeTestDetails using PyMongo
        bill_nos = [record['bill_no'] for record in billing_records if record.get('bill_no')]

        barcode_collection = db.core_barcodetestdetails
        barcode_records = fetch_in_chunks(
            collection=barcode_collection,
            query_field="bill_no",
            param_list=bill_nos,
            projection={'_id': 0, 'patient_id': 1, 'patientname': 1, 'age': 1, 'gender': 1, 'segment': 1, 'date': 1, 'bill_no': 1, 'barcode': 1, 'testdetails': 1},
            chunk_size=50
        )

        barcode_map = {record['bill_no']: record for record in barcode_records}
        if barcode_records:
            logger.debug(f"Sample BarcodeTestDetails record: {barcode_records[0]}")

        # Fetch patient details from Patient model
        patient_ids = [record['patient_id'] for record in billing_records if record.get('patient_id')]
        patient_details_map = {}
        try:
            patient_collection = db.core_patient
            patient_records = fetch_in_chunks(
                collection=patient_collection,
                query_field="patient_id",
                param_list=patient_ids,
                chunk_size=50
            )
            for patient_record in patient_records:
                patient_details_map[patient_record['patient_id']] = patient_record
        except Exception as e:
            logger.error(f"Error fetching patient details from Patient model: {str(e)}")

        # Fetch status and test data
        barcodes = [record['barcode'] for record in barcode_records if record.get('barcode')]

        # Prepare date query based on naive datetime
        date_query = {"$gte": from_date, "$lt": to_date}

        sample_status_collection = db.core_samplestatus
        sample_status_records = fetch_in_chunks(
            collection=sample_status_collection,
            query_field="barcode",
            param_list=barcodes,
            additional_query={"date": date_query},
            chunk_size=50
        )

        test_value_collection = db.core_testvalue
        test_value_records = fetch_in_chunks(
            collection=test_value_collection,
            query_field="barcode",
            param_list=barcodes,
            additional_query={"date": date_query},
            chunk_size=50
        )

        # Fetch MBTestValue records using PyMongo
        mb_test_value_collection = db.core_mbtestvalue
        mb_test_value_records = fetch_in_chunks(
            collection=mb_test_value_collection,
            query_field="barcode",
            param_list=barcodes,
            additional_query={"date": date_query},
            chunk_size=50
        )

        # Organize status data
        sample_status_map = {}
        for record in sample_status_records:
            td = record.get("testdetails", [])
            if isinstance(td, str):
                try:
                    td = json.loads(td.strip('"'))
                except json.JSONDecodeError:
                    td = []
            if isinstance(td, list):
                sample_status_map.setdefault(record.get("barcode", ""), []).extend(td)

        # Organize test value data - COMBINE ALL RECORDS FOR SAME BARCODE
        test_value_map = {}
        for record in test_value_records:
            barcode = record["barcode"]
            created_date = record["created_date"]
            testdetails = record["testdetails"]

            # Parse testdetails if it's a string
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails.strip('"'))
                except json.JSONDecodeError:
                    testdetails = []

            if barcode not in test_value_map:
                test_value_map[barcode] = {
                    "barcode": barcode,
                    "testdetails": [],
                    "created_date": created_date
                }

            # Add all test details from this record
            if isinstance(testdetails, list):
                test_value_map[barcode]["testdetails"].extend(testdetails)

            # Update to latest created_date
            if created_date > test_value_map[barcode]["created_date"]:
                test_value_map[barcode]["created_date"] = created_date


        # Organize MBTestValue data - COMBINE ALL RECORDS FOR SAME BARCODE
        mb_test_value_map = {}
        for record in mb_test_value_records:
            barcode = record["barcode"]
            created_date = record["created_date"]
            testdetails = record["testdetails"]

            # Parse testdetails if it's a string
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails.strip('"'))
                except json.JSONDecodeError:
                    testdetails = []

            if barcode not in mb_test_value_map:
                mb_test_value_map[barcode] = {
                    "barcode": barcode,
                    "testdetails": [],
                    "created_date": created_date
                }

            # Add all test details from this record
            if isinstance(testdetails, list):
                mb_test_value_map[barcode]["testdetails"].extend(testdetails)

            # Update to latest created_date
            if created_date and (not mb_test_value_map[barcode]["created_date"] or created_date > mb_test_value_map[barcode]["created_date"]):
                mb_test_value_map[barcode]["created_date"] = created_date

        # Format response
        formatted_data = []
        for record in billing_records:
            pid = record.get("patient_id", "N/A")
            barcode_data = barcode_map.get(record.get("bill_no", ""), {})
            patient_model_data = patient_details_map.get(pid, {})

            # Merge patient data
            merged_patient_data = {
                "patient_id": pid,
                "patientname": patient_model_data.get("patientname") or barcode_data.get("patientname", "N/A"),
                "age": patient_model_data.get("age") or barcode_data.get("age", "N/A"),
                "age_type": patient_model_data.get("age_type") or "",
                "gender": patient_model_data.get("gender") or barcode_data.get("gender", "N/A"),
                "phone": patient_model_data.get("phone", "N/A"),
                "email": patient_model_data.get("email", "N/A"),
                "address": patient_model_data.get("address", "N/A"),
            }

            # Parse address
            if isinstance(merged_patient_data["address"], str):
                try:
                    address_data = json.loads(merged_patient_data["address"])
                    if isinstance(address_data, dict):
                        area = address_data.get("area", "")
                        pincode = address_data.get("pincode", "")
                        formatted_address = f"{area}, {pincode}".strip(", ")
                        merged_patient_data["address"] = formatted_address if formatted_address else "N/A"
                except Exception as e:
                    logger.exception(f"Error parsing patient address JSON: {e}")
                    pass
            elif isinstance(merged_patient_data["address"], dict):
                area = merged_patient_data["address"].get("area", "")
                pincode = merged_patient_data["address"].get("pincode", "")
                merged_patient_data["address"] = f"{area}, {pincode}".strip(", ") or "N/A"

            # Billing details
            refby = record.get("refby", "N/A")
            segment = record.get("segment", barcode_data.get("segment", "N/A"))
            b2b = record.get("B2B", "N/A")
            branch = record.get("branch", "N/A")
            sample_collector = get_employee_name(record.get("sample_collector", ""))
            if not sample_collector:
                sample_collector = "N/A"
            sales_mapping = record.get("salesMapping", "N/A")
            bill_no = record.get("bill_no", "N/A")
            registeredby = record.get("created_by", "N/A")

            # Payment method parsing
            payment_details = {}
            raw = record.get("payment_method", "")
            if raw:
                if isinstance(raw, str):
                    try:
                        cleaned = raw.strip('"')
                        payment_data = json.loads(cleaned) if cleaned else {}
                        payment_details = payment_data if isinstance(payment_data, dict) else {"paymentmethod": str(payment_data)}
                    except json.JSONDecodeError:
                        payment_details = {"paymentmethod": raw}
                elif isinstance(raw, dict):
                    payment_details = raw
            else:
                payment_details = {"paymentmethod": "N/A"}

            if payment_details.get("paymentmethod") == "MultiplePayment":
                multiple_data = record.get("MultiplePayment", "")
                try:
                    if isinstance(multiple_data, str):
                        multiple_data = json.loads(multiple_data.strip('"')) if multiple_data.strip('"') else []
                    if isinstance(multiple_data, list):
                        payment_details["multiple_payments"] = multiple_data
                except json.JSONDecodeError:
                    pass

            # Test list - Get test_ids and enrich with test names and departments from MongoDB
            test_list = []
            test_ids = []
            departments_set = set()

            # Try to get test_ids from barcode_data or billing record
            test_field = barcode_data.get("testdetails", []) or record.get("testdetails", [])

            if isinstance(test_field, str):
                try:
                    test_field = json.loads(test_field.strip('"'))
                except json.JSONDecodeError:
                    test_field = []

            if isinstance(test_field, list):
                test_ids = [test.get("test_id") for test in test_field if test.get("test_id")]

            # Enrich test details from MongoDB core_testdetails with department
            if test_ids:
                for test_id in test_ids:
                    test_detail = test_details_collection.find_one(
                        {"test_id": test_id},
                        {"_id": 0, "test_id": 1, "test_name": 1, "department": 1}
                    )
                    if test_detail:
                        dept = test_detail.get("department", "N/A")
                        test_list.append({
                            "test_id": test_id,
                            "testname": test_detail.get("test_name", "N/A"),
                            "department": dept
                        })
                        # Collect department
                        if dept and dept != "N/A":
                            departments_set.add(dept)

            # Fallback to test_names if available in billing record
            if not test_list:
                test_names_str = record.get("test_names", "")
                if test_names_str:
                    test_list = [{"testname": name.strip(), "department": "N/A"} for name in test_names_str.split(",") if name.strip()]

            testnames = ", ".join([test.get("testname", "") for test in test_list])
            no_of_tests = len(test_list) or record.get("no_of_tests", 0)

            # Format departments as comma-separated string
            department = ", ".join(sorted(departments_set)) if departments_set else "N/A"

            # Get department-wise status
            barcode = barcode_data.get("barcode", None)
            department_statuses = {}
            if barcode and test_list:
                department_statuses = get_department_status(
                    test_list,
                    barcode,
                    sample_status_map,
                    test_value_map,
                    mb_test_value_map
                )
            # Amounts
            try:
                total_amount = int(float(record.get("totalAmount", 0) or 0))
            except Exception as e:
                logger.exception(f"Error parsing total_amount: {e}")
                total_amount = 0

            try:
                credit_amount = int(float(record.get("credit_amount", 0) or 0))
            except Exception as e:
                logger.exception(f"Error parsing credit_amount: {e}")
                credit_amount = 0

            try:
                discount = int(float(record.get('discount', 0) or 0))
            except (ValueError, TypeError):
                discount = 0

            # STATUS DETERMINATION
            barcode = barcode_data.get("barcode", None)
            status = record.get("status", "Registered")
            sample_tests = sample_status_map.get(barcode, []) if barcode else []

            # Get combined test value data from BOTH TestValue and MBTestValue
            latest_test_data = test_value_map.get(barcode, {}) if barcode else {}
            all_test_values = latest_test_data.get("testdetails", []).copy()
            test_created_date = latest_test_data.get("created_date", None)

            # Add MBTestValue data
            mb_test_data = mb_test_value_map.get(barcode, {}) if barcode else {}
            mb_test_values = mb_test_data.get("testdetails", [])
            mb_created_date = mb_test_data.get("created_date", None)

            # Combine test values from both sources
            if mb_test_values:
                all_test_values.extend(mb_test_values)

                # Update to latest created_date between both sources
                if mb_created_date:
                    if not test_created_date or mb_created_date > test_created_date:
                        test_created_date = mb_created_date

            # Filter out rerun records
            valid_test_values = []
            unapproved_tests = []
            if all_test_values:
                for test_record in all_test_values:
                    if not test_record.get("rerun", False):
                        valid_test_values.append(test_record)
                        if not test_record.get("approve", False):
                            unapproved_tests.append(test_record)

            # Sample collection status and timestamps
            all_collected = all(t.get("samplestatus") == "Sample Collected" for t in sample_tests) if sample_tests else False
            partially_collected = any(t.get("samplestatus") == "Sample Collected" for t in sample_tests)
            all_received = all(t.get("samplestatus") == "Received" for t in sample_tests) if sample_tests else False
            partially_received = any(t.get("samplestatus") == "Received" for t in sample_tests)

            collection_time_val = "N/A"
            collected_date_val = "N/A"
            if sample_tests:
                for t in sample_tests:
                    st = t.get("samplecollected_time")
                    if st:
                        try:
                            if isinstance(st, str):
                                if 'T' in st:
                                    dt = datetime.fromisoformat(st)
                                else:
                                    try:
                                        dt = datetime.strptime(st, "%Y-%m-%d %H:%M:%S")
                                    except ValueError:
                                        dt = None

                                if dt:
                                    collection_time_val = dt.strftime("%I:%M %p")
                                    collected_date_val = dt.strftime("%d-%m-%Y")
                                else:
                                    collection_time_val = st
                            elif isinstance(st, datetime):
                                collection_time_val = st.strftime("%I:%M %p")
                                collected_date_val = st.strftime("%d-%m-%Y")

                            if collection_time_val != "N/A":
                                break
                        except Exception:
                            collection_time_val = str(st)
                            break

            if all_collected:
                status = "Collected"
            elif partially_collected:
                status = "Partially Collected"

            if all_received:
                status = "Received"
            elif partially_received:
                status = "Partially Received"




                # Get individual test statuses
            individual_test_statuses = []
            if barcode and test_list:
                    for test in test_list:
                        test_id = test.get('test_id')
                        test_name = test.get('testname', 'N/A')

                        # Get sample info
                        sample_info = next((t for t in sample_tests if t.get('test_id') == test_id), {})

                        # Get test value info
                        test_value_info = next((t for t in valid_test_values if t.get('test_id') == test_id), {})

                        # Get TAT from core_testdetails
                        test_detail = test_details_collection.find_one(
                            {"test_id": test_id},
                            {"_id": 0, "TAT_Time": 1}
                        )
                        tat_time = test_detail.get("TAT_Time") if test_detail else None

                        # Get timestamps
                        sample_collected_time = None
                        approve_time = None

                        if sample_info and sample_info.get('samplecollected_time'):
                            try:
                                if isinstance(sample_info['samplecollected_time'], str):
                                    sample_collected_time = datetime.strptime(
                                        sample_info['samplecollected_time'],
                                        "%Y-%m-%d %H:%M:%S"
                                    )
                                elif isinstance(sample_info['samplecollected_time'], datetime):
                                    sample_collected_time = sample_info['samplecollected_time']
                            except Exception as e:
                                logger.error(f"Error parsing sample_collected_time: {e}")

                        if test_value_info and test_value_info.get('approve_time'):
                            try:
                                approve_time_str = test_value_info['approve_time']
                                if approve_time_str and approve_time_str != 'null':
                                    approve_time = datetime.strptime(
                                        approve_time_str,
                                        "%Y-%m-%d %H:%M:%S"
                                    )
                            except Exception as e:
                                logger.error(f"Error parsing approve_time: {e}")

                        # Calculate TAT status
                        tat_status = None
                        seconds_left = None
                        tat_deadline_iso = None

                        if tat_time and sample_collected_time:
                            # Parse TAT_Time using the new function
                            tat_seconds = parse_tat_format(tat_time)

                            if tat_seconds:
                                tat_deadline = sample_collected_time + timedelta(seconds=tat_seconds)
                                tat_deadline_iso = tat_deadline.isoformat()

                                if approve_time:
                                    # Test is approved - calculate time taken
                                    time_taken_seconds = (approve_time - sample_collected_time).total_seconds()
                                    seconds_left = tat_seconds - time_taken_seconds
                                    tat_status = "completed"
                                else:
                                    # Test not approved yet - calculate remaining time
                                    now = datetime.now()
                                    seconds_left = (tat_deadline - now).total_seconds()
                                    tat_status = "pending"

                        # Determine individual test status
                        test_status = "Registered"

                        if sample_info:
                            if sample_info.get('samplestatus') == 'Sample Collected':
                                test_status = "Collected"
                            if sample_info.get('samplestatus') == 'Received':
                                test_status = "Received"
                            if sample_info.get('samplestatus') == 'Rejected':
                                test_status = "Rejected"
                            if sample_info.get('samplestatus') == 'Outsource':
                                test_status = "Outsourced"

                        parameters = []
                        has_values = False

                        if test_value_info:
                        # Check if test has values
                            parameters = test_value_info.get("parameters", [])

                        if parameters:
                            has_values = any(
                                param.get("value") is not None and str(param.get("value")).strip() != ""
                                for param in parameters
                            )

                        # Biochemistry (single value)
                        elif test_value_info.get("value") is not None and str(test_value_info.get("value")).strip() != "":
                            has_values = True

                        # ✅ Microbiology FIX
                        elif test_value_info.get("remarks") or test_value_info.get("parameter_type"):
                            has_values = True

                        else:
                            has_values = False

                        if has_values:
                            test_status = "Tested"

                        if test_value_info.get('approve'):
                            test_status = "Approved"

                        if test_value_info.get('dispatch'):
                            test_status = "Dispatched"

                        individual_test_statuses.append({
                            'test_id': test_id,
                            'test_name': test_name,
                            'status': test_status,
                            'tat_time': tat_time,
                            'seconds_left': int(seconds_left) if seconds_left is not None else None,
                            'tat_status': tat_status,
                            'tat_deadline': tat_deadline_iso,
                            'sample_collected_time': sample_collected_time.isoformat() if sample_collected_time else None,
                            'approve_time': approve_time.isoformat() if approve_time else None,
                        })

            # Test value status logic using test_id
            if valid_test_values:
                # Check testing status
                def has_test_values(test):
                    parameters = test.get("parameters", [])
                    if not parameters:
                        return bool(test.get("value"))
                    return any(
                        param.get("value") is not None and str(param.get("value")).strip() != ""
                        for param in parameters
                    )

                all_tested = all(has_test_values(t) for t in valid_test_values)
                partially_tested = any(has_test_values(t) for t in valid_test_values)

                # Get test_ids from billing/barcode rdispatch_test_idsecord
                all_ordered_test_ids = {test.get("test_id") for test in test_list if test.get("test_id")}

                # Get approved and dispatch test_ids from test value records
                approved_test_ids = {t.get("test_id") for t in valid_test_values if t.get("approve", False) and t.get("test_id")}
                dispatch_test_ids = {t.get("test_id") for t in valid_test_values if t.get("dispatch", False) and t.get("test_id")}


                # Check approval status
                all_approved = False
                partially_approved = False

                if len(all_ordered_test_ids) > 0:
                    # Compare test_ids
                    if all_ordered_test_ids.issubset(approved_test_ids) and len(approved_test_ids) == len(all_ordered_test_ids):
                        all_approved = True
                    elif len(approved_test_ids) > 0:
                        partially_approved = True

                    # Fallback - check if all individual tests are approved
                    if not all_approved and valid_test_values:
                        approved_count = sum(1 for t in valid_test_values if t.get("approve", False))
                        total_expected = record.get("no_of_tests", 0)

                        if approved_count == total_expected and approved_count > 0:
                            all_approved = True
                            partially_approved = False
                        elif approved_count > 0:
                            partially_approved = True

                # Check dispatch status
                all_dispatched = False
                partially_dispatched = False

                if len(all_ordered_test_ids) > 0:
                    # Compare test_ids for dispatch
                    if all_ordered_test_ids.issubset(dispatch_test_ids) and len(dispatch_test_ids) == len(all_ordered_test_ids):
                        all_dispatched = True
                    elif len(dispatch_test_ids) > 0:
                        partially_dispatched = True

                    # Fallback - check if all individual tests are dispatched
                    if not all_dispatched and valid_test_values:
                        dispatch_count = sum(1 for t in valid_test_values if t.get("dispatch", False))
                        total_expected = record.get("no_of_tests", 0)

                        if dispatch_count == total_expected and dispatch_count > 0:
                            all_dispatched = True
                            partially_dispatched = False
                        elif dispatch_count > 0:
                            partially_dispatched = True


                # Set status based on testing progress
                if all_tested:
                    status = "Tested"
                elif partially_tested:
                    status = "Partially Tested"

                # Set status based on approval
                if all_approved:
                    status = "Approved"
                elif partially_approved:
                    status = "Partially Approved"

                # Set status based on dispatch
                if all_dispatched:
                    status = "Dispatched"
                elif partially_dispatched:
                    status = "Partially Dispatched"

            # Date formatting
            formatted_date = record["date"].strftime("%Y-%m-%d") if record.get("date") else "N/A"
            registration_date = record.get("bill_date", record.get("created_date", formatted_date))
            if isinstance(registration_date, datetime):
                registration_date = registration_date.isoformat()
            elif isinstance(registration_date, str):
                try:
                    parsed_date = datetime.fromisoformat(registration_date.replace('Z', '+00:00'))
                    registration_date = parsed_date.isoformat()
                except ValueError:
                    pass

            test_created_date_formatted = None
            if test_created_date:
                if isinstance(test_created_date, datetime):
                    test_created_date_formatted = test_created_date.isoformat()
                else:
                    test_created_date_formatted = str(test_created_date)

            formatted_data.append({
                "date": formatted_date,
                "registration_date": registration_date,
                "patient_id": merged_patient_data["patient_id"],
                "patient_name": merged_patient_data["patientname"],
                "gender": merged_patient_data["gender"],
                "age": f"{merged_patient_data['age']} {merged_patient_data['age_type']}",
                "phone": merged_patient_data["phone"],
                "email": merged_patient_data["email"],
                "address": merged_patient_data["address"],
                "refby": refby,
                "segment": segment,
                "b2b": b2b,
                "branch": branch,
                "sample_collector": sample_collector,
                "salesMapping": sales_mapping,
                "total_amount": total_amount,
                "credit_amount": credit_amount,
                "credit_details": json.loads(record.get("credit_details")) if isinstance(record.get("credit_details"), str) else record.get("credit_details", []),
                "discount": discount,
                "payment_method": payment_details,
                "test_names": testnames,
                "department": department,
                "department_statuses": department_statuses,
                "test_statuses": individual_test_statuses,
                "no_of_tests": no_of_tests,
                "bill_no": bill_no,
                "registeredby": registeredby,
                "barcode": barcode,
                "status": status,
                "test_created_date": test_created_date_formatted,
                "collection_time": collection_time_val,
                "collected_date": collected_date_val,
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
        logger.error(f"Critical Error: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)


@api_view(['PATCH'])
@permission_classes([HasRoleAndDataPermission])
def update_dispatch_status(request, barcode):
    """
    Update dispatch status for a specific test in core_testvalue collection.
    Uses barcode, test_id, and created_date for accurate targeting.
    """
    client = get_client()
    db = client.Diagnostics  # Database name
    collection = db.core_testvalue

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
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass
