from rest_framework.response import Response
from django.http import JsonResponse 
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from urllib.parse import quote_plus
from pymongo import MongoClient
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta
import re
from datetime import datetime, timedelta
from django.core.mail import EmailMessage
from django.conf import settings 
from django.utils.timezone import make_aware 
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ..models import Patient
from ..models import SampleStatus,Billing
from ..models import TestValue, MBTestValue
from ..models import BarcodeTestDetails
import os, json, traceback
from django.utils.timezone import make_aware
from django.views.decorators.csrf import csrf_exempt
from django.core.mail import EmailMessage
from dotenv import load_dotenv
import pytz
from django.utils.dateparse import parse_datetime
import gridfs
load_dotenv()
# Define IST timezone
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)

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
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        billing_collection = db["core_billing"]
        test_details_collection = db.core_testdetails

        # Log collection details
        total_billing_docs = billing_collection.count_documents({})
        sample_billing_doc = billing_collection.find_one()
        if sample_billing_doc:
            print("Sample document from core_billing:", sample_billing_doc)
        else:
            print("No documents found in core_billing collection")

        # Date filters
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        selected_date = request.GET.get("selected_date")
        patient_id = request.GET.get("patient_id")

        print("Received query parameters:", request.GET)
        print(f"from_date: {from_date}, to_date: {to_date}, selected_date: {selected_date}, patient_id: {patient_id}")

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
            except:
                return None

        # Validate and parse dates
        try:
            if selected_date:
                selected_date_parsed = datetime.strptime(selected_date, "%Y-%m-%d")
                from_date = selected_date_parsed
                to_date = selected_date_parsed + timedelta(days=1)
                print(f"Using selected_date: {selected_date}, parsed from_date: {from_date}, to_date: {to_date}")
            elif from_date and to_date:
                from_date = datetime.strptime(from_date, "%Y-%m-%d")
                to_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
                print(f"Using date range - parsed from_date: {from_date}, to_date: {to_date}")
            else:
                print("Missing date parameters")
                return JsonResponse({"error": "Either 'selected_date' or both 'from_date' and 'to_date' are required"}, status=400)
        except ValueError:
            print("Invalid date format received")
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        # Query core_billing
        billing_query = {"date": {"$gte": from_date, "$lt": to_date}}
        if patient_id:
            billing_query["patient_id"] = patient_id
        print(f"core_billing query: {billing_query}")
       
        billing_records = list(billing_collection.find(billing_query))
        print(f"Found {len(billing_records)} core_billing records")
        if billing_records:
            print("Sample core_billing record:", billing_records[0])

        if not billing_records:
            distinct_dates = billing_collection.distinct("date")
            print("Distinct date values in core_billing:", [str(d) for d in distinct_dates])
            test_query = {"patient_id": "SD0009"}
            test_result = list(billing_collection.find(test_query))
            print(f"Test query for patient_id SD0009: Found {len(test_result)} documents")
            if test_result:
                print("Test query result:", test_result[0])
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
        print(f"Fetching BarcodeTestDetails for {len(bill_nos)} bill_nos")
        
        barcode_collection = db.core_barcodetestdetails
        barcode_records = fetch_in_chunks(
            collection=barcode_collection,
            query_field="bill_no",
            param_list=bill_nos,
            projection={'_id': 0, 'patient_id': 1, 'patientname': 1, 'age': 1, 'gender': 1, 'segment': 1, 'date': 1, 'bill_no': 1, 'barcode': 1, 'testdetails': 1},
            chunk_size=50
        )
        
        barcode_map = {record['bill_no']: record for record in barcode_records}
        print(f"Found {len(barcode_records)} BarcodeTestDetails records")
        if barcode_records:
            print("Sample BarcodeTestDetails record:", barcode_records[0])

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
            print(f"Fetched {len(patient_details_map)} patient records from Patient model via PyMongo")
        except Exception as e:
            print(f"Error fetching patient details from Patient model: {str(e)}")

        # Fetch status and test data
        barcodes = [record['barcode'] for record in barcode_records if record.get('barcode')]
        print(f"Barcodes for querying: {len(barcodes)}")
        
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
        print(f"Fetched {len(sample_status_records)} SampleStatus records")

        test_value_collection = db.core_testvalue
        test_value_records = fetch_in_chunks(
            collection=test_value_collection,
            query_field="barcode",
            param_list=barcodes,
            additional_query={"date": date_query},
            chunk_size=50
        )
        print(f"Fetched {len(test_value_records)} TestValue records")

        # Fetch MBTestValue records using PyMongo
        mb_test_value_collection = db.core_mbtestvalue
        mb_test_value_records = fetch_in_chunks(
            collection=mb_test_value_collection,
            query_field="barcode",
            param_list=barcodes,
            additional_query={"date": date_query},
            chunk_size=50
        )
        print(f"Fetched {len(mb_test_value_records)} MBTestValue records")

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

        print(f"Processed test value map with {len(test_value_map)} unique barcodes")

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

        print(f"Processed MB test value map with {len(mb_test_value_map)} unique barcodes")

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
                except:
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
                    print(f"Using test_names fallback for barcode {barcode_data.get('barcode')}: {test_names_str}")
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
            except:
                total_amount = 0

            try:
                credit_amount = int(float(record.get("credit_amount", 0) or 0))
            except:
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
                print(f"Added {len(mb_test_values)} MB test values for barcode {barcode}")
                
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

            print(f"Barcode: {barcode}, Total test records: {len(all_test_values)}, Valid (non-rerun) tests: {len(valid_test_values)}, Unapproved tests: {len(unapproved_tests)}")

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
                                print(f"Error parsing sample_collected_time: {e}")
                        
                        if test_value_info and test_value_info.get('approve_time'):
                            try:
                                approve_time_str = test_value_info['approve_time']
                                if approve_time_str and approve_time_str != 'null':
                                    approve_time = datetime.strptime(
                                        approve_time_str, 
                                        "%Y-%m-%d %H:%M:%S"
                                    )
                            except Exception as e:
                                print(f"Error parsing approve_time: {e}")
                        
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

                        # Biochemistry (parameters)
                        print("parameters", parameters)
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

            print(f"Final status for {barcode}: {status}")
           
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

        return JsonResponse(formatted_data, safe=False)

    except Exception as e:
        print(f"Critical Error: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)    

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def patient_test_sorting(request):
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
        
        # Filter test values by barcode and include created_date
        tests = TestValue.objects.filter(barcode=barcode, date=formatted_date).values("testdetails", "created_date")
        test_list = []
        
        for test in tests:
            test_created_date = test.get("created_date")
            
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
            
            # Filter only approved tests and enrich with test_code and test_name from core_testdetails
            for test_item in testdetails_list:
                if test_item.get('approve') is True:
                    test_id = test_item.get('test_id')
                    test_code = test_item.get('test_code')
                    
                    # Fetch test_code and test_name from core_testdetails collection
                    if test_id:
                        # Build query to match both test_id and test_code if available
                        query = {"test_id": test_id}
                        if test_code:
                            query["test_code"] = test_code
                        
                        core_test = core_testdetails_collection.find_one(
                            query,
                            {"test_code": 1, "test_name": 1,"NABL": 1, "department": 1, "_id": 0}
                        )
                        
                        if core_test:
                            # Update test_code and test_name from core_testdetails
                            test_item['test_code'] = core_test.get('test_code', 'N/A')
                            test_item['test_name'] = core_test.get('test_name', test_item.get('test_name', 'N/A'))
                            test_item['NABL'] = core_test.get('NABL', 'N/A')
                            test_item['department'] = core_test.get('department', 'N/A')
                        else:
                            # If no match found, try with just test_id
                            core_test = core_testdetails_collection.find_one(
                                {"test_id": test_id},
                                {"test_code": 1, "test_name": 1, "NABL": 1, "department": 1, "_id": 0}
                            )
                            
                            if core_test:
                                test_item['test_code'] = core_test.get('test_code', 'N/A')
                                test_item['test_name'] = core_test.get('test_name', test_item.get('test_name', 'N/A'))
                                test_item['NABL'] = core_test.get('NABL', 'N/A')
                                test_item['department'] = core_test.get('department', 'N/A')
                            else:
                                test_item['test_code'] = test_item.get('test_code', 'N/A')
                                test_item['test_name'] = test_item.get('test_name', 'N/A')
                                test_item['NABL'] = test_item.get('NABL', 'N/A')
                                test_item['department'] = test_item.get('department', 'N/A')
                    else:
                        test_item['test_code'] = test_item.get('test_code', 'N/A')
                        test_item['test_name'] = test_item.get('test_name', 'N/A')
                        test_item['NABL'] = test_item.get('NABL', 'N/A')
                        test_item['department'] = test_item.get('department', 'N/A')

                    # Add created_date to each test item
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
def get_patient_test_details(request):
    barcode = request.GET.get('barcode')
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)

    try:
        barcode_details = BarcodeTestDetails.objects.filter(barcode=barcode).first()
        if not barcode_details:
            return JsonResponse({'error': 'No barcode details found for the given barcode'}, status=404)

        patient_id = barcode_details.patient_id
        bill_no    = barcode_details.bill_no

        test_values = TestValue.objects.filter(barcode=barcode)
        if not test_values.exists():
            return JsonResponse({'error': 'No test records found for the given barcode'}, status=404)

        patient = Patient.objects.filter(patient_id=patient_id).first()
        billing = Billing.objects.filter(bill_no=bill_no).first()
        sample_status = SampleStatus.objects.filter(patient_id=patient_id)

        barcodes = []
        try:
            tests    = json.loads(barcode_details.testdetails) if isinstance(barcode_details.testdetails, str) else barcode_details.testdetails
            barcodes = [test.get("barcode") for test in tests if test.get("barcode")]
        except (json.JSONDecodeError, AttributeError):
            barcodes = []

        # ── Resolve patient gender for reference range selection ──────────────
        # gender is stored on the Patient model; normalise to lowercase for matching
        patient_gender = (patient.gender or '') if patient else ''

        mongo_client             = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        mongo_db                 = mongo_client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = mongo_client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

        # ── Gender-aware reference_range / low / high resolver ────────────────
        def resolve_gender_fields(meta, gender):
            """
            Pick reference_range from male/female fields based on patient gender.
            Falls back to generic reference_range when gender-specific value absent.
            Returns (reference_range, _, _) — low/high not used in this view.
            """
            gender_key = (gender or '').strip().lower()   # 'male', 'female', or ''

            if gender_key == 'male' and meta.get('male'):
                reference_range = meta['male']
            elif gender_key == 'female' and meta.get('female'):
                reference_range = meta['female']
            else:
                reference_range = meta.get('reference_range', '') or ''

            return reference_range, '', ''

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

                employee_name      = profile.get("employeeName", "")
                designation        = profile.get("designation", "")
                signature_file_id  = profile.get("signatureFileId")
                signature_base64   = None

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
                        print(f"Error fetching signature for employee {employee_id}: {str(e)}")

                return {
                    "employeeName":    employee_name,
                    "designation":     designation,
                    "signatureBase64": signature_base64,
                }
            except Exception as e:
                print(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None

        # ── Process each TestValue record ─────────────────────────────────────
        all_results   = []
        all_approvers = set()

        for test_value_record in test_values:
            approved_tests = []

            test_details_list = test_value_record.testdetails
            if isinstance(test_details_list, str):
                try:
                    test_details_list = json.loads(test_details_list)
                except Exception:
                    test_details_list = []

            if not isinstance(test_details_list, list):
                test_details_list = []

            for test in test_details_list:
                if test.get("approve") is not True:
                    continue

                test_id     = test.get("test_id")
                device_id   = test.get("device_id")
                parameters  = test.get("parameters", [])
                approve_by  = test.get("approve_by", "")
                dispatch_time = test.get("dispatch_time", "")

                if approve_by:
                    all_approvers.add(approve_by)

                core_test = core_testdetails_collection.find_one({"test_id": test_id})

                if not core_test:
                    testname     = test.get("testname")
                    department   = test.get("department", "N/A")
                    NABL         = test.get("NABL", "N/A")                   
                    specimen_type = test.get("specimen_type") or core_test.get("specimen_type", "N/A")
                else:
                    testname      = core_test.get("test_name")
                    department    = core_test.get("department", "N/A")
                    NABL          = core_test.get("NABL", False)
                    critical_range = core_test.get("critical_range", "")
                    interpretation = core_test.get("interpretation", "")
                    labels         = core_test.get("labels", "")
                    lod         = core_test.get("lod", "")
                    specimen_type = test.get("specimen_type") or core_test.get("specimen_type", "N/A")

                outsourced  = test.get("outsourced", False)
                comment     = test.get("comment", "")
                verified_by = test.get("verified_by", "N/A")
                approve_time = test.get("approve_time", "N/A")

                status = None
                if sample_status.exists():
                    for sample_status_record in sample_status:
                        status_details = sample_status_record.testdetails
                        if isinstance(status_details, str):
                            try:
                                status_details = json.loads(status_details)
                            except Exception:
                                status_details = []
                        if isinstance(status_details, list):
                            status = next(
                                (s for s in status_details
                                 if s.get("test_id") == test_id or s.get("testname") == testname),
                                None,
                            )
                        if status:
                            break

                samplecollected_time = status.get("samplecollected_time") if status else None
                received_time        = status.get("received_time")         if status else None

                test_detail = {
                    "test_id":             test_id,
                    "department":          department,
                    "NABL":                NABL,
                    "critical_range":      critical_range,
                    "interpretation":      interpretation,
                    "labels":              labels,
                    "lod":                 lod,
                    "outsourced":          outsourced,
                    "comment":             comment,
                    "testname":            testname,
                    "verified_by":         verified_by,
                    "approve_by":          approve_by,
                    "approve_time":        approve_time,
                    "dispatch_time":       dispatch_time,
                    "samplecollected_time": samplecollected_time,
                    "received_time":       received_time,
                }

                # ── Parameterised test ────────────────────────────────────────
                if parameters and len(parameters) > 0 and core_test:
                    enriched_parameters = []

                    for param_index, param_value in enumerate(parameters):
                        test_code   = param_value.get("test_code")
                        value       = param_value.get("value", "")
                        param_comment = param_value.get("comment", "")

                        param_def = get_parameter_from_core(
                            core_test, device_id,
                            test_code=test_code,
                            param_index=param_index,
                        )

                        if param_def:
                            # Resolve gender-based reference_range only
                            ref_range, _, _ = resolve_gender_fields(param_def, patient_gender)

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
                            }
                        else:
                            # Fallback: no core definition found for this param
                            enriched_param = {
                                "name":            param_value.get("name", "N/A"),
                                "test_code":       test_code,
                                "value":           value,
                                "unit":            param_value.get("unit", "N/A"),
                                "reference_range": param_value.get("reference_range", "N/A"),
                                "method":          param_value.get("method", "N/A"),
                                "specimen_type":   specimen_type,
                                "sub_title":       param_value.get("sub_title", ""),
                                "value_option":    [],
                                "comment":         param_comment,
                            }

                        enriched_parameters.append(enriched_param)

                    test_detail["parameters"] = enriched_parameters

                elif parameters and len(parameters) > 0:
                    # No core_test found — pass raw parameters through unchanged
                    test_detail["parameters"] = parameters

                else:
                    # ── Single-value test (no parameters array) ───────────────
                    if core_test:
                        # Resolve gender-based reference_range only
                        ref_range, _, _ = resolve_gender_fields(core_test, patient_gender)

                        test_detail.update({
                            "method":          core_test.get("method", ""),
                            "specimen_type":   specimen_type,
                            "value":           test.get("value", ""),
                            "unit":            core_test.get("unit", ""),
                            "reference_range": ref_range,   # gender-resolved
                            "sub_title":       test.get("sub_title", ""),
                        })
                    else:
                        test_detail.update({
                            "method":          test.get("method", ""),
                            "specimen_type":   test.get("specimen_type", ""),
                            "value":           test.get("value", ""),
                            "unit":            test.get("unit", ""),
                            "reference_range": test.get("reference_range", ""),
                            "sub_title":       test.get("sub_title", ""),
                        })

                approved_tests.append(test_detail)

            if approved_tests:
                patient_details = {
                    "patient_id":  patient_id,
                    "patientname": patient.patientname if patient else "N/A",
                    "age":         patient.age         if patient else "N/A",
                    "age_type":    patient.age_type    if patient else "Years",
                    "gender":      patient.gender      if patient else "N/A",   # already present — used for H/L in frontend
                    "date":        test_value_record.date,
                    "barcode":     test_value_record.barcode,
                    "bill_no":     bill_no,
                    "barcodes":    barcodes,
                    "testdetails": approved_tests,
                    "refby":       billing.refby   if billing else "N/A",
                    "B2B":         billing.B2B     if billing else False,
                    "branch":      billing.branch  if billing else "N/A",
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

        response_data = {
            "patient_data": all_results[0] if len(all_results) == 1 else all_results,
            "signatures":   signatures_data,
        }

        return JsonResponse(response_data, safe=False)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)  


