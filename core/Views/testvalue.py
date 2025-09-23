from rest_framework.response import Response
from django.http import JsonResponse , HttpResponse
from datetime import datetime
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from urllib.parse import quote_plus
from pymongo import MongoClient
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
from ..models import Patient,Hmssamplestatus
from ..models import SampleStatus
from ..models import TestValue
from ..models import SampleStatus
from ..models import BarcodeTestDetails
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ..models import SampleStatus, TestValue, Hmssamplestatus, Hmsbarcode, HmspatientBilling
from ..serializers import SampleStatusSerializer
from ..serializers import TestValueSerializer
import os
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_samplestatus_testvalue(request):
    try:
        # Get query parameters
        from_date_str = request.query_params.get('from_date', None)
        to_date_str = request.query_params.get('to_date', None)
        date_str = request.query_params.get('date', None)
        source = request.query_params.get('source', 'all')  # 'hms', 'regular', or 'all'
        
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
        
        # Initialize combined results
        combined_results = []
        processed_barcodes = {}
        
        # Helper function to safely convert datetime to string
        def safe_datetime_to_string(dt_obj):
            """Convert datetime object to string safely"""
            if dt_obj is None:
                return None
            if isinstance(dt_obj, str):
                return dt_obj
            if hasattr(dt_obj, 'isoformat'):
                return dt_obj.isoformat()
            return str(dt_obj)
        
        # Process HMS Sample Status (if source is 'hms' or 'all')
        if source in ['hms', 'all']:
            hms_sample_statuses = Hmssamplestatus.objects.filter(
                date__gte=start_of_range,
                date__lte=end_of_range
            ).order_by('-date', '-created_date')
            
            for sample_status in hms_sample_statuses:
                try:
                    testdetails = json.loads(sample_status.testdetails) if isinstance(sample_status.testdetails, str) else sample_status.testdetails
                except json.JSONDecodeError:
                    continue
                
                filtered_tests = [
                    test for test in testdetails
                    if test.get('samplestatus') in ['Received', 'Outsource']
                ]
                
                if filtered_tests:
                    barcode = sample_status.barcode
                    
                    if barcode not in processed_barcodes or sample_status.created_date > processed_barcodes[barcode]['created_date']:
                        # Default values
                        patient_name = "Unknown Patient"
                        patient_id = "Unknown ID"
                        age = "Unknown"
                        gender = "Unknown"
                        
                        try:
                            # Get HMS barcode details
                            barcode_details = Hmsbarcode.objects.get(barcode=barcode)
                            billnumber = barcode_details.billnumber
                            
                            # Try to match billing using billnumber
                            try:
                                billing_details = HmspatientBilling.objects.get(billnumber=billnumber)
                                patient_name = billing_details.patientname
                                patient_id = billing_details.patient_id
                                age = billing_details.age
                                gender = billing_details.gender
                            except HmspatientBilling.DoesNotExist:
                                pass
                                
                        except Hmsbarcode.DoesNotExist:
                            if hasattr(sample_status, 'patient_id'):
                                patient_id = sample_status.patient_id
                        
                        # Get TestValue data for HMS
                        all_test_values = TestValue.objects.filter(
                            barcode=barcode
                        ).order_by('-created_date', '-lastmodified_date')
                        
                        updated_tests = []
                        for test in filtered_tests:
                            test_name = test.get('testname', '').strip().lower()
                            test_code = test.get('testcode', test_name).strip().lower()
                            
                            test.update({
                                'rerun': False,
                                'approve': False,
                                'test_value_exists': False,
                                'approve_time': None,
                                'rerun_time': None,
                                'approve_by': None
                            })
                            
                            matching_value = None
                            for tv in all_test_values:
                                try:
                                    tv_details = json.loads(tv.testdetails) if isinstance(tv.testdetails, str) else tv.testdetails
                                except Exception:
                                    tv_details = []
                                
                                for tv_test in tv_details:
                                    tv_test_name = tv_test.get('testname', '').strip().lower()
                                    tv_test_code = tv_test.get('testcode', tv_test_name).strip().lower()
                                    
                                    if tv_test_name == test_name or tv_test_code == test_code:
                                        matching_value = tv_test
                                        break
                                if matching_value:
                                    break
                            
                            if matching_value:
                                test.update({
                                    'test_value_exists': True,
                                    'approve': bool(matching_value.get('approve', False)),
                                    'rerun': bool(matching_value.get('rerun', False)),
                                    'approve_time': matching_value.get('approve_time'),
                                    'rerun_time': matching_value.get('rerun_time'),
                                    'approve_by': matching_value.get('approve_by')
                                })
                            
                            updated_tests.append(test)
                        
                        sample_status_dict = {
                            'id': sample_status.id,
                            'created_by': sample_status.created_by,
                            'created_date': safe_datetime_to_string(sample_status.created_date),
                            'lastmodified_by': sample_status.lastmodified_by,
                            'lastmodified_date': safe_datetime_to_string(sample_status.lastmodified_date),
                            'patient_id': patient_id,
                            'patientname': patient_name,
                            'age': age,
                            'gender': gender,
                            'barcode': barcode,
                            'date': safe_datetime_to_string(sample_status.date),
                            'testdetails': updated_tests,
                            'data_source': 'hms_django_model'
                        }
                        
                        processed_barcodes[barcode] = {
                            'created_date': sample_status.created_date,
                            'data': sample_status_dict
                        }
        
        # Process Regular Sample Status (if source is 'regular' or 'all')
        if source in ['regular', 'all']:
            regular_sample_statuses = SampleStatus.objects.filter(
                date__gte=start_of_range, 
                date__lte=end_of_range
            ).order_by('-date', '-created_date')
            
            for sample_status in regular_sample_statuses:
                try:
                    testdetails = json.loads(sample_status.testdetails) if isinstance(sample_status.testdetails, str) else sample_status.testdetails
                except json.JSONDecodeError:
                    continue
                
                filtered_tests = [
                    test for test in testdetails
                    if test.get('samplestatus') in ['Received', 'Outsource']
                ]
                
                if filtered_tests:
                    barcode = sample_status.barcode
                    
                    # Check if this is the latest document for this barcode
                    if barcode not in processed_barcodes or sample_status.created_date > processed_barcodes[barcode]['created_date']:
                        try:
                            barcode_details = BarcodeTestDetails.objects.get(barcode=barcode)
                            patient_name = barcode_details.patientname
                            patient_id = barcode_details.patient_id
                            age = barcode_details.age
                            gender = barcode_details.gender
                        except BarcodeTestDetails.DoesNotExist:
                            patient_name = "Unknown Patient"
                            patient_id = sample_status.patient_id if hasattr(sample_status, 'patient_id') else "Unknown ID"
                            age = "Unknown"
                            gender = "Unknown"
                        
                        # Get TestValue data for regular
                        all_test_values = TestValue.objects.filter(
                            barcode=barcode
                        ).order_by('-created_date', '-lastmodified_date')
                        
                        updated_tests = []
                        for test in filtered_tests:
                            test_name = test.get('testname', '').strip().lower()
                            test_code = test.get('testcode', test_name).strip().lower()
                            
                            # Default values
                            test['rerun'] = False
                            test['approve'] = False
                            test['test_value_exists'] = False
                            test['approve_time'] = None
                            test['rerun_time'] = None
                            test['approve_by'] = None
                            
                            matching_value = None
                            # Loop through all TestValues until we find the latest matching one
                            for tv in all_test_values:
                                try:
                                    tv_details = json.loads(tv.testdetails) if isinstance(tv.testdetails, str) else tv.testdetails
                                except Exception:
                                    tv_details = []
                                
                                for tv_test in tv_details:
                                    tv_test_name = tv_test.get('testname', '').strip().lower()
                                    tv_test_code = tv_test.get('testcode', tv_test_name).strip().lower()
                                    
                                    if tv_test_name == test_name or tv_test_code == test_code:
                                        matching_value = tv_test
                                        break
                                
                                if matching_value:
                                    break
                            
                            # If we found a match, update the test with values
                            if matching_value:
                                test['test_value_exists'] = True
                                test['approve'] = bool(matching_value.get('approve', False))
                                test['rerun'] = bool(matching_value.get('rerun', False))
                                test['approve_time'] = matching_value.get('approve_time')
                                test['rerun_time'] = matching_value.get('rerun_time')
                                test['approve_by'] = matching_value.get('approve_by')
                            
                            updated_tests.append(test)
                        
                        sample_status_dict = {
                            'id': sample_status.id,
                            'created_by': sample_status.created_by,
                            'created_date': safe_datetime_to_string(sample_status.created_date),
                            'lastmodified_by': sample_status.lastmodified_by,
                            'lastmodified_date': safe_datetime_to_string(sample_status.lastmodified_date),
                            'patient_id': patient_id,
                            'patientname': patient_name,
                            'age': age,
                            'gender': gender,
                            'barcode': barcode,
                            'date': safe_datetime_to_string(sample_status.date),
                            'testdetails': updated_tests,
                            'data_source': 'regular_django_model'
                        }
                        
                        processed_barcodes[barcode] = {
                            'created_date': sample_status.created_date,
                            'data': sample_status_dict
                        }
                
        # ✅ Process CHC Sample Status (if source is 'chc' or 'all')
       
        if source in ['chc', 'all']:
            try:
                print("🔍 Checking CHC samples...")
                print("👉 start_of_range:", start_of_range)
                print("👉 end_of_range:", end_of_range)

                # Connect to CHC MongoDB
                client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
                corp = client.Corporatehealthcheckup
                sample_collection = corp.core_sample
                billing_collection = corp.core_billing
                patient_collection = corp.core_employeeregistration

                # Query MongoDB for CHC samples within date range
                chc_query = {
                    "created_date": {
                        "$gte": start_of_range,
                        "$lt": end_of_range
                    }
                }
                chc_samples = list(sample_collection.find(chc_query).sort("created_date", -1))
                print(f"👉 Total CHC samples found: {len(chc_samples)}")

                chc_processed_barcodes = {}

                for record in chc_samples:
                    if '_id' in record:
                        record['_id'] = str(record['_id'])

                    barcode = record.get('barcode', '')

                    # Skip if already processed (keep latest only)
                    if barcode in processed_barcodes or (
                        barcode in chc_processed_barcodes and record.get('created_date') <= chc_processed_barcodes[barcode]['created_date']
                    ):
                        continue

                    try:
                        testdetails = record['testdetails'] if isinstance(record['testdetails'], list) else json.loads(record['testdetails'])
                    except Exception:
                        continue

                    # Filter only "Received" or "Outsource" tests
                    filtered_tests = [
                        test for test in testdetails
                        if test.get('samplestatus') in ['Received', 'Outsource']
                    ]

                    if not filtered_tests:
                        continue

                    # Get related billing details
                    billing = billing_collection.find_one({"barcode": barcode}) or {}
                    print(f"👉 Processing barcode: {barcode}, Billing found: {'Yes' if billing else 'No'}")
                    patient_id = billing.get("employee_id", None)
                    employee_id = patient_id
                    print(f"👉 Employee ID: {employee_id}")

                    # Get patient details
                    patient = patient_collection.find_one({"employee_id": patient_id}) if patient_id else {}
                    patient_name = patient.get("employee_name", "Unknown Patient")
                    age = patient.get("age", "Unknown")
                    gender = patient.get("gender", "Unknown")

                    # Fetch TestValue data from Django DB
                    all_test_values = TestValue.objects.filter(
                        barcode=barcode
                    ).order_by('-created_date', '-lastmodified_date')

                    updated_tests = []
                    for test in filtered_tests:
                        test_name = test.get('testname', '').strip().lower()
                        test_code = test.get('testcode', test_name).strip().lower()

                        test.update({
                            'rerun': False,
                            'approve': False,
                            'test_value_exists': False,
                            'approve_time': None,
                            'rerun_time': None,
                            'approve_by': None
                        })

                        matching_value = None
                        for tv in all_test_values:
                            try:
                                tv_details = json.loads(tv.testdetails) if isinstance(tv.testdetails, str) else tv.testdetails
                            except Exception:
                                tv_details = []

                            for tv_test in tv_details:
                                tv_test_name = tv_test.get('testname', '').strip().lower()
                                tv_test_code = tv_test.get('testcode', tv_test_name).strip().lower()
                                if tv_test_name == test_name or tv_test_code == test_code:
                                    matching_value = tv_test
                                    break
                            if matching_value:
                                break

                        if matching_value:
                            test.update({
                                'test_value_exists': True,
                                'approve': bool(matching_value.get('approve', False)),
                                'rerun': bool(matching_value.get('rerun', False)),
                                'approve_time': matching_value.get('approve_time'),
                                'rerun_time': matching_value.get('rerun_time'),
                                'approve_by': matching_value.get('approve_by')
                            })

                        updated_tests.append(test)

                    # Build final CHC dict
                    sample_dict = {
                        'id': record.get('_id'),
                        'created_by': record.get('created_by', ''),
                        'created_date': safe_datetime_to_string(record.get('created_date')),
                        'lastmodified_by': record.get('lastmodified_by', ''),
                        'lastmodified_date': safe_datetime_to_string(record.get('lastmodified_date')),
                        'barcode': barcode,
                        'company_id': record.get('company_id', ''),
                        'patient_id': employee_id or 'Unknown ID',
                        'patientname': patient_name,
                        'date': safe_datetime_to_string(record.get('date')),
                        'age': age,
                        'gender': gender,
                        'testdetails': updated_tests,
                        'data_source': 'chc_mongodb'
                    }

                    chc_processed_barcodes[barcode] = {
                        'created_date': record.get('created_date'),
                        'data': sample_dict
                    }

                # Add CHC MongoDB results to combined_results
                for barcode_data in chc_processed_barcodes.values():
                    combined_results.append(barcode_data['data'])

                client.close()

            except Exception as chc_error:
                print(f"CHC MongoDB error: {str(chc_error)}")

        # Add Django model results to combined_results
        for barcode_data in processed_barcodes.values():
            combined_results.append(barcode_data['data'])
        
        # Process MongoDB data (if source is 'regular' or 'all')
        mongodb_processed_barcodes = {}
        
        if source in ['regular', 'all']:
            try:
                # Connect to MongoDB
                client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
                db = client.franchise
                sample_collection = db.franchise_sample
                billing_collection = db.franchise_billing
                patient_collection = db.franchise_patient
                
                # Query MongoDB for records within the date range
                mongo_query = {
                    "created_date": {
                        "$gte": start_of_range,
                        "$lt": end_of_range
                    }
                }
                
                # Fetch data from MongoDB sample collection - order by created_date desc to get latest first
                mongodb_sample_records = list(sample_collection.find(mongo_query).sort("created_date", -1))
                
                # Extract all unique barcodes from the sample records
                barcodes = set()
                for record in mongodb_sample_records:
                    if 'barcode' in record and record['barcode']:
                        barcodes.add(record['barcode'])
                
                # Fetch billing data for all barcodes in one query
                billing_data = {}
                if barcodes:
                    billing_query = {"barcode": {"$in": list(barcodes)}}
                    billings = list(billing_collection.find(billing_query))
                    
                    # Create a dictionary for quick lookup: barcode -> patient_id
                    for billing in billings:
                        billing_data[billing.get('barcode')] = billing.get('patient_id')
                
                # Extract all unique patient_ids from billing data
                patient_ids = set(billing_data.values())
                patient_ids.discard(None)  # Remove None values
                
                # Fetch patient data for all patient_ids in one query
                patient_data = {}
                if patient_ids:
                    patient_query = {"patient_id": {"$in": list(patient_ids)}}
                    patients = list(patient_collection.find(patient_query))
                    
                    # Create a dictionary for quick lookup: patient_id -> patient_details
                    for patient in patients:
                        patient_data[patient.get('patient_id')] = {
                            'patientname': patient.get('patientname', ''),
                            'age': patient.get('age', ''),
                            'gender': patient.get('gender', ''),
                            'phoneNumber': patient.get('phoneNumber', ''),
                            'email': patient.get('email', ''),
                            'city': patient.get('city', ''),
                            'area': patient.get('area', ''),
                            'pincode': patient.get('pincode', ''),
                            'dateOfBirth': patient.get('dateOfBirth', '')
                        }
                
                # Process MongoDB records - only keep the latest document per barcode
                for record in mongodb_sample_records:
                    # Convert ObjectId to string for JSON serialization
                    if '_id' in record:
                        record['_id'] = str(record['_id'])
                    
                    # Get barcode and franchise_id from sample record
                    barcode = record.get('barcode', '')
                    franchise_id = record.get('franchise_id', '')
                    
                    # Skip if we already processed a newer document for this barcode
                    # or if this barcode already exists in Django results (Django takes precedence)
                    if barcode in processed_barcodes:
                        continue
                        
                    if barcode in mongodb_processed_barcodes:
                        # Compare created_date to see if this is newer
                        if record.get('created_date') <= mongodb_processed_barcodes[barcode]['created_date']:
                            continue
                    
                    # Get patient_id from billing data using barcode
                    patient_id = billing_data.get(barcode)
                    
                    # Get patient details using patient_id
                    if patient_id and patient_id in patient_data:
                        patient_details = patient_data[patient_id]
                        patient_name = patient_details['patientname']
                        age = patient_details['age']
                        gender = patient_details['gender']
                        phone_number = patient_details['phoneNumber']
                        email = patient_details['email']
                        city = patient_details['city']
                        area = patient_details['area']
                        pincode = patient_details['pincode']
                        date_of_birth = patient_details['dateOfBirth']
                    else:
                        # Set default values if patient not found
                        patient_name = 'Unknown Patient'
                        age = 'Unknown'
                        gender = 'Unknown'
                        phone_number = ''
                        email = ''
                        city = ''
                        area = ''
                        pincode = ''
                        date_of_birth = ''
                    
                    # Check if the record has testdetails and apply similar filtering
                    if 'testdetails' in record:
                        try:
                            testdetails = record['testdetails'] if isinstance(record['testdetails'], list) else json.loads(record['testdetails'])
                            
                            # Filter tests with samplestatus 'Received' or 'Outsource'
                            filtered_tests = [
                                test for test in testdetails
                                if test.get('samplestatus') in ['Received', 'Outsource']
                            ]
                            
                            if filtered_tests:
                                # Get ALL TestValues for this barcode (most recent created_date first)
                                all_test_values = TestValue.objects.filter(
                                    barcode=barcode
                                ).order_by('-created_date', '-lastmodified_date')
                                
                                updated_tests = []
                                for test in filtered_tests:
                                    test_name = test.get('testname', '').strip().lower()
                                    test_code = test.get('testcode', test_name).strip().lower()
                                    
                                    # Default values
                                    test['rerun'] = False
                                    test['approve'] = False
                                    test['test_value_exists'] = False
                                    test['approve_time'] = None
                                    test['rerun_time'] = None
                                    test['approve_by'] = None
                                    
                                    matching_value = None
                                    # Loop through all TestValues until we find the latest matching one
                                    for tv in all_test_values:
                                        try:
                                            tv_details = json.loads(tv.testdetails) if isinstance(tv.testdetails, str) else tv.testdetails
                                        except Exception:
                                            tv_details = []
                                        
                                        for tv_test in tv_details:
                                            tv_test_name = tv_test.get('testname', '').strip().lower()
                                            tv_test_code = tv_test.get('testcode', tv_test_name).strip().lower()
                                            
                                            if tv_test_name == test_name or tv_test_code == test_code:
                                                matching_value = tv_test
                                                break  # Found latest, stop inner loop
                                        
                                        if matching_value:
                                            break  # Stop outer loop too, we found the most recent match
                                    
                                    # If we found a match, update the test with values
                                    if matching_value:
                                        test['test_value_exists'] = True
                                        test['approve'] = bool(matching_value.get('approve', False))
                                        test['rerun'] = bool(matching_value.get('rerun', False))
                                        test['approve_time'] = matching_value.get('approve_time')
                                        test['rerun_time'] = matching_value.get('rerun_time')
                                        test['approve_by'] = matching_value.get('approve_by')
                                    
                                    updated_tests.append(test)
                                
                                # Create a standardized record format with proper datetime conversion
                                mongo_record_dict = {
                                    'id': str(record.get('_id', '')),
                                    'created_by': record.get('created_by', ''),
                                    'created_date': safe_datetime_to_string(record.get('created_date')),
                                    'lastmodified_by': record.get('lastmodified_by', ''),
                                    'lastmodified_date': safe_datetime_to_string(record.get('lastmodified_date')),
                                    'patient_id': patient_id or 'Unknown ID',
                                    'patientname': patient_name,
                                    'age': age,
                                    'gender': gender,
                                    'phoneNumber': phone_number,
                                    'email': email,
                                    'city': city,
                                    'area': area,
                                    'pincode': pincode,
                                    'dateOfBirth': date_of_birth,
                                    'barcode': barcode,
                                    'franchise_id': franchise_id,
                                    'date': safe_datetime_to_string(record.get('created_date')),  # Using created_date as date
                                    'testdetails': updated_tests,
                                    'data_source': 'mongodb'
                                }
                                
                                # Store this as the latest document for this barcode
                                mongodb_processed_barcodes[barcode] = {
                                    'created_date': record.get('created_date'),
                                    'data': mongo_record_dict
                                }
                        except (json.JSONDecodeError, TypeError):
                            # If testdetails cannot be parsed, skip this record
                            continue
                
                # Add MongoDB results to combined_results
                for barcode_data in mongodb_processed_barcodes.values():
                    combined_results.append(barcode_data['data'])
                
                # Close MongoDB connection
                client.close()
                
            except Exception as mongo_error:
                # If MongoDB connection fails, continue with just Django model data
                print(f"MongoDB connection error: {str(mongo_error)}")
        
        # FIXED: Safe sorting with datetime conversion
        def safe_sort_key(item):
            """Extract date for sorting, handling mixed datetime types"""
            date_value = item.get('date', '')
            if not date_value:
                return ''
            
            # If it's already a string, return as is for sorting
            if isinstance(date_value, str):
                return date_value
            
            # If it's a datetime object, convert to string
            if hasattr(date_value, 'isoformat'):
                return date_value.isoformat()
            
            # Fallback to string conversion
            return str(date_value)
        
        # Sort combined results by date (most recent first) using safe key
        try:
            combined_results.sort(key=safe_sort_key, reverse=True)
        except Exception as sort_error:
            print(f"Sort error: {sort_error}")
            # If sorting fails, return unsorted results
            pass
        
        return Response(combined_results, status=status.HTTP_200_OK)
        
    except ValueError as ve:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def compare_test_details(request):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics  # Database name

    core_testdetails_collection = db.core_testdetails
    interface_testvalue_collection = db.interface_testvalue

    franchise_client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    franchise_db = franchise_client.franchise
    corporate_db = franchise_client.Corporatehealthcheckup
    franchise_collection = franchise_db.franchise_sample
    corporate_billing_collection = corporate_db.core_billing
    corporate_sample_collection = corporate_db.core_sample

    barcode = request.GET.get('barcode')
    device_id = request.GET.get('device_id')
    source = request.GET.get('source', 'all')

    if not barcode:
        return JsonResponse({'error': 'Barcode parameter is required'}, status=400)

    patient_id = None
    patient_name = None
    final_test_data = []
    processed_records = []

    # HMS processing
    if source in ['hms', 'all']:
        hms_patient_id = None
        hms_patient_name = None
        hms_test_list = []
        try:
            barcode_obj = Hmsbarcode.objects.get(barcode=barcode)

            hms_patient_id = (
                getattr(barcode_obj, "patient_id", None)
                or getattr(barcode_obj, "patientid", None)
                or getattr(barcode_obj, "billnumber", f"HMS_UNKNOWN_{barcode}")
            )


            
            

            hms_patient_name = getattr(barcode_obj, "patientname", f"HMS Unknown Patient {barcode}")
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
                hms_patient_id = interface_record.get('patient_id', f'HMS_UNKNOWN_{barcode}')
                hms_patient_name = interface_record.get('patientname', f'HMS Unknown Patient {barcode}')
                unique_tests = interface_testvalue_collection.distinct("TestCode", {"Barcode": barcode})
                for test_code in unique_tests:
                    test_detail = core_testdetails_collection.find_one({"test_code": test_code})
                    test_name = test_detail.get('test_name', test_code) if test_detail else test_code
                    hms_test_list.append({'testname': test_name, 'test_id': test_code})

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
                test_name = sample_test.get('testname')
                sample_status = sample_test.get('samplestatus')
                if test_name:
                    hms_sample_status_map[test_name] = {'status': sample_status, 'source': 'hms_django_model'}
        except (Hmssamplestatus.DoesNotExist, json.JSONDecodeError):
            pass

        if hms_test_list:
            if not patient_id:
                patient_id = hms_patient_id
                patient_name = hms_patient_name
            hms_test_data = process_test_data(
                hms_test_list, hms_sample_status_map, hms_patient_id, hms_patient_name,
                barcode, device_id, core_testdetails_collection,
                interface_testvalue_collection, 'hms'
            )
            final_test_data.extend(hms_test_data['test_data'])
            processed_records.extend(hms_test_data['processed_records'])

    # Corporate Health Checkup processing (combine billing and samplestatus)
    if source in ['corporate', 'all']:
        corporate_patient_id = None
        corporate_patient_name = None
        corporate_test_list = []
        corporate_sample_status_map = {}

        # Billing record for test details and patient info
        billing_record = corporate_billing_collection.find_one({"barcode": barcode})
        if billing_record:
            corporate_patient_id = billing_record.get('patient_id')
            corporate_patient_name = billing_record.get('patientname')
            testdetails = billing_record.get('testdetails', [])
            if isinstance(testdetails, str):
                try:
                    corporate_test_list = json.loads(testdetails)
                except json.JSONDecodeError:
                    corporate_test_list = []
            elif isinstance(testdetails, list):
                corporate_test_list = testdetails

        # Sample status record for sample status per test
        try:
            sample_status_detail = corporate_sample_collection.find_one({"barcode": barcode})
            if sample_status_detail:
                sample_testdetails = sample_status_detail.get('testdetails', [])
                if isinstance(sample_testdetails, str):
                    sample_testdetails = json.loads(sample_testdetails)
                for test in sample_testdetails:
                    test_name = test.get('testname')
                    sample_status = test.get('samplestatus')
                    if test_name:
                        corporate_sample_status_map[test_name] = {
                            'status': sample_status,
                            'source': 'corporate_mongodb'
                        }
        except Exception:
            pass

        if corporate_test_list:
            if not patient_id:
                patient_id = corporate_patient_id
                patient_name = corporate_patient_name
            corporate_test_data = process_test_data(
                corporate_test_list, corporate_sample_status_map, corporate_patient_id,
                corporate_patient_name, barcode, device_id, core_testdetails_collection,
                interface_testvalue_collection, 'corporate'
            )
            final_test_data.extend(corporate_test_data['test_data'])
            processed_records.extend(corporate_test_data['processed_records'])

    # Regular (Franchise) processing
    if source in ['regular', 'all']:
        regular_patient_id = None
        regular_patient_name = None
        regular_test_list = []



        

        

        
        # First, try to get patient details from franchise_sample collection
        franchise_sample = franchise_collection.find_one({"barcode": barcode})
        


        if franchise_sample:
            regular_patient_id = franchise_sample.get('patient_id')
            regular_patient_name = franchise_sample.get('patientname')
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
                regular_patient_id = barcode_test_detail.patient_id
                regular_patient_name = barcode_test_detail.patientname
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
                        regular_patient_id = interface_record.get('patient_id', f'REG_UNKNOWN_{barcode}')
                        regular_patient_name = interface_record.get('patientname', f'Regular Unknown Patient {barcode}')
                        unique_tests = interface_testvalue_collection.distinct("TestCode", {"Barcode": barcode})
                        for test_code in unique_tests:
                            test_detail = core_testdetails_collection.find_one({"test_code": test_code})
                            test_name = test_detail.get('test_name', test_code) if test_detail else test_code
                            regular_test_list.append({'test_name': test_name, 'test_id': test_code})

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
                test_name = sample_test.get('testname')
                sample_status = sample_test.get('samplestatus')
                if test_name:
                    regular_sample_status_map[test_name] = {'status': sample_status, 'source': 'regular_django_model'}
        except (SampleStatus.DoesNotExist, json.JSONDecodeError):
            sample_test_list = []

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
                    test_name = sample_test.get('testname')
                    sample_status = sample_test.get('samplestatus')
                    if test_name and test_name not in regular_sample_status_map:
                        regular_sample_status_map[test_name] = {'status': sample_status, 'source': 'mongodb_franchise'}
        except Exception as franchise_error:
            print(f"Franchise MongoDB connection error: {str(franchise_error)}")

        if regular_test_list:
            if not patient_id:
                patient_id = regular_patient_id
                patient_name = regular_patient_name
            regular_test_data = process_test_data(
                regular_test_list, regular_sample_status_map, regular_patient_id, regular_patient_name,
                barcode, device_id, core_testdetails_collection, interface_testvalue_collection, 'regular'
            )
            final_test_data.extend(regular_test_data['test_data'])
            processed_records.extend(regular_test_data['processed_records'])

    try:
        client.close()
        franchise_client.close()
    except:
        pass

    if not final_test_data:
        return JsonResponse({'error': f'No data found for barcode: {barcode} in any collection'}, status=404)

    response_data = {
        'success': True,
        'patient_info': {
            'patient_id': patient_id,
            'patient_name': patient_name,
            'barcode': barcode
        },
        'test_count': len(final_test_data),
        'data': final_test_data,
        'processed_records': processed_records,
        'data_sources': list(set([item.get('data_source') for item in final_test_data if item.get('data_source')]))
    }
    return Response(response_data, status=200)


