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
from ...models import Hmssamplestatus,HmspatientBilling
from ...models import TestValue
from ...models import Hmsbarcode
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ...models import SampleStatus, TestValue
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.core.mail import EmailMessage
import os
from dotenv import load_dotenv
import pytz
load_dotenv()


@api_view(['GET', 'PATCH'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def hms_overall_report(request):
    try:
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

        # Query Hmsbarcode as the primary source
        barcode_query = {"date__gte": from_date, "date__lt": to_date}
        if patient_id:
            barcode_query["patient_id"] = patient_id
        print(f"HMS Barcode query: {barcode_query}")
        
        barcode_records = list(Hmsbarcode.objects.filter(**barcode_query).values(
            'billnumber', 'barcode', 'date', 'testdetails',
            'patient_id', 'patientname', 'age', 'age_type', 'gender', 
            'IPOPType', 'ref_doctor', 'ipnumber', 'location_id'
        ))
        print(f"Found {len(barcode_records)} HMS barcode records")
        if barcode_records:
            print("Sample HMS barcode record:", barcode_records[0])

        if not barcode_records:
            return JsonResponse([], safe=False)

        # Fetch status and test data
        barcodes = [record['barcode'] for record in barcode_records if record['barcode']]
        print(f"Barcodes for querying: {barcodes}")
        
        sample_status_records = Hmssamplestatus.objects.filter(
            barcode__in=barcodes,
            date__range=(make_aware(from_date), make_aware(to_date))
        ).values("barcode", "testdetails")
        print(f"Fetched {len(sample_status_records)} HMS Sample Status records")

        test_value_records = TestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(from_date.date(), to_date.date())
        ).values("barcode", "testdetails", "created_date")
        print(f"Fetched {len(test_value_records)} TestValue records")

        # Organize status data
        sample_status_map = {}
        for record in sample_status_records:
            sample_status_map.setdefault(record["barcode"], []).extend(record["testdetails"] or [])

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

        # Format response
        formatted_data = []
        for record in barcode_records:
            billnumber = record.get("billnumber", "N/A")
            barcode = record.get("barcode", "N/A")

            # Patient details from Hmsbarcode only
            patient_data = {
                "patient_id": record.get("patient_id", "N/A"),
                "patientname": record.get("patientname", "N/A"),
                "age": record.get("age", "N/A"),
                "age_type": record.get("age_type", ""),
                "gender": record.get("gender", "N/A"),
                "ipnumber": record.get("ipnumber", "N/A")
            }

            # Get opiptype and ref_doctor from Hmsbarcode only
            opiptype = record.get("IPOPType", "N/A")
            refby = record.get("ref_doctor", "N/A")
            branch = record.get("location_id", "N/A")

            # Test list from HMS billing record
            test_list = []
            test_field = record.get("testdetails", [])
            if isinstance(test_field, str):
                try:
                    test_list = json.loads(test_field.strip('"'))
                except json.JSONDecodeError as e:
                    print(f"Error parsing test_field for billnumber {billnumber}: {e}")
                    test_list = []
            elif isinstance(test_field, list):
                test_list = test_field

            testnames = ", ".join([test.get("testname", "") for test in test_list if isinstance(test, dict)])
            no_of_tests = len(test_list)

            # STATUS DETERMINATION (using test_id instead of test_name)
            status = "Registered"  # Default status
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

            # Test value status logic (using test_id for comparison)
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
               
                # Get test_ids from billing record
                all_ordered_test_ids = {
                    str(test.get("test_id", "")).strip() 
                    for test in test_list 
                    if isinstance(test, dict) and test.get("test_id")
                }
               
                # Get approved test_ids from ALL test value records
                approved_test_ids = {
                    str(t.get("test_id", "")).strip() 
                    for t in valid_test_values 
                    if t.get("approve", False) and t.get("test_id")
                }
               
                # Check approval status based on test_id
                all_approved = False
                partially_approved = False
               
                if len(all_ordered_test_ids) > 0:
                    # Compare test IDs
                    if all_ordered_test_ids.issubset(approved_test_ids) and len(approved_test_ids) == len(all_ordered_test_ids):
                        all_approved = True
                    elif len(approved_test_ids) > 0:
                        partially_approved = True
                   
                    # Fallback - check if all individual tests are approved
                    if not all_approved and valid_test_values:
                        approved_count = sum(1 for t in valid_test_values if t.get("approve", False))
                        total_expected = no_of_tests
                       
                        if approved_count == total_expected and approved_count > 0:
                            all_approved = True
                            partially_approved = False
                        elif approved_count > 0:
                            partially_approved = True
               
                # Check dispatch status
                approved_tests = [t for t in valid_test_values if t.get("approve", False)]
                all_dispatched = all(t.get("dispatch", False) for t in approved_tests) if approved_tests else False
               
                print(f"Approval status for {barcode}: all_approved={all_approved}, partially_approved={partially_approved}")
                print(f"Ordered test IDs: {all_ordered_test_ids}, Approved test IDs: {approved_test_ids}")
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
            registration_date = record.get("date", formatted_date)
            if isinstance(registration_date, datetime):
                registration_date = registration_date.isoformat()

            test_created_date_formatted = None
            if test_created_date:
                if isinstance(test_created_date, datetime):
                    test_created_date_formatted = test_created_date.isoformat()
                else:
                    test_created_date_formatted = str(test_created_date)

            formatted_data.append({
                "date": formatted_date,
                "registration_date": registration_date,
                "patient_id": patient_data["patient_id"],
                "patient_name": patient_data["patientname"],
                "gender": patient_data["gender"],
                "age": f"{patient_data['age']} {patient_data['age_type']}",
                "ipnumber": patient_data["ipnumber"],
                "opiptype": opiptype,
                "refby": refby,
                "branch": branch,
                "test_names": testnames,
                "no_of_tests": no_of_tests,
                "billnumber": billnumber,
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
@permission_classes([HasRoleAndDataPermission])
def get_hms_patient_test_details(request):
    barcode = request.GET.get('barcode')
    
    # Check if barcode is provided
    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)
    
    try:
        # Get TestValue records using barcode
        test_values = TestValue.objects.filter(barcode=barcode)
        if not test_values.exists():
            return JsonResponse({'error': 'No test records found for the given barcode'}, status=404)
        
        # Get patient details from Hmsbarcode using barcode
        barcode_details = Hmsbarcode.objects.filter(barcode=barcode).first()
        if not barcode_details:
            return JsonResponse({'error': 'No patient details found for the given barcode'}, status=404)
        
        # Get sample status from Hmssamplestatus using barcode
        sample_status = Hmssamplestatus.objects.filter(barcode=barcode)
   
        mongo_client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        mongo_db = mongo_client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails
        
        # Helper function to get parameter details by index or test_code
        def get_parameter_from_core(core_test, device_id, test_code=None, param_index=None):
            """
            Get parameter details from core_testdetails
            Supports both dict (device_id-keyed) and list formats
            Uses param_index for accurate matching when available
            """
            core_parameters = core_test.get("parameters", {})
            params_list = []
            
            # Case 1: parameters is a dictionary with device_id keys
            if isinstance(core_parameters, dict):
                # Try to get parameters for the specific device_id
                if device_id and device_id != "N/A" and device_id in core_parameters:
                    params_list = core_parameters[device_id]
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
        
        all_results = []
        
        # Process each TestValue record
        for test_value_record in test_values:
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
                if test.get("approve") == True:
                    test_id = test.get("test_id")
                    device_id = test.get("device_id")
                    parameters = test.get("parameters", [])
                    
                    # Fetch test details from core_testdetails
                    core_test = core_testdetails_collection.find_one({"test_id": test_id})
                    
                    if not core_test:
                        continue
                    
                    testname = core_test.get("test_name")
                    department = core_test.get("department", "N/A")
                    NABL = core_test.get("NABL", False)
                    outsourced = test.get("outsourced", False)
                    comment = test.get("comment", "")
                    verified_by = test.get("verified_by", "N/A")
                    approve_by = test.get("approve_by", "N/A")
                    approve_time = test.get("approve_time", "N/A")
                    specimen_type = core_test.get("specimen_type", "N/A")
                    
                    # Get sample status information
                    status = None
                    if sample_status.exists():
                        for sample_status_record in sample_status:
                            status_details_list = sample_status_record.testdetails
                            if isinstance(status_details_list, str):
                                try:
                                    status_details_list = json.loads(status_details_list)
                                except:
                                    status_details_list = []
                            
                            if isinstance(status_details_list, list):
                                status = next(
                                    (s for s in status_details_list if s.get("test_id") == test_id),
                                    None
                                )
                                if status:
                                    break
                    
                    samplecollected_time = status.get("samplecollected_time") if status else None
                    received_time = status.get("received_time") if status else None
                    
                    test_detail = {
                        "department": department,
                        "NABL": NABL,
                        "outsourced": outsourced,
                        "comment": comment,
                        "testname": testname,
                        "verified_by": verified_by,
                        "approve_by": approve_by,
                        "approve_time": approve_time,
                        "samplecollected_time": samplecollected_time,
                        "received_time": received_time,
                    }
                    
                    # Handle parameters - match with core_testdetails using INDEX
                    if parameters and len(parameters) > 0:
                        enriched_parameters = []
                        
                        # Use index-based matching for accurate parameter retrieval
                        for param_index, param_value in enumerate(parameters):
                            test_code = param_value.get("test_code")
                            value = param_value.get("value", "")
                            param_comment = param_value.get("comment", "")
                            
                            # Get parameter definition using INDEX (most accurate)
                            param_def = get_parameter_from_core(
                                core_test, 
                                device_id, 
                                test_code=test_code, 
                                param_index=param_index
                            )
                            
                            if param_def:
                                enriched_param = {
                                    "name": param_def.get("test_name", ""),
                                    "test_code": test_code,
                                    "value": value,
                                    "unit": param_def.get("unit", ""),
                                    "reference_range": param_def.get("reference_range", ""),
                                    "method": param_def.get("method", ""),
                                    "specimen_type": specimen_type,
                                    "sub_title": param_def.get("sub_title", ""),
                                    "value_option": param_def.get("value_option", []),
                                    "comment": param_comment
                                }
                                enriched_parameters.append(enriched_param)
                            else:
                                # Fallback if parameter definition not found
                                enriched_param = {
                                    "name": "N/A",
                                    "test_code": test_code,
                                    "value": value,
                                    "unit": "N/A",
                                    "reference_range": "N/A",
                                    "method": "N/A",
                                    "specimen_type": specimen_type,
                                    "sub_title": "",
                                    "value_option": [],
                                    "comment": param_comment
                                }
                                enriched_parameters.append(enriched_param)
                        
                        test_detail["parameters"] = enriched_parameters
                    else:
                        # No parameters - single test with value
                        test_detail.update({
                            "method": core_test.get("method", ""),
                            "value": test.get("value", ""),
                            "unit": core_test.get("unit", ""),
                            "reference_range": core_test.get("reference_range", ""),
                            "sub_title": test.get("sub_title", "")
                        })
                    
                    approved_tests.append(test_detail)
            
            # Only add patient details if there are approved tests
            if approved_tests:
                patient_details = {
                    "patient_id": barcode_details.patient_id,
                    "patientname": barcode_details.patientname,
                    "age": barcode_details.age,
                    "age_type": barcode_details.age_type if hasattr(barcode_details, 'age_type') else "Years",
                    "gender": barcode_details.gender,
                    "date": test_value_record.date,
                    "barcode": test_value_record.barcode,
                    "bill_no": barcode_details.billnumber,
                    "barcodes": [barcode],  # Just the current barcode
                    "testdetails": approved_tests,
                    "refby": barcode_details.ref_doctor if hasattr(barcode_details, 'ref_doctor') else "SELF",
                    "branch": barcode_details.location_id if hasattr(barcode_details, 'location_id') else "N/A",
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
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)   

@csrf_exempt
def hms_send_email(request):
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
def hms_send_approval_email(request):
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
def hms_update_dispatch_status(request, barcode):
    """
    Update dispatch status for tests in core_testvalue collection.
    Uses test_id instead of testname for accurate tracking.
    """
    # MongoDB connection
    password = quote_plus('Smrft@2024')
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics  # Database name
    collection = db.core_testvalue
    
    try:
        # Get auth-user-id from request data
        auth_user_id = request.data.get('auth-user-id')
        auth_user_name = request.data.get('auth-user-name')
        
        if not auth_user_id:
            return Response(
                {"error": "auth-user-id parameter is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Build the query filter with only barcode
        query_filter = {
            "barcode": barcode
        }
        
        # Find ALL documents with the same barcode, sorted by created_date descending (latest first)
        test_value_records = list(collection.find(query_filter).sort("created_date", -1))
        
        if not test_value_records:
            return Response({
                "error": f"No TestValue records found for barcode: {barcode}"
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Dictionary to track the latest document for each test_id
        latest_documents_by_test_id = {}
        
        # Process each document to find the latest one for each test_id
        for record in test_value_records:
            # Parse the testdetails field
            test_details = json.loads(record.get("testdetails", "[]"))
            
            for test in test_details:
                test_id = test.get("test_id")
                
                # Skip if test_id is missing
                if not test_id:
                    continue
                
                # If this test_id hasn't been seen yet, or this document is newer
                if test_id not in latest_documents_by_test_id:
                    latest_documents_by_test_id[test_id] = {
                        "document": record,
                        "test_details": test_details,
                        "created_date": record.get("created_date"),
                        "testname": test.get("testname", "Unknown")
                    }
                # Since records are sorted by created_date descending,
                # the first occurrence is the latest
        
        updated_count = 0
        total_tests_updated = 0
        updated_records = []
        
        # Update dispatch status for the latest document of each test_id
        for test_id, doc_info in latest_documents_by_test_id.items():
            document = doc_info["document"]
            test_details = doc_info["test_details"]
            tests_updated_in_doc = 0
            
            # Update dispatch status for all tests in this document
            for test in test_details:
                # Only update if dispatch is currently false
                if not test.get("dispatch", False):
                    test["dispatch"] = True
                    test["dispatched_by"] = auth_user_name
                    test["dispatch_time"] = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
                    tests_updated_in_doc += 1
            
            # Convert the updated testdetails back to a JSON string
            updated_test_details = json.dumps(test_details)
            
            # Update the document in MongoDB using the document's _id
            result = collection.update_one(
                {"_id": document["_id"]},
                {"$set": {
                    "testdetails": updated_test_details,
                    "lastmodified_by": auth_user_id,
                    "lastmodified_date": datetime.now(IST)
                }}
            )
            
            if result.matched_count > 0:
                updated_count += 1
                total_tests_updated += tests_updated_in_doc
                updated_records.append({
                    "document_id": str(document["_id"]),
                    "created_date": document.get("created_date"),
                    "test_id": test_id,
                    "testname": doc_info["testname"],
                    "tests_updated": tests_updated_in_doc,
                    "all_tests": [
                        {
                            "test_id": t.get("test_id", "Unknown"),
                            "testname": t.get("testname", "Unknown")
                        } 
                        for t in test_details
                    ]
                })
        
        if updated_count == 0:
            return Response({
                "message": f"No records were updated for barcode: {barcode}. All tests may already be dispatched."
            }, status=status.HTTP_200_OK)
        
        return Response({
            "message": "Dispatch status updated successfully for latest documents of each test.",
            "barcode": barcode,
            "unique_test_ids_processed": len(latest_documents_by_test_id),
            "documents_updated": updated_count,
            "total_tests_updated": total_tests_updated,
            "modified_by": auth_user_id,
            "updated_records": updated_records
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        client.close()