@csrf_exempt
def send_email(request):
    try:
        subject = request.POST.get('subject', 'No Subject')
        message = request.POST.get('message', 'No Message')
        recipient_list = request.POST.getlist('recipients') or ['shanmugainnovations@gmail.com']
        from_email = request.POST.get('from_email', settings.DEFAULT_FROM_EMAIL)
        signature = (
            "Contact Us,\nShanmuga Hospital,\n24, Saradha College Road,\n"
            "Salem-636007 Tamil Nadu,\n\n6369131631, 0427 270 6666,\n"
            "info@shanmugahospital.com,\nhttps://shanmugahospital.com/"
        )
        files = request.FILES.getlist('attachments')
        if not recipient_list:
            return JsonResponse({'status': 'error', 'message': 'At least one recipient is required to send the email.'}, status=400)
        email = EmailMessage(
            subject=subject,
            body=message + "\n\n" + signature,
            from_email=from_email,
            to=recipient_list,
        )
        for file in files:
            email.attach(file.name, file.read(), file.content_type)
        email.send()
        return JsonResponse({'status': 'success', 'message': 'Email sent successfully!'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)    

@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def send_approval_email(request):
    if request.method == 'POST':
        try:
            print("Received approval email request")
            # Parse JSON request data
            try:
                data = json.loads(request.body.decode('utf-8'))
                test_name = data.get('test_name')
                recipient_email = data.get('recipient_email')
                print(f"Test name from request: {test_name}")
                print(f"Recipient email from request: {recipient_email}")
                if not test_name:
                    print("Error: Test name is missing")
                    return JsonResponse({'error': 'Test name is required'}, status=400)
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                return JsonResponse({'error': 'Invalid JSON'}, status=400)
            # Connect to MongoDB to verify the test exists
            try:
                password = quote_plus('Smrft@2024')
                client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
                db = client.Diagnosttics
                collection = db.core_testdetails
                # Check if test exists and get all test details
                test = collection.find_one({'test_name': test_name})
                if not test:
                    print(f"Test not found: {test_name}")
                    return JsonResponse({'error': 'Test not found'}, status=404)
                # Convert ObjectId to string for JSON serialization if needed
                if '_id' in test:
                    test['_id'] = str(test['_id'])
                print(f"Test found: {test_name}")
            except Exception as mongo_err:
                print(f"MongoDB connection error: {mongo_err}")
                return JsonResponse({'error': f'Database error: {str(mongo_err)}'}, status=500)
            # Generate approval URL

            # For local development, override the URL if needed
            base_url = 'https://shinova.in1.cloudlets.co.in/'
            approval_url = f"{base_url}_b_a_c_k_e_n_d/Diagnostics/approve_test/?test_name={test_name}"
            # Format test details for email
            test_details_str = ""
            for key, value in test.items():
                if key != '_id' and key != 'parameters':
                    test_details_str += f"{key.replace('_', ' ').title()}: {value}\n"
            # Handle parameters separately if they exist and are in JSON format
            if 'parameters' in test:
                try:
                    parameters = json.loads(test['parameters']) if isinstance(test['parameters'], str) else test['parameters']
                    if parameters:
                        test_details_str += "\nParameters:\n"
                        for i, param in enumerate(parameters, 1):
                            test_details_str += f"  Parameter {i}:\n"
                            for param_key, param_value in param.items():
                                test_details_str += f"    {param_key.replace('_', ' ').title()}: {param_value}\n"
                except (json.JSONDecodeError, TypeError):
                    test_details_str += f"\nParameters: {test.get('parameters', 'Not available')}\n"
            # Compose email with HTML for better formatting and button
            subject = f'Approval Request: Test {test_name}'
            # HTML email template with direct approval button - improved for spam prevention
            html_message = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Test Approval Request</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; color: #333333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                    .header {{ background-color: #F5F5F5; padding: 10px; border-radius: 5px; margin-bottom: 20px; }}
                    .test-details {{ white-space: pre-line; margin-bottom: 20px; }}
                    .button {{ display: inline-block; padding: 10px 20px; background-color: #4CAF50; color: white;
                               text-decoration: none; border-radius: 5px; font-weight: bold; }}
                    .footer {{ font-size: 12px; color: #666; margin-top: 30px; border-top: 1px solid #ddd; padding-top: 10px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h2>Lab Test Approval Request</h2>
                    </div>
                    <p>Hello,</p>
                    <p>A new lab test has been submitted and requires your approval. Here are the details:</p>
                    <div class="test-details">
                        {test_details_str}
                    </div>
                    <p>To approve this test, please click the button below:</p>
                    <p><a href="{approval_url}" class="button">Approve Test</a></p>
                    <div class="footer">
                        <p>This is an automated message from Shanmuga Diagnostics Laboratory System. If you did not request this approval, please ignore this email.</p>
                        <p>© 2025 Shanmuga Diagnostics. All rights reserved.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            # Plain text version for email clients that don't support HTML
            plain_message = f"""
            Lab Test Approval Request
            Hello,
            A new lab test has been submitted and requires your approval. Here are the details:
            {test_details_str}
            To approve this test, please click on the following link:
            {approval_url}
            This is an automated message from Shanmuga Diagnostics System. If you did not request this approval, please ignore this email.
            © 2025 Shanmuga Diagnostics. All rights reserved.
            """
            # Create the recipient list
            # Use provided email if available, otherwise use default
            recipient_list = []
            if recipient_email:
                recipient_list.append(recipient_email)

            # Always include default emails
            default_emails = ['drprabusankar@smrft.org', 'drpriya@smrft.org']
            for email in default_emails:
                if email not in recipient_list:
                    recipient_list.append(email)

            # Send email using smtplib directly for more control
            try:
                print(f"Sending email to: {recipient_list}")
                import smtplib
                from email.mime.multipart import MIMEMultipart
                from email.mime.text import MIMEText
                from email.utils import formatdate, make_msgid
                # Set up the SMTP server
                smtp_server = "smtp.gmail.com"
                smtp_port = 587
                smtp_username = settings.EMAIL_HOST_USER
                smtp_password = settings.EMAIL_HOST_PASSWORD  # Make sure this is an app password if using Gmail
                # Create message container
                msg = MIMEMultipart('alternative')
                msg['Subject'] = subject
                msg['From'] = f"Shanmuga Diagnostics<{smtp_username}>"
                msg['To'] = ", ".join(recipient_list)
                msg['Date'] = formatdate(localtime=True)
                msg['Message-ID'] = make_msgid(domain='shinovadatabase.in')
                # Add custom headers to reduce chance of being marked as spam
                msg.add_header('X-Priority', '1')  # 1 = High priority
                msg.add_header('X-MSMail-Priority', 'High')
                msg.add_header('Importance', 'High')
                msg.add_header('X-Mailer', 'Shanmuga Diagnostics Approval System')
                # Record-Route might help with deliverability
                msg.add_header('Return-Path', smtp_username)
                # Attach parts
                part1 = MIMEText(plain_message, 'plain')
                part2 = MIMEText(html_message, 'html')
                msg.attach(part1)
                msg.attach(part2)
                # Create SMTP session
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_username, smtp_password)
                # Send email
                server.sendmail(smtp_username, recipient_list, msg.as_string())
                server.quit()
                print("Email sent successfully using direct SMTP")
                return JsonResponse({'message': 'Approval email sent successfully'}, status=200)
            except Exception as email_err:
                print(f"Email sending error: {email_err}")
                return JsonResponse({'error': f'Email sending failed: {str(email_err)}'}, status=500)
        except Exception as e:
            print(f"General error sending approval email: {e}")
            return JsonResponse({'error': str(e)}, status=500)
    print("Invalid request method for send_approval_email")
    return JsonResponse({'error': 'Invalid request method'}, status=405)



@api_view(['PATCH'])
@permission_classes([HasRoleAndDataPermission])
def update_dispatch_status(request, barcode):
    """
    Update dispatch status for a specific test in core_testvalue collection.
    Uses barcode, test_id, and created_date for accurate targeting.
    """
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
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
        client.close()

        
@api_view(['GET'])
@csrf_exempt
def b2b_ledger_report(request):
    try:
        from_date = request.GET.get('from_date')
        to_date = request.GET.get('to_date')
        b2b_name = request.GET.get('b2b_name')

        print(f"b2b_ledger_report: from={from_date}, to={to_date}, b2b={b2b_name}")

        query = {}
        if from_date and to_date:
            try:
                start_date = datetime.strptime(from_date, "%Y-%m-%d")
                end_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
                query['date__range'] = [start_date, end_date]
            except ValueError:
                return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        
        if b2b_name and b2b_name != "All":
            query['B2B'] = b2b_name

        # Construct QuerySet
        billings_qs = Billing.objects.filter(**query)

        # Exclude empty B2B if showing "All" (implied by not having b2b_name or b2b_name == "All" if we handled it above)
        # Note: If b2b_name is None, we didn't add it to query.
        if not b2b_name or b2b_name == "All":
             # Use exclude instead of regex for better compatibility
            billings_qs = billings_qs.exclude(B2B__isnull=True).exclude(B2B__exact='')

        billings = billings_qs.order_by('date')
        
        data = []
        for bill in billings:
            try:
                total = float(bill.totalAmount) if bill.totalAmount else 0
                net = float(bill.netAmount) if bill.netAmount else 0
                discount = float(bill.discount) if bill.discount else 0
            except ValueError:
                total, net, discount = 0, 0, 0

            # Safely get ID
            bill_id = getattr(bill, 'id', str(bill.pk))

            data.append({
                "id": bill_id,
                "date": bill.date.strftime("%Y-%m-%d") if bill.date else "N/A",
                "bill_no": bill.bill_no,
                "patient_name": bill.patientname,
                "b2b_name": bill.B2B,
                "total_amount": total,
                "net_amount": net,
                "discount": discount
            })
            
        return JsonResponse({"success": True, "data": data})
            
    except Exception as e:
        print(f"Error in b2b_ledger_report: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({"success": False, "error": f"{str(e)} | {traceback.format_exc()}"}, status=500)


@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_home_collection_report(request):
    """
    Get report specifically for Home Collection samples.
    Filters by Date and Segment = 'Home Collection'.
    Params are expected in the POST body.
    """
    try:
        data = request.data
        from_date = data.get('from_date')
        to_date = data.get('to_date')

        if not from_date or not to_date:
             return JsonResponse({"error": "Date range (from_date, to_date) is required"}, status=400)

        try:
            from_dt = datetime.strptime(from_date, '%Y-%m-%d')
            to_dt = datetime.strptime(to_date, '%Y-%m-%d') + timedelta(days=1) # Include full end date
        except ValueError:
             return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        # MongoDB setup (using same logic as overall_report)
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        billing_collection = db["core_billing"]

        # Query for Home Collection in given date range
        query = {
            "date": {"$gte": from_dt, "$lt": to_dt},
            "segment": "Home Collection"
        }

        records = list(billing_collection.find(query))
        
        # If no records found with explicit segment, check for 'Walk-in' with a sample_collector assigned, 
        # as sometimes logic might differ, but user explicitly asked for "Home Collection Details".
        # We will stick to segment="Home Collection" primarily. 
        
        # Fetch Patient model details using patient_id from billing records
        patient_ids = [r.get('patient_id') for r in records if r.get('patient_id')]
        patient_map = {}
        if patient_ids:
            try:
                patient_records = Patient.objects.filter(patient_id__in=patient_ids).values(
                    'patient_id', 'patientname', 'age', 'age_type', 'gender', 'phone', 'address'
                )
                patient_map = {p['patient_id']: p for p in patient_records}
            except Exception as e:
                print(f"Error fetching Patient model details: {e}")

        report_data = []
        for record in records:
            bill_no = record.get("bill_no", "N/A")
            pid = record.get("patient_id")
            
            p_details = patient_map.get(pid, {})
            
            # Helper to join address parts - Prioritize Patient Model -> Billing Record
            address_source = p_details.get("address") or record.get("address")
            formatted_address = "N/A"
            
            if address_source:
                if isinstance(address_source, str):
                    try:
                        addr_json = json.loads(address_source)
                        if isinstance(addr_json, dict):
                             parts = [str(v) for k, v in addr_json.items() if v]
                             formatted_address = ", ".join(parts)
                        else:
                            formatted_address = address_source
                    except:
                        formatted_address = address_source
                elif isinstance(address_source, dict):
                    parts = [str(v) for k, v in address_source.items() if v]
                    formatted_address = ", ".join(parts)

            # Patient Name priority: Patient Model > Billing
            patient_name = p_details.get("patientname") or record.get("patientname") or "N/A"
            
            # Phone priority: Patient Model > Billing
            phone = p_details.get("phone") or record.get("phone") or "N/A"

            # Parse Test Names
            test_names = record.get("test_names", "")
            if not test_names:
                # Fallback to testdetails parsing similar to overall_report (simplified)
                td = record.get("testdetails")
                if isinstance(td, str):
                    try:
                        parsed = json.loads(td)
                        test_names = ", ".join([t.get("testname", "") for t in parsed if isinstance(t, dict)])
                    except:
                        pass
                elif isinstance(td, list):
                    test_names = ", ".join([t.get("testname", "") for t in td if isinstance(t, dict)])


            item = {
                "date": record.get("date").strftime("%Y-%m-%d") if record.get("date") else "N/A",
                "bill_no": bill_no,
                "patient_id": pid or "N/A",
                "patient_name": patient_name,
                "phone": phone,
                "address": formatted_address,
                "sample_collector": get_employee_name(record.get("sample_collector", "")) or "N/A",
                "test_names": test_names,
                "total_amount": record.get("totalAmount", 0),
                "paid_amount": record.get("paid_amount", 0),
                "balance_amount": record.get("balance_amount", 0),
                "payment_status": record.get("payment_status", "N/A"),
                "status": record.get("status", "N/A"),
                "remarks": record.get("remarks", "")
            }
            report_data.append(item)

        return JsonResponse({"data": report_data, "count": len(report_data)}, safe=False)

    except Exception as e:
        print(f"Error in get_home_collection_report: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)