def process_test_data(test_list, sample_status_map, patient_id, patient_name, barcode, device_id, 
                     core_testdetails_collection, interface_testvalue_collection, data_source_type):
    """
    Helper function to process test data for HMS, Corporate, and Regular sources.
    Safely handles 'parameters' stored as dict or list.
    """
    final_test_data = []
    processed_records = []

    for test_item in test_list:
        test_name = test_item.get('test_name') or test_item.get('testname')
        test_id = test_item.get('test_id')

        # Get sample status
        sample_status_info = sample_status_map.get(test_name, {'status': 'Unknown', 'source': 'none'})
        sample_status = sample_status_info['status']
        data_source = sample_status_info['source']

        if sample_status not in ['Received', 'Unknown']:
            continue

        # Fetch test details from core_testdetails
        test_details_cursor = core_testdetails_collection.find({
            "$or": [{"test_name": test_name}, {"test_id": test_id}]
        })
        test_details_list = list(test_details_cursor)

        if not test_details_list:
            # No core_testdetails found, create a placeholder
            final_test_data.append({
                "patient_id": patient_id,
                "patientname": patient_name,
                "barcode": barcode,
                "device_id": "N/A",
                "test_id": test_id,
                "testname": test_name,
                "test_code": "N/A",
                "parameter_name": None,
                "unit": "",
                "reference_range": "",
                "method": "",
                "department": "",
                "specimen_type": "",
                "NABL": "N/A",
                "test_value": "",
                "processing_status": "No Test Details",
                "sample_status": sample_status,
                "data_source": data_source,
                "data_source_type": data_source_type,
                "lab_unique_id": "N/A",
                "created_date": None,
                "received_date": None
            })
            continue

        for test_detail in test_details_list:
            # Normalize parameters: dict if not already
            parameters = test_detail.get('parameters', {})
            if isinstance(parameters, list):
                # Convert old list format into dict with first device or default
                device_ids = test_detail.get('device_id', ['DefaultDevice'])
                parameters = {str(device_ids[0]): parameters}

            # DEVICE SELECTION LOGIC
            selected_device = None
            if device_id and str(device_id) in parameters:
                selected_device = str(device_id)
            elif parameters:
                selected_device = list(parameters.keys())[0]

            # Fetch interface records
            all_barcode_records = list(interface_testvalue_collection.find({
                "Barcode": barcode,
                "processingstatus": "pending"
            }))

            has_interface_data = bool(all_barcode_records)

            if has_interface_data:
                # Try to find best matching device based on interface test codes
                interface_test_codes = [r.get('TestCode') for r in all_barcode_records if r.get('TestCode')]
                best_match_count = 0
                best_device = selected_device
                for dev in parameters:
                    param_test_codes = [p.get('test_code') for p in parameters[dev] if p.get('test_code')]
                    matches = len(set(param_test_codes) & set(interface_test_codes))
                    if matches > best_match_count:
                        best_match_count = matches
                        best_device = dev
                selected_device = best_device

            # Process parameters for selected device
            param_list = parameters.get(selected_device, [])
            for param in param_list:
                test_code = param.get('test_code', 'N/A')

                # Default values
                test_value = ''
                processing_status = 'No Data'
                lab_unique_id = 'N/A'
                created_date = None
                received_date = None

                if has_interface_data:
                    matching_record = next(
                        (r for r in all_barcode_records if r.get('TestCode') == test_code and r.get('processingstatus') == 'pending'), None
                    )
                    if matching_record:
                        processed_records.append({
                            'barcode': barcode,
                            'test_code': test_code,
                            'device_id': matching_record.get('DeviceID'),
                            'record_id': str(matching_record.get('_id')),
                            'data_source_type': data_source_type
                        })
                        test_value = matching_record.get('Value', '')
                        processing_status = matching_record.get('processingstatus', 'pending')
                        lab_unique_id = matching_record.get('lab_unique_id', 'N/A')
                        created_date = matching_record.get('CreatedDate')
                        received_date = matching_record.get('Receiveddate')

                # Convert datetime to string
                for dt_field in ['created_date', 'received_date']:
                    val = locals()[dt_field]
                    if val and hasattr(val, 'isoformat'):
                        locals()[dt_field] = val.isoformat()
                    elif val and not isinstance(val, str):
                        locals()[dt_field] = str(val)

                final_test_data.append({
                    "patient_id": patient_id,
                    "patientname": patient_name,
                    "barcode": barcode,
                    "device_id": selected_device,
                    "test_id": test_id,
                    "testname": test_name,
                    "test_code": test_code,
                    "parameter_name": param.get('test_name'),
                    "unit": param.get('unit', 'N/A'),
                    "reference_range": param.get('reference_range', 'N/A'),
                    "method": param.get('method', 'N/A'),
                    "department": test_detail.get('department', 'N/A'),
                    "specimen_type": test_detail.get('specimen_type', 'N/A'),
                    "NABL": test_detail.get('NABL', 'N/A'),
                    "test_value": test_value,
                    "processing_status": processing_status,
                    "sample_status": sample_status,
                    "data_source": data_source,
                    "data_source_type": data_source_type,
                    "lab_unique_id": lab_unique_id,
                    "created_date": created_date,
                    "received_date": received_date
                })

            # Only process first matching test_detail
            break

    return {"test_data": final_test_data, "processed_records": processed_records}


