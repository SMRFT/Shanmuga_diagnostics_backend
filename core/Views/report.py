from rest_framework.response import Response
from django.http import JsonResponse 
from datetime import datetime
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from urllib.parse import quote_plus
from pymongo import MongoClient
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta
from collections import defaultdict
from django.utils import timezone  # Import Django's timezone module
import re
from django.core.mail import EmailMessage
from django.conf import settings 
from django.utils.timezone import make_aware
from datetime import datetime, date  
from rest_framework.views import APIView
import traceback
import json
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ..models import Patient
from ..models import SampleStatus,Billing
from ..models import TestValue
from ..models import BarcodeTestDetails
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ..models import SampleStatus, TestValue
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.core.mail import EmailMessage
from django.core.mail import EmailMessage
import os
from dotenv import load_dotenv
import pytz
load_dotenv()


@api_view(['GET', 'PATCH'])
@csrf_exempt
def overall_report(request):
    try:
        # MongoDB setup for core_billing        
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        billing_collection = db["core_billing"]

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

        # Fetch barcode from BarcodeTestDetails
        bill_nos = [record['bill_no'] for record in billing_records if record['bill_no']]
        barcode_query = {"bill_no__in": bill_nos} if bill_nos else {}
        print(f"BarcodeTestDetails query: {barcode_query}")
        barcode_records = BarcodeTestDetails.objects.filter(**barcode_query).values(
            'patient_id', 'patientname', 'age', 'gender', 'segment', 'date', 'bill_no', 'barcode', 'testdetails'
        )
        barcode_map = {record['bill_no']: record for record in barcode_records}
        print(f"Found {len(barcode_records)} BarcodeTestDetails records")
        if barcode_records:
            print("Sample BarcodeTestDetails record:", barcode_records[0])

        # Fetch patient details from Patient model
        patient_ids = [record['patient_id'] for record in billing_records]
        patient_details_map = {}
        try:
            patient_records = Patient.objects.filter(patient_id__in=patient_ids).values(
                'patient_id', 'patientname', 'age', 'age_type', 'gender', 'phone', 'email', 'address', 'created_date'
            )
            for patient_record in patient_records:
                patient_details_map[patient_record['patient_id']] = patient_record
            print(f"Fetched {len(patient_details_map)} patient records from Patient model")
        except Exception as e:
            print(f"Error fetching patient details from Patient model: {str(e)}")

        # Fetch status and test data
        barcodes = [record['barcode'] for record in barcode_records if record['barcode']]
        print(f"Barcodes for querying: {barcodes}")
        sample_status_records = SampleStatus.objects.filter(
            barcode__in=barcodes,
            date__range=(make_aware(from_date), make_aware(to_date))
        ).values("barcode", "testdetails")
        print(f"Fetched {len(sample_status_records)} SampleStatus records")

        test_value_records = TestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(make_aware(from_date), make_aware(to_date))
        ).values("barcode", "testdetails", "created_date")
        print(f"Fetched {len(test_value_records)} TestValue records")

        # Check all TestValue records for barcode 000005
        if "000005" in barcodes:
            all_test_value_records = TestValue.objects.filter(barcode="000005").values("barcode", "testdetails", "created_date")
            print(f"All TestValue records for barcode 000005: {list(all_test_value_records)}")

        # Organize status data
        sample_status_map = {}
        for record in sample_status_records:
            sample_status_map.setdefault(record["barcode"], []).extend(record["testdetails"])

        # FIXED: Organize test value data - COMBINE ALL RECORDS FOR SAME BARCODE
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
            sample_collector = record.get("sample_collector", "N/A")
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

            # Test list (prefer test_names from billing record)
            test_list = []
            test_names_str = record.get("test_names", "")
            if test_names_str:
                print(f"Using test_names for barcode {barcode_data.get('barcode')}: {test_names_str}")
                test_list = [{"testname": name.strip()} for name in test_names_str.split(",") if name.strip()]
            else:
                test_field = barcode_data.get("testdetails", []) or record.get("testdetails", [])
                print(f"Barcode {barcode_data.get('barcode')}: test_field source: {'barcode_data' if barcode_data.get('testdetails') else 'billing_record'}")
                if isinstance(test_field, str):
                    try:
                        test_list = json.loads(test_field.strip('"'))
                    except json.JSONDecodeError as e:
                        print(f"Error parsing test_field for barcode {barcode_data.get('barcode')}: {e}")
                        test_list = []
                elif isinstance(test_field, list):
                    test_list = test_field

            testnames = ", ".join([test.get("testname", "") for test in test_list])
            no_of_tests = len(test_list) or record.get("no_of_tests", 0)

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

            # Get combined test value data
            latest_test_data = test_value_map.get(barcode, {}) if barcode else {}
            all_test_values = latest_test_data.get("testdetails", [])
            test_created_date = latest_test_data.get("created_date", None)

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
            if unapproved_tests:
                print(f"Unapproved tests for barcode {barcode}: {unapproved_tests}")
            if test_created_date:
                print(f"Test created date: {test_created_date}")

            # Sample collection status
            all_collected = all(t.get("samplestatus") == "Sample Collected" for t in sample_tests) if sample_tests else False
            partially_collected = any(t.get("samplestatus") == "Sample Collected" for t in sample_tests)
            all_received = all(t.get("samplestatus") == "Received" for t in sample_tests) if sample_tests else False
            partially_received = any(t.get("samplestatus") == "Received" for t in sample_tests)

            if all_collected:
                status = "Collected"
            elif partially_collected:
                status = "Partially Collected"

            if all_received:
                status = "Received"
            elif partially_received:
                status = "Partially Received"

            # FIXED: Test value status logic
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
               
                # Normalize test names for comparison
                def normalize_test_name(name):
                    if not name:
                        return ""
                    name = re.sub(r'\s+', ' ', name.strip())
                    name = name.lower()
                    name = re.sub(r'[^\w\s-]', '', name)
                    name = name.split('[')[0].strip()
                    return name

                # Get test names from billing record
                all_ordered_tests = {normalize_test_name(test.get("testname", "")) for test in test_list}
               
                # Get approved test names from ALL test value records
                approved_test_names = {normalize_test_name(t.get("testname", "")) for t in valid_test_values if t.get("approve", False)}
               
                # Debug logging
                if barcode == "000005":
                    print(f"=== DEBUG FOR BARCODE 000005 ===")
                    print(f"All ordered tests: {all_ordered_tests}")
                    print(f"Approved test names: {approved_test_names}")
                    print(f"Number of ordered tests: {len(all_ordered_tests)}")
                    print(f"Number of approved tests: {len(approved_test_names)}")
                    print(f"Valid test values count: {len(valid_test_values)}")
                    print(f"All approved individual checks: {[t.get('approve', False) for t in valid_test_values]}")

                # Check approval status
                all_approved = False
                partially_approved = False
               
                if len(all_ordered_tests) > 0:
                    # Method 1: Compare test names
                    if all_ordered_tests.issubset(approved_test_names) and len(approved_test_names) == len(all_ordered_tests):
                        all_approved = True
                    elif len(approved_test_names) > 0:
                        partially_approved = True
                   
                    # Method 2: Fallback - check if all individual tests are approved
                    if not all_approved and valid_test_values:
                        approved_count = sum(1 for t in valid_test_values if t.get("approve", False))
                        total_expected = record.get("no_of_tests", 0)
                       
                        if approved_count == total_expected and approved_count > 0:
                            all_approved = True
                            partially_approved = False
                        elif approved_count > 0:
                            partially_approved = True
               
                # Check dispatch status
                approved_tests = [t for t in valid_test_values if t.get("approve", False)]
                all_dispatched = all(t.get("dispatch", False) for t in approved_tests) if approved_tests else False
               
                print(f"Approval status for {barcode}: all_approved={all_approved}, partially_approved={partially_approved}")
                print(f"Testing status: all_tested={all_tested}, partially_tested={partially_tested}")
                print(f"Dispatch status: all_dispatched={all_dispatched}")

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
                if all_dispatched and approved_tests:
                    status = "Dispatched"

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
                "credit_details": record.get("credit_details", []),
                "discount": discount,
                "payment_method": payment_details,
                "test_names": testnames,
                "no_of_tests": no_of_tests,
                "bill_no": bill_no,
                "registeredby": registeredby,
                "barcode": barcode,
                "status": status,
                "test_created_date": test_created_date_formatted,
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
        # Change from patient_id to barcode
        barcode = request.GET.get('barcode')
        date = request.GET.get('date', datetime.now().strftime("%Y-%m-%d"))
        
        if not barcode:
            return JsonResponse({'error': 'Missing barcode'}, status=400)
        
        # Ensure the date is in YYYY-MM-DD format
        try:
            formatted_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)
        
        # Filter test values by barcode instead of patient_id
        tests = TestValue.objects.filter(barcode=barcode, date=formatted_date).values("testdetails")
        test_list = []
        
        for test in tests:
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
            
            # Filter only approved tests
            approved_tests = [test_item for test_item in testdetails_list if test_item.get('approve') is True]
            test_list.extend(approved_tests)
        
        # Return barcode as key instead of patient_id for consistency
        if test_list:
            return JsonResponse({barcode: {"testdetails": test_list}})
        else:
            return JsonResponse({'error': 'No records found for this barcode'}, status=404)
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_patient_test_details(request):
    barcode = request.GET.get('barcode')
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
        test_values = TestValue.objects.filter( barcode=barcode)
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
        all_results = []
        # Process each TestValue record
        for test_value_record in test_values:
            # Filter for approved tests only
            approved_tests = []
            for test in test_value_record.testdetails:
                # Check if the test is approved
                if test.get("approve") == True:  # Only include approved tests
                    testname = test.get("testname")
                    department = test.get("department", "N/A")
                    NABL = test.get("NABL", "N/A")
                    verified_by = test.get("verified_by", "N/A")
                    approve_by = test.get("approve_by", "N/A")
                    approve_time = test.get("approve_time", "N/A")
                    parameters = test.get("parameters", [])
                    # Get sample status information
                    status = None
                    if sample_status.exists():
                        for sample_status_record in sample_status:
                            status = next(
                                (status for status in sample_status_record.testdetails
                                 if status.get("testname") == testname), None)
                            if status:
                                break
                    samplecollected_time = status.get("samplecollected_time") if status else None
                    received_time = status.get("received_time") if status else None
                    test_detail = {
                        "department": department,
                        "NABL": NABL,
                        "testname": testname,
                        "verified_by": verified_by,
                        "approve_by": approve_by,
                        "approve_time": approve_time,
                        "samplecollected_time": samplecollected_time,
                        "received_time": received_time
                    }
                    if parameters:
                        test_detail["parameters"] = parameters
                    else:
                        test_detail.update({
                            "method": test.get("method", ""),
                            "specimen_type": test.get("specimen_type", ""),
                            "value": test.get("value", ""),
                            "unit": test.get("unit", ""),
                            "reference_range": test.get("reference_range", "")
                        })
                    approved_tests.append(test_detail)
            # Only add patient details if there are approved tests
            if approved_tests:
                patient_details = {
                    "patient_id": patient_id,
                    "patientname": patient.patientname,
                    "age": patient.age,
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
        # If only one result, return it directly; otherwise return array
        if len(all_results) == 1:
            return JsonResponse(all_results[0], safe=False)
        else:
            return JsonResponse(all_results, safe=False)
    except Exception as e:
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

# Define IST timezone
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)

