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
from ...models import TestValue, MBTestValue
from ...models import Hmsbarcode
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.core.mail import EmailMessage
import os
from dotenv import load_dotenv
import pytz
import gridfs
load_dotenv()

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
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def hms_overall_report(request):
    try:
        # MongoDB setup for test details
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        test_details_collection = db.core_testdetails

        # Date filters
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        selected_date = request.GET.get("selected_date")
        patient_id = request.GET.get("patient_id")

        # ADD THIS HELPER FUNCTION AT THE TOP
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
            elif from_date and to_date:
                from_date = datetime.strptime(from_date, "%Y-%m-%d")
                to_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
            else:
                return JsonResponse({"error": "Either 'selected_date' or both 'from_date' and 'to_date' are required"}, status=400)
        except ValueError:
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        # Query Hmsbarcode as the primary source
        barcode_query = {"date__gte": from_date, "date__lt": to_date}
        if patient_id:
            barcode_query["patient_id"] = patient_id
        
        barcode_records = list(Hmsbarcode.objects.filter(**barcode_query).values(
            'billnumber', 'barcode', 'date', 'testdetails',
            'patient_id', 'patientname', 'age', 'age_type', 'gender', 
            'IPOPType', 'ref_doctor', 'ipnumber', 'location_id', 'phone', 'created_date'
        ))
        if barcode_records:
            print("Sample HMS barcode record:", barcode_records[0])

        if not barcode_records:
            return JsonResponse([], safe=False)

        # Fetch status and test data
        barcodes = [record['barcode'] for record in barcode_records if record['barcode']]
        
        sample_status_records = Hmssamplestatus.objects.filter(
            barcode__in=barcodes,
            date__range=(make_aware(from_date), make_aware(to_date))
        ).values("barcode", "testdetails")

        test_value_records = TestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(from_date.date(), to_date.date())
        ).values("barcode", "testdetails", "created_date")

        # Fetch MBTestValue records using Django ORM
        mb_test_value_records = MBTestValue.objects.filter(
            barcode__in=barcodes,
            date__range=(make_aware(from_date), make_aware(to_date))
        ).values("barcode", "testdetails", "created_date")

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

            # Test list from HMS billing record - ENRICH WITH DEPARTMENT FROM MONGODB
            test_list = []
            test_ids = []
            departments_set = set()
            
            test_field = record.get("testdetails", [])
            if isinstance(test_field, str):
                try:
                    test_field = json.loads(test_field.strip('"'))
                except json.JSONDecodeError as e:
                    test_field = []
            elif isinstance(test_field, list):
                test_field = test_field
            else:
                test_field = []

            # Extract test_ids
            if isinstance(test_field, list):
                test_ids = [test.get("test_id") for test in test_field if isinstance(test, dict) and test.get("test_id")]

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
                    else:
                        # Fallback if not found in MongoDB
                        original_test = next((t for t in test_field if t.get("test_id") == test_id), {})
                        test_list.append({
                            "test_id": test_id,
                            "testname": original_test.get("testname", "N/A"),
                            "department": "N/A"
                        })

            # Format departments as comma-separated string
            department = ", ".join(sorted(departments_set)) if departments_set else "N/A"

            testnames = ", ".join([test.get("testname", "") for test in test_list if isinstance(test, dict)])
            no_of_tests = len(test_list)

            # Get department-wise status
            department_statuses = {}
            if barcode and test_list:
                department_statuses = get_department_status(
                    test_list, 
                    barcode, 
                    sample_status_map, 
                    test_value_map,
                    mb_test_value_map  # Include MBTestValue data
                )

            # STATUS DETERMINATION (using test_id instead of test_name)
            status = "Registered"  # Default status
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

            # UPDATED: Get individual test statuses WITH TAT TRACKING
            individual_test_statuses = []
            if barcode and test_list:
                for test in test_list:
                    test_id = test.get('test_id')
                    test_name = test.get('testname', 'N/A')
                    
                    # Get sample info
                    sample_info = next((t for t in sample_tests if t.get('test_id') == test_id), {})
                    
                    # Get test value info
                    test_value_info = next((t for t in valid_test_values if t.get('test_id') == test_id), {})
                    
                    # **NEW: Get TAT from core_testdetails**
                    test_detail = test_details_collection.find_one(
                        {"test_id": test_id},
                        {"_id": 0, "TAT_Time": 1}
                    )
                    tat_time = test_detail.get("TAT_Time") if test_detail else None
                    
                    # **NEW: Get timestamps**
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
                            print(f"Error parsing sample_collected_time for test_id {test_id}: {e}")
                    
                    if test_value_info and test_value_info.get('approve_time'):
                        try:
                            approve_time_str = test_value_info['approve_time']
                            if approve_time_str and approve_time_str != 'null':
                                approve_time = datetime.strptime(
                                    approve_time_str, 
                                    "%Y-%m-%d %H:%M:%S"
                                )
                        except Exception as e:
                            print(f"Error parsing approve_time for test_id {test_id}: {e}")
                    
                    # **NEW: Calculate TAT status**
                    tat_status = None
                    seconds_left = None
                    tat_deadline_iso = None
                    
                    if tat_time and sample_collected_time:
                        # Parse TAT_Time using the helper function
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
                    
                    if test_value_info:
                        # Check if test has values
                        has_values = False
                        parameters = test_value_info.get("parameters", [])

                        # Biochemistry (parameters)
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
                    
                    # **NEW: Add TAT & Printed information to individual test status**
                    individual_test_statuses.append({
                        'test_id': test_id,
                        'test_name': test_name,
                        'status': test_status,
                        'printed': bool(test_value_info.get('printed', False)),
                        'printed_status': "Printed" if test_value_info.get('printed', False) else "Not Printed",
                        'tat_time': tat_time,
                        'seconds_left': int(seconds_left) if seconds_left is not None else None,
                        'tat_status': tat_status,
                        'tat_deadline': tat_deadline_iso,
                        'sample_collected_time': sample_collected_time.isoformat() if sample_collected_time else None,
                        'approve_time': approve_time.isoformat() if approve_time else None,
                    })

            # Test value status logic (using test_id for comparison)
            printed_status = "Not Printed"
            if valid_test_values:
                # Check testing status
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
               
                all_tested = all(has_test_values(t) for t in valid_test_values)
                partially_tested = any(has_test_values(t) for t in valid_test_values)
               
                # Get test_ids from billing record
                all_ordered_test_ids = {test.get("test_id") for test in test_list if test.get("test_id")}
               
                # Get approved, dispatch, and printed test_ids from test value records
                approved_test_ids = {t.get("test_id") for t in valid_test_values if t.get("approve", False) and t.get("test_id")}
                dispatch_test_ids = {t.get("test_id") for t in valid_test_values if t.get("dispatch", False) and t.get("test_id")}
                printed_test_ids = {t.get("test_id") for t in valid_test_values if t.get("printed", False) and t.get("test_id")}
               
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
                        total_expected = no_of_tests
                       
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
                        total_expected = no_of_tests
                       
                        if dispatch_count == total_expected and dispatch_count > 0:
                            all_dispatched = True
                            partially_dispatched = False
                        elif dispatch_count > 0:
                            partially_dispatched = True

                # Check printed status
                all_printed = False
                partially_printed = False
                if len(all_ordered_test_ids) > 0:
                    if all_ordered_test_ids.issubset(printed_test_ids) and len(printed_test_ids) == len(all_ordered_test_ids):
                        all_printed = True
                    elif len(printed_test_ids) > 0:
                        partially_printed = True
                    if not all_printed and valid_test_values:
                        printed_count = sum(1 for t in valid_test_values if t.get("printed", False))
                        total_expected = no_of_tests
                        if printed_count == total_expected and printed_count > 0:
                            all_printed = True
                            partially_printed = False
                        elif printed_count > 0:
                            partially_printed = True
                
                if all_printed:
                    printed_status = "Printed"
                elif partially_printed:
                    printed_status = "Partially Printed"
                else:
                    printed_status = "Not Printed"

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
            registration_date = record.get("date", formatted_date)
            if isinstance(registration_date, datetime):
                registration_date = registration_date.isoformat()

            test_created_date_formatted = None
            if test_created_date:
                if isinstance(test_created_date, datetime):
                    test_created_date_formatted = test_created_date.isoformat()
                else:
                    test_created_date_formatted = str(test_created_date)

            barcode_created_date = record.get("created_date")
            barcode_created_date_formatted = None
            if barcode_created_date:
                if isinstance(barcode_created_date, datetime):
                    barcode_created_date_formatted = barcode_created_date.isoformat()
                else:
                    barcode_created_date_formatted = str(barcode_created_date)

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
                "phone": record.get("phone", "N/A"),
                "barcode_generated_time": barcode_created_date_formatted,
                "test_names": testnames,
                "department": department,
                "department_statuses": department_statuses,
                "test_statuses": individual_test_statuses,  # NOW INCLUDES TAT & PRINTED INFO
                "no_of_tests": no_of_tests,
                "billnumber": billnumber,
                "barcode": barcode,
                "status": status,
                "printed_status": printed_status,
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

    if not barcode:
        return JsonResponse({'error': 'Barcode is required'}, status=400)

    try:
        test_values = TestValue.objects.filter(barcode=barcode)
        if not test_values.exists():
            return JsonResponse({'error': 'No test records found for the given barcode'}, status=404)

        barcode_details = Hmsbarcode.objects.filter(barcode=barcode).first()
        if not barcode_details:
            return JsonResponse({'error': 'No patient details found for the given barcode'}, status=404)

        sample_status = Hmssamplestatus.objects.filter(barcode=barcode)

        mongo_client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        mongo_db = mongo_client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        global_db          = mongo_client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs                 = gridfs.GridFS(global_db)

        # ── Resolve patient gender for reference range selection ──────────────
        patient_gender = (barcode_details.gender or '') if barcode_details else ''

        # ── Gender-aware reference_range resolver ─────────────────────────────
        def resolve_reference_range(meta, gender):
            """
            Pick reference_range from male/female fields based on patient gender.
            Falls back to generic reference_range when gender-specific value absent.
            low/high critical thresholds are NOT returned here (not needed).
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
                matching_params = [
                    p for p in params_list
                    if isinstance(p, dict) and p.get("test_code") == test_code
                ]
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

                test_id       = test.get("test_id")
                device_id     = test.get("device_id")
                parameters    = test.get("parameters", [])
                approve_by    = test.get("approve_by", "")
                dispatch_time = test.get("dispatch_time", "")

                if approve_by:
                    all_approvers.add(approve_by)

                core_test = core_testdetails_collection.find_one({"test_id": test_id})
                if not core_test:
                    continue

                testname      = core_test.get("test_name")
                department    = core_test.get("department", "N/A")
                NABL          = core_test.get("NABL", False)
                interpretation= core_test.get("interpretation", "")
                critical_range= core_test.get("critical_range", "")
                lod           = core_test.get("lod", "")
                labels        = core_test.get("labels", "")
                outsourced    = test.get("outsourced", False)
                comment       = test.get("comment", "")
                verified_by   = test.get("verified_by", "N/A")
                approve_time  = test.get("approve_time", "N/A")
                specimen_type = test.get("specimen_type") or core_test.get("specimen_type", "N/A")

                status = None
                if sample_status.exists():
                    for sample_status_record in sample_status:
                        status_details_list = sample_status_record.testdetails
                        if isinstance(status_details_list, str):
                            try:
                                status_details_list = json.loads(status_details_list)
                            except Exception:
                                status_details_list = []
                        if isinstance(status_details_list, list):
                            status = next(
                                (s for s in status_details_list if s.get("test_id") == test_id),
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
                    "outsourced":          outsourced,
                    "comment":             comment,
                    "testname":            testname,
                    "verified_by":         verified_by,
                    "approve_by":          approve_by,
                    "approve_time":        approve_time,
                    "dispatch_time":       dispatch_time,
                    "samplecollected_time": samplecollected_time,
                    "received_time":       received_time,
                    "interpretation":      interpretation,
                    "critical_range":      critical_range,
                    "lod":                 lod,
                    "labels":              labels,
                    "notes":               core_test.get("notes", ""),
                }

                # ── Parameterised test ────────────────────────────────────────
                if parameters and len(parameters) > 0:
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
                                "notes":           param_def.get("notes", ""),
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
                    # ── Single-value test ─────────────────────────────────────
                    # Gender-resolved reference_range from core_test
                    ref_range = resolve_reference_range(core_test, patient_gender)

                    test_detail.update({
                        "method":          core_test.get("method", ""),
                        "specimen_type":   specimen_type,
                        "value":           test.get("value", ""),
                        "unit":            core_test.get("unit", ""),
                        "reference_range": ref_range,   # gender-resolved
                        "sub_title":       test.get("sub_title", ""),
                    })

                approved_tests.append(test_detail)

            if approved_tests:
                patient_details = {
                    "patient_id":  barcode_details.patient_id,
                    "patientname": barcode_details.patientname,
                    "age":         barcode_details.age,
                    "age_type":    barcode_details.age_type if hasattr(barcode_details, 'age_type') else "Years",
                    "gender":      barcode_details.gender,
                    "phone":       barcode_details.phone,
                    "date":        test_value_record.date,
                    "barcode":     test_value_record.barcode,
                    "bill_no":     barcode_details.billnumber,
                    "barcodes":    [barcode],
                    "testdetails": approved_tests,
                    "refby":       barcode_details.ref_doctor  if hasattr(barcode_details, 'ref_doctor')  else "SELF",
                    "branch":      barcode_details.location_id if hasattr(barcode_details, 'location_id') else "N/A",
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
            "signatures":   signatures_data,
        }

        return JsonResponse(response_data, safe=False)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)