def update_processing_status(barcode, test_code, device_id, latest_record_id_str):
    """
    Helper function to update processing status:
    - Mark the latest record as 'Completed'
    - Mark older records as 'Ignored'
    """
    client = None
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        interface_testvalue_collection = db.interface_testvalue
        
        # Convert string ID back to ObjectId
        try:
            latest_record_id = ObjectId(latest_record_id_str)
        except Exception as e:
            print(f"Invalid ObjectId format: {latest_record_id_str}, Error: {str(e)}")
            return False
        
        # Debug logging
        print(f"DEBUG: Updating processing status for:")
        print(f"  Barcode: {barcode}")
        print(f"  TestCode: {test_code}")
        print(f"  DeviceID: {device_id}")
        print(f"  RecordID: {latest_record_id_str}")
        
        # First, verify the record exists
        latest_record = interface_testvalue_collection.find_one({"_id": latest_record_id})
        if not latest_record:
            print(f"Record with ID {latest_record_id_str} not found")
            return False
        
        # Check current processing status
        current_status = latest_record.get('processingstatus', 'N/A')
        print(f"Current processing status: {current_status}")
        
        # Skip if already processed
        if current_status in ['Completed', 'Ignored']:
            print(f"Record already processed with status: {current_status}")
            return True
        
        # Update the latest record to 'Completed'
        update_result = interface_testvalue_collection.update_one(
            {
                "_id": latest_record_id,
                "Barcode": barcode,
                "TestCode": test_code,
                "DeviceID": device_id
            },
            {
                "$set": {
                    "processingstatus": "Completed",
                    "processesdate": datetime.utcnow()
                }
            }
        )
        
        if update_result.modified_count > 0:
            print(f"Successfully updated latest record to Completed")
        else:
            print(f"Failed to update latest record - criteria may not match")
            return False
        
        # Update older records for the same barcode, test_code, and device_id to 'Ignored'
        ignore_query = {
            "Barcode": barcode,
            "TestCode": test_code,
            "DeviceID": device_id,
            "_id": {"$ne": latest_record_id},
            "processingstatus": {"$nin": ["Ignored", "Completed"]}
        }
        
        count_to_ignore = interface_testvalue_collection.count_documents(ignore_query)
        print(f"Records to be marked as Ignored: {count_to_ignore}")
        
        if count_to_ignore > 0:
            ignore_result = interface_testvalue_collection.update_many(
                ignore_query,
                {
                    "$set": {
                        "processingstatus": "Ignored",
                        "processesdate": datetime.utcnow()
                    }
                }
            )
            print(f"Updated {ignore_result.modified_count} older records to Ignored")
        
        return True
        
    except Exception as e:
        print(f"Error updating processing status for barcode {barcode}, test_code {test_code}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if client:
            client.close()



            
@api_view([ 'POST'])
@permission_classes([HasRoleAndDataPermission])
def save_test_value(request):
    print(f"DEBUG: Request received: Method={request.method}, User={request.user}, Data={request.data}")
    
   
    if request.method == 'POST':
        payload = request.data
        employee_id = payload.get('auth-user-id')
        print(f"DEBUG: POST payload: {payload}")
        try:
           
            test_details_json = payload.get("testdetails", [])
            barcode = payload.get("barcode")
            locationId = payload.get("locationId")
            
            processed_records = payload.get("processed_records", [])
            
            print(f"DEBUG POST: Received {len(processed_records)} processed records")
            
            if not isinstance(test_details_json, list) or not test_details_json:
                return Response({"error": "Invalid test details format"}, status=status.HTTP_400_BAD_REQUEST)
            
            for test in test_details_json:
                testname = test.get('testname')
                if not testname:
                    return Response({"error": "Missing testname in test details"}, status=status.HTTP_400_BAD_REQUEST)
            
            test_value_record = TestValue.objects.create(
              
                created_by=employee_id,
                date=payload.get('date'),
                barcode=barcode,
                locationId=locationId,
                testdetails=test_details_json,
            )
            
            update_success_count = 0
            update_errors = []
            
            for record in processed_records:
                try:
                    success = update_processing_status(
                        record['barcode'],
                        record['test_code'],
                        record['device_id'],
                        record['record_id']
                    )
                    if success:
                        update_success_count += 1
                    else:
                        update_errors.append(f"Failed to update record {record['record_id']}")
                except Exception as e:
                    error_msg = f"Error updating record {record['record_id']}: {str(e)}"
                    print(error_msg)
                    update_errors.append(error_msg)
            
            response_message = "Test details saved successfully."
            if processed_records:
                response_message += f" Updated processing status for {update_success_count}/{len(processed_records)} records."
                if update_errors:
                    response_message += f" Errors: {'; '.join(update_errors[:3])}"
            
            return Response({
                "message": response_message,
                "updated_records": update_success_count,
                "total_records": len(processed_records),
                "errors": update_errors if update_errors else None
            }, status=status.HTTP_201_CREATED)
            
        except Patient.DoesNotExist:
            print("DEBUG: Patient not found")
            return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            print(f"DEBUG: POST error: {str(e)}")
            return Response({"error": f"An error occurred: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