@api_view(['PATCH'])
@permission_classes([HasRoleAndDataPermission])
def update_dispatch_status(request, barcode):
    # MongoDB connection
    password = quote_plus('Smrft@2024')
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics  # Database name
    collection = db.core_testvalue
    
    try:
        # Get created_date from query parameters or request body
        created_date = request.query_params.get('created_date') or request.data.get('created_date')
        
        # Get auth-user-id from request data
        auth_user_id = request.data.get('auth-user-id')
        
        if not created_date:
            return Response({"error": "created_date parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        if not auth_user_id:
            return Response({"error": "auth-user-id parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Parse the created_date string to datetime object for comparison
        try:
            # Assuming created_date is passed as ISO string (e.g., "2025-09-04T04:57:30.581Z")
            created_date_obj = datetime.fromisoformat(created_date.replace('Z', '+00:00'))
        except ValueError:
            try:
                # Try parsing as date only (e.g., "2025-09-04")
                created_date_obj = datetime.strptime(created_date, '%Y-%m-%d')
            except ValueError:
                return Response({"error": "Invalid date format. Use ISO format or YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Build the query filter with barcode and created_date
        query_filter = {
            "barcode": barcode,
            "created_date": created_date_obj  # Direct match with the exact created_date
        }
        
        # Alternative: If you need date range filtering, use this instead:
        # start_of_day = created_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
        # end_of_day = created_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
        # query_filter["created_date"] = {
        #     "$gte": start_of_day,
        #     "$lte": end_of_day
        # }
        
        # Find the document with both barcode and created_date filters
        test_value_record = collection.find_one(query_filter)
        
        if not test_value_record:
            return Response({
                "error": f"TestValue record not found for barcode: {barcode} and created_date: {created_date}"
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Parse the testdetails field (convert JSON string to a Python list)
        test_details = json.loads(test_value_record.get("testdetails", "[]"))
        
        # Update dispatch status to true for all tests
        for test in test_details:
            test["dispatch"] = True
            # Only set dispatch_time if dispatch is True
            if test.get("dispatch", False):
                test["dispatch_time"] = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')  # Convert to IST format
        
        # Convert the updated testdetails back to a JSON string
        updated_test_details = json.dumps(test_details)
        
        # Update the document in MongoDB using the same filter
        result = collection.update_one(
            query_filter,  # Use the same filter for update
            {"$set": {
                "testdetails": updated_test_details,
                "lastmodified_by": auth_user_id,  # Use auth-user-id from request data
                "lastmodified_date": datetime.now(IST)
            }}
        )
        
        if result.matched_count == 0:
            return Response({"error": "Failed to update dispatch status"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        return Response({
            "message": "Dispatch status updated successfully.",
            "barcode": barcode,
            "created_date": created_date,
            "updated_tests": len(test_details),
            "modified_by": auth_user_id
        }, status=status.HTTP_200_OK)
    
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        client.close()