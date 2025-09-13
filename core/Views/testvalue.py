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
from ..models import Patient
from ..models import SampleStatus
from ..models import TestValue
from ..models import SampleStatus
from ..models import BarcodeTestDetails
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ..models import SampleStatus, TestValue
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
        # Get date parameters from query
        from_date_str = request.query_params.get('from_date', None)
        to_date_str = request.query_params.get('to_date', None)
        date_str = request.query_params.get('date', None)
        
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
        
        # Initialize combined results list and barcode tracking
        combined_results = []
        processed_barcodes = {}  # Track latest document per barcode
        
        # Get data from Django model (SampleStatus)
        sample_statuses = SampleStatus.objects.filter(
            date__gte=start_of_range, 
            date__lte=end_of_range
        ).order_by('-date', '-created_date')  # Order by date desc, then created_date desc
        
        # Process Django model data
        for sample_status in sample_statuses:
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
                    
                    sample_status_dict = {
                        'id': sample_status.id,
                        'created_by': sample_status.created_by,
                        'created_date': sample_status.created_date,
                        'lastmodified_by': sample_status.lastmodified_by,
                        'lastmodified_date': sample_status.lastmodified_date,
                        'patient_id': patient_id,
                        'patientname': patient_name,
                        'age': age,
                        'gender': gender,
                        'barcode': barcode,
                        'date': sample_status.date,
                        'testdetails': updated_tests,
                        'data_source': 'django_model'
                    }
                    
                    # Store/update the latest document for this barcode
                    processed_barcodes[barcode] = {
                        'created_date': sample_status.created_date,
                        'data': sample_status_dict
                    }
        
        # Add Django model results to combined_results
        for barcode_data in processed_barcodes.values():
            combined_results.append(barcode_data['data'])
        
        # Reset processed_barcodes for MongoDB processing
        mongodb_processed_barcodes = {}
        
        # Get data from MongoDB
        try:
            # Connect to MongoDB
            client = MongoClient("mongodb://admin:YSEgnm42789@103.205.141.245:27017/")
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
                            
                            # Create a standardized record format
                            mongo_record_dict = {
                                'id': str(record.get('_id', '')),
                                'created_by': record.get('created_by', ''),
                                'created_date': record.get('created_date'),
                                'lastmodified_by': record.get('lastmodified_by', ''),
                                'lastmodified_date': record.get('lastmodified_date'),
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
                                'date': record.get('created_date'),  # Using created_date as date
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
        
        # Serialize the combined data for JSON response
        serialized_results = []
        for item in combined_results:
            # Convert datetime objects to strings for JSON serialization
            for key, value in item.items():
                if isinstance(value, datetime):
                    item[key] = value.isoformat()
            serialized_results.append(item)
        
        # Sort combined results by date (most recent first)
        serialized_results.sort(key=lambda x: x.get('date', ''), reverse=True)
        
        return Response(serialized_results, status=status.HTTP_200_OK)
        
    except ValueError as ve:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def compare_test_details(request):   
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics  # Database name
    
    # Collections
    core_testdetails_collection = db.core_testdetails
    interface_testvalue_collection = db.interface_testvalue
    
    # MongoDB connection setup for franchise database    
    franchise_client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    franchise_db = franchise_client.franchise
    franchise_collection = franchise_db.franchise_sample
    
    # Get barcode from request
    barcode = request.GET.get('barcode')
    device_id = request.GET.get('device_id')  # Optional device_id filter
    
    if not barcode:
        return JsonResponse({'error': 'Barcode parameter is required'}, status=400)
    
    # Initialize default values
    patient_id = None
    patient_name = None
    test_list = []
    
    # First, try to get patient details from franchise_sample collection
    franchise_sample = franchise_collection.find_one({"barcode": barcode})
    
    if franchise_sample:
        # Get patient details from franchise_sample
        patient_id = franchise_sample.get('patient_id')
        patient_name = franchise_sample.get('patientname')
        
        # Get test details from franchise testdetails field
        try:
            testdetails = franchise_sample.get('testdetails', [])
            if isinstance(testdetails, str):
                test_list = json.loads(testdetails)
            elif isinstance(testdetails, list):
                test_list = testdetails
            else:
                test_list = []
        except json.JSONDecodeError:
            test_list = []
    else:
        # If not found in franchise_sample, try BarcodeTestDetails as fallback
        try:
            barcode_test_detail = BarcodeTestDetails.objects.get(barcode=barcode)
            patient_id = barcode_test_detail.patient_id
            patient_name = barcode_test_detail.patientname
            
            # Get test details from testdetails field
            try:
                if isinstance(barcode_test_detail.testdetails, str):
                    test_list = json.loads(barcode_test_detail.testdetails)
                elif isinstance(barcode_test_detail.testdetails, list):
                    test_list = barcode_test_detail.testdetails
                else:
                    test_list = []
            except json.JSONDecodeError:
                test_list = []
                
        except BarcodeTestDetails.DoesNotExist:
            # If neither source has the barcode, check if we have interface_testvalue data
            interface_record = interface_testvalue_collection.find_one(
                {"Barcode": barcode},
                sort=[("Receiveddate", -1)]
            )
            
            if interface_record:
                # Create basic patient info from interface data
                patient_id = interface_record.get('patient_id', f'UNKNOWN_{barcode}')
                patient_name = interface_record.get('patientname', f'Unknown Patient {barcode}')
                
                # Create test list from available test codes in interface_testvalue
                unique_tests = interface_testvalue_collection.distinct("TestCode", {"Barcode": barcode})
                
                for test_code in unique_tests:
                    # Try to find the test name from core_testdetails
                    test_detail = core_testdetails_collection.find_one({"test_code": test_code})
                    if test_detail:
                        test_name = test_detail.get('test_name', test_code)
                    else:
                        test_name = test_code
                    
                    test_list.append({
                        'test_name': test_name,
                        'test_id': test_code
                    })
            else:
                return JsonResponse({'error': f'No data found for barcode: {barcode} in any collection'}, status=404)
    
    # Get sample status details from Django SampleStatus model
    sample_status_map = {}
    try:
        sample_status_detail = SampleStatus.objects.get(barcode=barcode)
        if isinstance(sample_status_detail.testdetails, str):
            sample_test_list = json.loads(sample_status_detail.testdetails)
        elif isinstance(sample_status_detail.testdetails, list):
            sample_test_list = sample_status_detail.testdetails
        else:
            sample_test_list = []
            
        # Create a mapping of test names to sample status from Django
        for sample_test in sample_test_list:
            test_name = sample_test.get('testname')
            sample_status = sample_test.get('samplestatus')
            if test_name:
                sample_status_map[test_name] = {'status': sample_status, 'source': 'django_model'}
                
    except SampleStatus.DoesNotExist:
        sample_test_list = []
    except json.JSONDecodeError:
        sample_test_list = []
    
    # Also check franchise MongoDB collection for sample status
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
            
            # Add franchise sample status to mapping (if not already present from Django)
            for sample_test in franchise_test_list:
                test_name = sample_test.get('testname')
                sample_status = sample_test.get('samplestatus')
                if test_name and test_name not in sample_status_map:
                    sample_status_map[test_name] = {'status': sample_status, 'source': 'mongodb_franchise'}
                    
    except Exception as franchise_error:
        print(f"Franchise MongoDB connection error: {str(franchise_error)}")
    
    final_test_data = []
    processed_records = []  # Track records that will be marked as "Completed"
    
    # Process each test in the test list
    for test_item in test_list:
        test_name = test_item.get('test_name') or test_item.get('testname')
        test_id = test_item.get('test_id')
        
        # Get sample status for this test from either Django or franchise
        sample_status_info = sample_status_map.get(test_name, {'status': 'Unknown', 'source': 'none'})
        sample_status = sample_status_info['status']
        data_source = sample_status_info['source']
        
        # Only process if the sample status is 'Received' or if we want to show all
        if sample_status == "Received" or sample_status == 'Unknown':
            # FIXED: Get test details using BOTH test_name AND test_id for exact match
            test_details_cursor = core_testdetails_collection.find({
                "test_name": test_name,
                "test_id": test_id
            })
            
            test_details_list = list(test_details_cursor)
            
            # If no results with both criteria, try just test_id
            if not test_details_list and test_id:
                test_details_cursor = core_testdetails_collection.find({"test_id": test_id})
                test_details_list = list(test_details_cursor)
            
            # If still no results, try just test_name (but this might pick wrong test)
            if not test_details_list:
                test_details_cursor = core_testdetails_collection.find({"test_name": test_name})
                test_details_list = list(test_details_cursor)
                # If multiple results, try to pick the one that matches test_id if available
                if len(test_details_list) > 1 and test_id:
                    for detail in test_details_list:
                        if detail.get('test_id') == test_id:
                            test_details_list = [detail]
                            break
            
            test_found = False
            for test_detail in test_details_list:
                test_found = True
                # Get parameters (it's already a dict, no need to parse JSON)
                parameters = test_detail.get('parameters', {})
                
                print(f"DEBUG: Processing test_id {test_id}, test_name: {test_name}")
                print(f"DEBUG: Found test_detail with device_ids: {list(parameters.keys())}")
                
                if not parameters:
                    # Handle tests without parameters
                    test_code = test_detail.get('test_code', f"{test_name.replace(' ', '').upper()}01")
                    
                    test_value_query = {
                        "Barcode": barcode,
                        "TestCode": test_code,
                        "processingstatus": "pending"
                    }
                    
                    if device_id:
                        test_value_query["DeviceID"] = device_id
                    
                    test_value_doc = interface_testvalue_collection.find_one(
                        test_value_query,
                        sort=[("Receiveddate", -1)]
                    )
                    
                    if test_value_doc:
                        test_value = test_value_doc.get('Value', '')
                        processing_status = test_value_doc.get('processingstatus', 'N/A')
                        device_id_used = test_value_doc.get('DeviceID', 'N/A')
                        
                        # Track this record for processing status update
                        processed_records.append({
                            'barcode': barcode,
                            'test_code': test_code,
                            'device_id': device_id_used,
                            'record_id': str(test_value_doc.get('_id'))
                        })
                    else:
                        test_value = ''
                        processing_status = 'N/A'
                        device_id_used = 'N/A'
                    
                    test_info = {
                        "patient_id": patient_id,
                        "patientname": patient_name,
                        "barcode": barcode,
                        "device_id": device_id_used,
                        "test_id": test_id,
                        "testname": test_name,
                        "test_code": test_code,
                        "parameter_name": None,
                        "unit": test_detail.get('unit', 'N/A'),
                        "reference_range": test_detail.get('reference_range', 'N/A'),
                        "method": test_detail.get('method', 'N/A'),
                        "department": test_detail.get('department', 'N/A'),
                        "specimen_type": test_detail.get('specimen_type', 'N/A'),
                        "NABL": test_detail.get('NABL', 'N/A'),
                        "test_value": test_value,
                        "processing_status": processing_status,
                        "sample_status": sample_status,
                        "data_source": data_source,
                        "lab_unique_id": test_value_doc.get('lab_unique_id', 'N/A') if test_value_doc else 'N/A',
                        "created_date": test_value_doc.get('CreatedDate') if test_value_doc else None,
                        "received_date": test_value_doc.get('Receiveddate') if test_value_doc else None
                    }
                    
                    final_test_data.append(test_info)
                    continue
                
                # FIXED DEVICE SELECTION LOGIC FOR PARAMETERIZED TESTS
                has_interface_data = False
                selected_device = None
                
                # First, find which device actually has data for this barcode
                # Only check records with Pending status
                all_barcode_records = list(interface_testvalue_collection.find({
                    "Barcode": barcode,
                    "processingstatus": "pending"
                }))
                
                print(f"DEBUG: Found {len(all_barcode_records)} pending records for barcode {barcode}")
                
                if all_barcode_records:
                    # Get the actual test codes from interface data
                    interface_test_codes = [record.get('TestCode') for record in all_barcode_records if record.get('TestCode')]
                    interface_device_ids = list(set([record.get('DeviceID') for record in all_barcode_records if record.get('DeviceID')]))
                    
                    print(f"DEBUG: Interface test codes: {interface_test_codes}")
                    print(f"DEBUG: Interface device IDs: {interface_device_ids}")
                    print(f"DEBUG: Available parameter devices: {list(parameters.keys())}")
                    
                    # Find the device that has matching test codes
                    best_match_device = None
                    best_match_count = 0
                    
                    for device_key in parameters:
                        # Get test codes for this device from parameters
                        param_test_codes = [param.get('test_code') for param in parameters[device_key] if param.get('test_code')]
                        
                        # Count how many test codes match with interface data
                        matches = len(set(param_test_codes) & set(interface_test_codes))
                        
                        print(f"DEBUG: Device {device_key} - Parameter test codes: {param_test_codes[:5]}...")
                        print(f"DEBUG: Device {device_key} - Matches with interface: {matches}")
                        
                        if matches > best_match_count:
                            best_match_count = matches
                            best_match_device = device_key
                    
                    # Also check if any interface device ID directly matches a parameter device
                    for interface_dev_id in interface_device_ids:
                        if str(interface_dev_id) in parameters:
                            # Double check this device has matching test codes
                            param_test_codes = [param.get('test_code') for param in parameters[str(interface_dev_id)] if param.get('test_code')]
                            matches = len(set(param_test_codes) & set(interface_test_codes))
                            
                            print(f"DEBUG: Direct device match {interface_dev_id} - Matches: {matches}")
                            
                            if matches > best_match_count:
                                best_match_count = matches
                                best_match_device = str(interface_dev_id)
                    
                    if best_match_device and best_match_count > 0:
                        selected_device = best_match_device
                        has_interface_data = True
                        print(f"DEBUG: SELECTED DEVICE: {selected_device} with {best_match_count} matching test codes")
                    else:
                        # No matches found, check if requested device exists
                        if device_id and str(device_id) in parameters:
                            selected_device = str(device_id)
                            print(f"DEBUG: Using requested device {device_id} (no test code matches)")
                        else:
                            # Use first available device
                            selected_device = sorted(parameters.keys())[0]
                            print(f"DEBUG: Using default device {selected_device} (no matches found)")
                
                else:
                    # No interface data found
                    if device_id and str(device_id) in parameters:
                        selected_device = str(device_id)
                    else:
                        selected_device = sorted(parameters.keys())[0]
                    print(f"DEBUG: No interface data, using device {selected_device}")
                
                # Process parameters for the selected device
                if selected_device and selected_device in parameters:
                    param_list = parameters[selected_device]
                    print(f"DEBUG: Processing {len(param_list)} parameters for device: {selected_device}")
                    
                    for param in param_list:
                        test_code = param.get('test_code')
                        if not test_code:
                            continue
                        
                        # Initialize default values
                        test_value = ''
                        processing_status = 'No Data'
                        lab_unique_id = 'N/A'
                        created_date = None
                        received_date = None
                        
                        if has_interface_data:
                            # Look for exact test code match in interface data
                            matching_record = None
                            for record in all_barcode_records:
                                if (record.get('TestCode') == test_code and 
                                    record.get('processingstatus') == 'pending'):
                                    matching_record = record
                                    break
                            
                            if matching_record:
                                # Track this record for processing status update
                                processed_records.append({
                                    'barcode': barcode,
                                    'test_code': test_code,
                                    'device_id': matching_record.get('DeviceID'),
                                    'record_id': str(matching_record.get('_id'))
                                })
                                
                                # Set values from interface_testvalue
                                test_value = matching_record.get('Value', '')
                                processing_status = matching_record.get('processingstatus', 'pending')
                                lab_unique_id = matching_record.get('lab_unique_id', 'N/A')
                                created_date = matching_record.get('CreatedDate')
                                received_date = matching_record.get('Receiveddate')
                                
                                print(f"DEBUG: Found data for {test_code}: Value={test_value}")
                            else:
                                print(f"DEBUG: No interface data found for {test_code}")
                        
                        test_info = {
                            "patient_id": patient_id,
                            "patientname": patient_name,
                            "barcode": barcode,
                            "device_id": selected_device,
                            "test_id": test_id,
                            "testname": test_name,
                            "test_code": test_code,
                            "parameter_name": param.get('test_name'),
                            "unit": param.get('unit'),
                            "reference_range": param.get('reference_range'),
                            "method": param.get('method'),
                            "department": test_detail.get('department'),
                            "specimen_type": test_detail.get('specimen_type', param.get('specimen_type')),
                            "NABL": test_detail.get('NABL', 'N/A'),
                            "test_value": test_value,
                            "processing_status": processing_status,
                            "sample_status": sample_status,
                            "data_source": data_source,
                            "lab_unique_id": lab_unique_id,
                            "created_date": created_date,
                            "received_date": received_date
                        }
                        
                        final_test_data.append(test_info)
                
                # Break after processing first test_detail to avoid duplicates
                break
            
            if not test_found:
                # If no test details found in core_testdetails, create a basic entry
                test_info = {
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
                    "lab_unique_id": "N/A",
                    "created_date": None,
                    "received_date": None
                }
                final_test_data.append(test_info)
    
    # Close MongoDB connections
    try:
        client.close()
        franchise_client.close()
    except:
        pass
    
    # Return the consolidated test data along with processing records info
    return JsonResponse({
        'success': True,
        'patient_info': {
            'patient_id': patient_id,
            'patient_name': patient_name,
            'barcode': barcode
        },
        'test_count': len(final_test_data),
        'data': final_test_data,
        'processed_records': processed_records  # This is crucial for the save operation
    })

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
