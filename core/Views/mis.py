from rest_framework.response import Response
from datetime import datetime,timedelta
from django.utils import timezone
import pytz
from rest_framework.views import APIView
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
import json
from ..models import Patient,SampleStatus,TestValue,Billing,BarcodeTestDetails,HmspatientBilling,Hmsbarcode,Hmssamplestatus
from pymongo import MongoClient
import os
from dotenv import load_dotenv
load_dotenv()
import logging

logger = logging.getLogger(__name__)
@permission_classes([HasRoleAndDataPermission])
class ConsolidatedDataView(APIView):
    def get(self, request):
        # Get date parameters - support both single date and date range
        single_date = request.query_params.get('date')
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')
        
        # Define IST timezone
        ist = pytz.timezone('Asia/Kolkata')
        
        # Determine date filtering approach
        if from_date and to_date:
            # Date range filtering
            try:
                from_date_obj = datetime.strptime(from_date, '%Y-%m-%d').date()
                to_date_obj = datetime.strptime(to_date, '%Y-%m-%d').date()
                use_date_range = True
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
        elif single_date:
            # Single date filtering (backward compatibility)
            try:
                input_date = datetime.strptime(single_date, '%Y-%m-%d').date()
                use_date_range = False
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
        else:
            # Default to today if no date provided
            input_date = datetime.now(ist).date()
            use_date_range = False
        
        try:
            # Connect to MongoDB to get test details from core_testdetails
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            test_details_collection = db.core_testdetails
            
            # Step 1: Get all billing records and filter by date in Python
            billing_records = Billing.objects.all()
            
            response_data = []
            processed_barcodes = set()  # Track processed barcodes to avoid duplicates
            
            for billing in billing_records:
                # Filter by bill_date in Python instead of database
                if billing.bill_date:
                    bill_date = billing.bill_date
                    
                    # Convert to IST if datetime is timezone-aware
                    if hasattr(bill_date, 'astimezone'):
                        bill_date_ist = bill_date.astimezone(ist)
                        bill_record_date = bill_date_ist.date()
                    elif hasattr(bill_date, 'date'):
                        bill_record_date = bill_date.date()
                    else:
                        bill_record_date = bill_date
                    
                    # Apply date filtering based on mode
                    if use_date_range:
                        if not (from_date_obj <= bill_record_date <= to_date_obj):
                            continue
                    else:
                        if bill_record_date != input_date:
                            continue
                else:
                    continue
                
                bill_no = billing.bill_no
                patient_id = billing.patient_id
                
                if not bill_no:
                    continue
                
                # Step 2: Get barcode from BarcodeTestDetails based on bill_no
                try:
                    barcode_details = BarcodeTestDetails.objects.get(bill_no=bill_no)
                    barcode = barcode_details.barcode
                except BarcodeTestDetails.DoesNotExist:
                    continue
                
                # Skip if we've already processed this barcode
                if barcode in processed_barcodes:
                    continue
                
                # Step 3: Try to get sample status data based on barcode (optional now)
                sample_status = None
                try:
                    sample_status = SampleStatus.objects.get(barcode=barcode)
                except SampleStatus.DoesNotExist:
                    # Continue processing even if sample status doesn't exist
                    pass
                
                # Step 4: Get ALL test value records for this barcode (not just the latest)
                test_value_records = TestValue.objects.filter(barcode=barcode).order_by('-created_date', '-lastmodified_date')
                
                # Get patient details
                try:
                    patient = Patient.objects.get(patient_id=patient_id)
                except Patient.DoesNotExist:
                    continue
                
                # Parse testdetails from BarcodeTestDetails to get test_ids
                barcode_tests = []
                try:
                    if isinstance(barcode_details.testdetails, str):
                        barcode_tests = json.loads(barcode_details.testdetails)
                    else:
                        barcode_tests = barcode_details.testdetails
                except json.JSONDecodeError:
                    barcode_tests = []
                
                # Parse JSON fields from sample status (if exists)
                sample_tests = []
                if sample_status:
                    try:
                        if isinstance(sample_status.testdetails, str):
                            sample_tests = json.loads(sample_status.testdetails)
                        else:
                            sample_tests = sample_status.testdetails
                    except json.JSONDecodeError:
                        sample_tests = []
                
                # Create a dictionary to store test value data by test_id
                test_values_by_id = {}
                
                # Process all TestValue records and organize by test_id
                for test_value in test_value_records:
                    try:
                        if isinstance(test_value.testdetails, str):
                            test_values = json.loads(test_value.testdetails)
                        else:
                            test_values = test_value.testdetails
                        
                        # Process each test in this TestValue record
                        for tv in test_values:
                            test_id = tv.get('test_id')
                            if test_id:
                                # Only keep the most recent record for each test_id
                                if test_id not in test_values_by_id:
                                    test_values_by_id[test_id] = tv
                                    
                    except json.JSONDecodeError:
                        continue
                
                # Create a dictionary to map test_id to sample status info
                sample_status_by_id = {}
                for test in sample_tests:
                    test_id = test.get('test_id')
                    if test_id:
                        sample_status_by_id[test_id] = test
                
                # Create a dictionary to store the consolidated test data by test_id
                consolidated_test_data = {}
                
                # Process tests from barcode record (which has the original test list)
                for test in barcode_tests:
                    test_id = test.get('test_id')
                    
                    if not test_id:
                        continue
                    
                    # Get sample status for this test_id (if exists)
                    sample_test = sample_status_by_id.get(test_id, {})
                    sample_status_value = sample_test.get('samplestatus', '')
                    
                    # Skip if sample status is "Outsource"
                    if sample_status_value == 'Outsource':
                        continue
                    
                    # Fetch test name and department from MongoDB core_testdetails
                    test_detail = test_details_collection.find_one(
                        {"test_id": test_id},
                        {
                            "_id": 0,
                            "test_id": 1,
                            "test_name": 1,
                            "department": 1
                        }
                    )
                    
                    if not test_detail:
                        continue
                    
                    testname = test_detail.get('test_name', 'N/A')
                    department = test_detail.get('department', 'N/A')
                    
                    consolidated_test_data[test_id] = {
                        'test_id': test_id,
                        'testname': testname,
                        'department': department,
                        'collected_time': sample_test.get('samplecollected_time', 'pending'),
                        'received_time': sample_test.get('received_time', 'pending'),
                        'approval_time': 'pending',
                        'dispatch_time': 'pending'
                    }
                
                # Then, update with the corresponding test values if available
                for test_id, test_data in consolidated_test_data.items():
                    if test_id in test_values_by_id:
                        tv = test_values_by_id[test_id]
                        test_data['approval_time'] = tv.get('approve_time', 'pending')
                        test_data['dispatch_time'] = tv.get('dispatch_time', 'pending')
                
                # Format bill_date - RETURN ISO 8601 UTC FORMAT
                if billing.bill_date:
                    if hasattr(billing.bill_date, 'astimezone'):
                        # If timezone-aware, convert to IST first, then to UTC for ISO format
                        bill_date_ist = billing.bill_date.astimezone(ist)
                        bill_date_utc = bill_date_ist.astimezone(pytz.UTC)
                        registered_time = bill_date_utc.isoformat()
                    elif hasattr(billing.bill_date, 'strftime'):
                        # If timezone-naive, assume it's already in IST, convert to UTC for ISO format
                        bill_date_ist = ist.localize(billing.bill_date)
                        bill_date_utc = bill_date_ist.astimezone(pytz.UTC)
                        registered_time = bill_date_utc.isoformat()
                    else:
                        registered_time = str(billing.bill_date)
                else:
                    registered_time = 'N/A'
                
                def convert_to_iso_if_needed(time_str):
                    """Convert IST time strings to ISO 8601 UTC format"""
                    if not time_str or time_str == 'pending' or time_str == 'null':
                        return time_str
                    
                    try:
                        # Try parsing as IST datetime string
                        dt_ist = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                        # Localize to IST and convert to UTC
                        dt_ist_aware = ist.localize(dt_ist)
                        dt_utc = dt_ist_aware.astimezone(pytz.UTC)
                        return dt_utc.isoformat()
                    except (ValueError, TypeError):
                        return time_str
                
                # Process each unique test
                for test_id, test_data in consolidated_test_data.items():
                    collected_time = convert_to_iso_if_needed(test_data['collected_time'])
                    received_time = convert_to_iso_if_needed(test_data['received_time'])
                    approval_time = convert_to_iso_if_needed(test_data['approval_time'])
                    dispatch_time = convert_to_iso_if_needed(test_data['dispatch_time'])
                    
                    # Calculate TAT time (approval_time - registered_time)
                    tat_time = 'pending'
                    if registered_time != 'N/A' and approval_time != 'pending' and approval_time != 'null':
                        try:
                            # Parse ISO 8601 format with timezone
                            registered_dt = datetime.fromisoformat(registered_time.replace('Z', '+00:00'))
                            approval_dt = datetime.fromisoformat(approval_time.replace('Z', '+00:00'))
                            
                            time_diff = approval_dt - registered_dt
                            total_seconds = int(time_diff.total_seconds())
                            tat_time = str(timedelta(seconds=total_seconds))
                        except (ValueError, TypeError, AttributeError):
                            tat_time = 'pending'
                    
                    # Calculate total processing time (dispatch_time - collected_time)
                    total_processing_time = 'pending'
                    if collected_time != 'pending' and dispatch_time != 'pending' and dispatch_time != 'null':
                        try:
                            collected_dt = datetime.fromisoformat(collected_time.replace('Z', '+00:00'))
                            dispatch_dt = datetime.fromisoformat(dispatch_time.replace('Z', '+00:00'))
                            
                            time_diff = dispatch_dt - collected_dt
                            total_seconds = int(time_diff.total_seconds())
                            total_processing_time = str(timedelta(seconds=total_seconds))
                        except (ValueError, TypeError, AttributeError):
                            total_processing_time = 'pending'
                    
                    response_data.append({
                        "patient_id": patient_id,
                        "patient_name": patient.patientname,
                        "age": patient.age,
                        "date": registered_time,
                        "registered_time": registered_time,
                        "barcode": barcode,
                        "test_id": test_id,
                        "test_name": test_data['testname'],
                        "department": test_data['department'],
                        "collected_time": collected_time,
                        "received_time": received_time,
                        "approval_time": approval_time,
                        "dispatch_time": dispatch_time,
                        "tat_time": tat_time,
                        "total_processing_time": total_processing_time
                    })
                
                # Mark this barcode as processed
                processed_barcodes.add(barcode)
            
            return Response(response_data, status=200)
            
        except Exception as e:
            return Response({
                "error": str(e)
            }, status=500)

@permission_classes([HasRoleAndDataPermission])
class HMSConsolidatedDataView(APIView):
    def get(self, request):
        # Get date parameters - support both single date and date range
        single_date = request.query_params.get('date')
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')
        
        # Determine date filtering approach
        if from_date and to_date:
            # Date range filtering
            try:
                from_date_obj = datetime.strptime(from_date, '%Y-%m-%d')
                to_date_obj = datetime.strptime(to_date, '%Y-%m-%d')
                # Set to end of day for to_date
                to_date_obj = to_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
                use_date_range = True
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
        elif single_date:
            # Single date filtering (backward compatibility)
            try:
                from_date_obj = datetime.strptime(single_date, '%Y-%m-%d')
                to_date_obj = from_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
                use_date_range = True
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
        else:
            # Default to today if no date provided
            from_date_obj = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            to_date_obj = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)
            use_date_range = True
        
        try:
            # Define Indian timezone
            ist = pytz.timezone('Asia/Kolkata')
            
            # Connect to MongoDB to get test details from core_testdetails
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            test_details_collection = db.core_testdetails
            
            # Step 1: Get barcode records directly from Hmsbarcode based on date range
            barcode_records = Hmsbarcode.objects.filter(
                date__gte=from_date_obj,
                date__lte=to_date_obj + timedelta(days=1)
            ).order_by('-date', 'barcode')
            
            response_data = []
            processed_barcodes = set()  # Track processed barcodes to avoid duplicates
            
            for barcode_record in barcode_records:
                barcode = barcode_record.barcode
                
                if not barcode or barcode in processed_barcodes:
                    continue
                
                # Step 2: Try to get sample status data based on barcode (optional now)
                sample_status = None
                try:
                    sample_status = Hmssamplestatus.objects.get(barcode=barcode)
                except Hmssamplestatus.DoesNotExist:
                    # Continue processing even if sample status doesn't exist
                    pass
                
                # Step 3: Get ALL test value records for this barcode (not just the latest)
                test_value_records = TestValue.objects.filter(barcode=barcode).order_by('-created_date', '-lastmodified_date')
                
                # Parse JSON fields from barcode record to get test_ids
                try:
                    if isinstance(barcode_record.testdetails, str):
                        barcode_tests = json.loads(barcode_record.testdetails)
                    else:
                        barcode_tests = barcode_record.testdetails
                except json.JSONDecodeError:
                    continue
                
                # Parse JSON fields from sample status (if exists)
                sample_tests = []
                if sample_status:
                    try:
                        if isinstance(sample_status.testdetails, str):
                            sample_tests = json.loads(sample_status.testdetails)
                        else:
                            sample_tests = sample_status.testdetails
                    except json.JSONDecodeError:
                        sample_tests = []
                
                # Create a dictionary to store test value data by test_id
                test_values_by_id = {}
                
                # Process all TestValue records and organize by test_id
                for test_value in test_value_records:
                    try:
                        if isinstance(test_value.testdetails, str):
                            test_values = json.loads(test_value.testdetails)
                        else:
                            test_values = test_value.testdetails
                        
                        # Process each test in this TestValue record
                        for tv in test_values:
                            test_id = tv.get('test_id')
                            if test_id:
                                # Only keep the most recent record for each test_id
                                if test_id not in test_values_by_id:
                                    test_values_by_id[test_id] = tv
                                    
                    except json.JSONDecodeError:
                        continue
                
                # Create a dictionary to map test_id to sample status info
                sample_status_by_id = {}
                for test in sample_tests:
                    test_id = test.get('test_id')
                    if test_id:
                        sample_status_by_id[test_id] = test
                
                # Create a dictionary to store the consolidated test data by test_id
                consolidated_test_data = {}
                
                # Process tests from barcode record (which has the original test list)
                for test in barcode_tests:
                    test_id = test.get('test_id')
                    
                    if not test_id:
                        continue
                    
                    # Get sample status for this test_id
                    sample_test = sample_status_by_id.get(test_id, {})
                    sample_status_value = sample_test.get('samplestatus', '')
                    
                    # Skip if sample status is "Outsource"
                    if sample_status_value == 'Outsource':
                        continue
                    
                    # Fetch test name and department from MongoDB core_testdetails
                    test_detail = test_details_collection.find_one(
                        {"test_id": test_id},
                        {
                            "_id": 0,
                            "test_id": 1,
                            "test_name": 1,
                            "department": 1
                        }
                    )
                    
                    if not test_detail:
                        continue
                    
                    testname = test_detail.get('test_name', 'N/A')
                    department = test_detail.get('department', 'N/A')
                    
                    consolidated_test_data[test_id] = {
                        'test_id': test_id,
                        'testname': testname,
                        'department': department,
                        'collected_time': sample_test.get('samplecollected_time', 'pending'),
                        'received_time': sample_test.get('received_time', 'pending'),
                        'approval_time': 'pending',
                        'dispatch_time': 'pending'
                    }
                
                # Then, update with the corresponding test values if available
                for test_id, test_data in consolidated_test_data.items():
                    if test_id in test_values_by_id:
                        tv = test_values_by_id[test_id]
                        test_data['approval_time'] = tv.get('approve_time', 'pending')
                        test_data['dispatch_time'] = tv.get('dispatch_time', 'pending')
                
                # Format registration time from barcode record's created_date and convert to IST
                if barcode_record.created_date:
                    # Convert to IST timezone
                    if hasattr(barcode_record.created_date, 'astimezone'):
                        # If it's a timezone-aware datetime, convert to IST
                        registered_dt_ist = barcode_record.created_date.astimezone(ist)
                    else:
                        # If it's a naive datetime, assume it's UTC and convert to IST
                        utc = pytz.UTC
                        registered_dt_utc = utc.localize(barcode_record.created_date)
                        registered_dt_ist = registered_dt_utc.astimezone(ist)
                    
                    registered_time = registered_dt_ist.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    registered_time = 'N/A'
                
                # Process each unique test
                for test_id, test_data in consolidated_test_data.items():
                    collected_time = test_data['collected_time']
                    received_time = test_data['received_time']
                    approval_time = test_data['approval_time']
                    dispatch_time = test_data['dispatch_time']
                    
                    # Calculate TAT time (approval_time - registered_time)
                    tat_time = 'pending'
                    if registered_time != 'N/A' and approval_time != 'pending' and approval_time != 'null':
                        try:
                            registered_dt = datetime.strptime(registered_time, '%Y-%m-%d %H:%M:%S')
                            approval_dt = datetime.strptime(approval_time, '%Y-%m-%d %H:%M:%S')
                            
                            time_diff = approval_dt - registered_dt
                            total_seconds = int(time_diff.total_seconds())
                            tat_time = str(timedelta(seconds=total_seconds))
                        except (ValueError, TypeError):
                            tat_time = 'pending'
                    
                    # Calculate total processing time (dispatch_time - collected_time)
                    total_processing_time = 'pending'
                    if collected_time != 'pending' and dispatch_time != 'pending' and dispatch_time != 'null':
                        try:
                            collected_dt = datetime.strptime(collected_time, '%Y-%m-%d %H:%M:%S')
                            dispatch_dt = datetime.strptime(dispatch_time, '%Y-%m-%d %H:%M:%S')
                            
                            time_diff = dispatch_dt - collected_dt
                            total_seconds = int(time_diff.total_seconds())
                            total_processing_time = str(timedelta(seconds=total_seconds))
                        except (ValueError, TypeError):
                            total_processing_time = 'pending'
                    
                    response_data.append({
                        "patient_id": barcode_record.patient_id or '',
                        "patient_name": barcode_record.patientname or '',
                        "age": barcode_record.age or 0,
                        "gender": barcode_record.gender or '',
                        "phone": getattr(barcode_record, 'phone', '') or '',
                        "ref_doctor": barcode_record.ref_doctor or '',
                        "date": registered_time,
                        "barcode": barcode,
                        "test_id": test_id,
                        "test_name": test_data['testname'],
                        "department": test_data['department'],
                        "collected_time": collected_time,
                        "received_time": received_time,
                        "approval_time": approval_time,
                        "dispatch_time": dispatch_time,
                        "tat_time": tat_time,
                        "total_processing_time": total_processing_time
                    })
                
                # Mark this barcode as processed
                processed_barcodes.add(barcode)
            
            return Response(response_data, status=200)
            
        except Exception as e:
            return Response({
                "error": str(e)
            }, status=500)     
              
@permission_classes([HasRoleAndDataPermission])
class FranchiseConsolidatedDataView(APIView):
    def get(self, request):
        # Get date parameters - support both single date and date range
        single_date = request.query_params.get('date')
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')
        
        # Determine date filtering approach
        if from_date and to_date:
            # Date range filtering
            try:
                from_date_obj = datetime.strptime(from_date, '%Y-%m-%d').date()
                to_date_obj = datetime.strptime(to_date, '%Y-%m-%d').date()
                use_date_range = True
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
        elif single_date:
            # Single date filtering (backward compatibility)
            try:
                input_date = datetime.strptime(single_date, '%Y-%m-%d').date()
                use_date_range = False
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
        else:
            # Default to today if no date provided
            input_date = datetime.now().date()
            use_date_range = False
        
        try:
            # Initialize MongoDB connection
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.franchise
            franchise_billing_collection = db.franchise_billing
            franchise_sample_collection = db.franchise_sample
            franchise_patient_collection = db.franchise_patient
            
            # Step 1: Get all billing records from MongoDB
            billing_records = list(franchise_billing_collection.find({}))
            
            response_data = []
            processed_barcodes = {}  # Track processed barcodes to avoid duplicates
            
            for billing in billing_records:
                # Filter by registrationDate in Python
                if billing.get('registrationDate'):
                    registration_date = billing['registrationDate']
                    if hasattr(registration_date, 'date'):
                        bill_record_date = registration_date.date()
                    else:
                        # Handle datetime string or already parsed date
                        if isinstance(registration_date, str):
                            bill_record_date = datetime.strptime(registration_date, '%Y-%m-%d').date()
                        else:
                            bill_record_date = registration_date.date()
                    
                    # Apply date filtering based on mode
                    if use_date_range:
                        if not (from_date_obj <= bill_record_date <= to_date_obj):
                            continue
                    else:
                        if bill_record_date != input_date:
                            continue
                else:
                    continue
                
                patient_id = billing.get('patient_id')
                barcode = billing.get('barcode')
                
                if not barcode or not patient_id:
                    continue
                
                # Skip if we've already processed this barcode
                if barcode in processed_barcodes:
                    continue
                
                # Step 2: Get sample status data based on barcode
                sample_status = franchise_sample_collection.find_one({"barcode": barcode})
                if not sample_status:
                    continue
                
                # Step 3: Get patient details
                patient = franchise_patient_collection.find_one({"patient_id": patient_id})
                if not patient:
                    continue
                
                # Parse JSON fields from sample status
                try:
                    testdetails_str = sample_status.get('testdetails', '[]')
                    if isinstance(testdetails_str, str):
                        sample_tests = json.loads(testdetails_str)
                    else:
                        sample_tests = testdetails_str
                except json.JSONDecodeError:
                    continue
                
                # Step 4: Get ALL test value records for this barcode (keeping existing TestValue logic)
                test_value_records = TestValue.objects.filter(barcode=barcode).order_by('-created_date', '-lastmodified_date')
                
                # Create a dictionary to store test value data by test name
                test_values_by_name = {}
                
                # Process all TestValue records and organize by test name
                for test_value in test_value_records:
                    try:
                        if isinstance(test_value.testdetails, str):
                            test_values = json.loads(test_value.testdetails)
                        else:
                            test_values = test_value.testdetails
                        
                        # Process each test in this TestValue record
                        for tv in test_values:
                            testname = tv.get('testname')
                            if testname:
                                # Only keep the most recent record for each test name
                                if testname not in test_values_by_name:
                                    test_values_by_name[testname] = tv
                                    
                    except json.JSONDecodeError:
                        continue
                
                # Create a dictionary to store the consolidated test data
                consolidated_test_data = {}
                
                # First, populate with sample status data
                for test in sample_tests:
                    testname = test.get('testname', 'N/A')
                    consolidated_test_data[testname] = {
                        'testname': testname,
                        'department': 'N/A',  # Will be updated from TestValue
                        'collected_time': test.get('samplecollected_time', 'pending'),
                        'received_time': test.get('received_time', 'pending'),
                        'approval_time': 'pending',
                        'dispatch_time': 'pending'
                    }
                
                # Then, update with the corresponding test values if available (including department)
                for testname, test_data in consolidated_test_data.items():
                    if testname in test_values_by_name:
                        tv = test_values_by_name[testname]
                        test_data['approval_time'] = tv.get('approve_time', 'pending')
                        test_data['dispatch_time'] = tv.get('dispatch_time', 'pending')
                        test_data['department'] = tv.get('department', 'N/A')  # Get department from TestValue
                
                # Format registrationDate for calculations
                if billing.get('registrationDate'):
                    registration_date = billing['registrationDate']
                    if hasattr(registration_date, 'strftime'):
                        registered_time = registration_date.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        registered_time = str(registration_date)
                else:
                    registered_time = 'N/A'
                
                # Process each unique test
                for testname, test_data in consolidated_test_data.items():
                    collected_time = test_data['collected_time']
                    received_time = test_data['received_time']
                    approval_time = test_data['approval_time']
                    dispatch_time = test_data['dispatch_time']
                    
                    # Calculate TAT time (approval_time - registered_time)
                    tat_time = 'pending'
                    if registered_time != 'N/A' and approval_time != 'pending' and approval_time != 'null':
                        try:
                            # Handle different datetime formats for registered_time
                            if 'T' in str(registered_time) and 'Z' in str(registered_time):
                                registered_dt = datetime.fromisoformat(str(registered_time).replace('Z', '+00:00'))
                            else:
                                registered_dt = datetime.strptime(str(registered_time), '%Y-%m-%d %H:%M:%S')
                            
                            # Handle different datetime formats for approval_time
                            if 'T' in str(approval_time) and 'Z' in str(approval_time):
                                approval_dt = datetime.fromisoformat(str(approval_time).replace('Z', '+00:00'))
                            else:
                                approval_dt = datetime.strptime(str(approval_time), '%Y-%m-%d %H:%M:%S')
                            
                            time_diff = approval_dt - registered_dt
                            total_seconds = int(time_diff.total_seconds())
                            tat_time = str(timedelta(seconds=total_seconds))
                        except (ValueError, TypeError):
                            tat_time = 'pending'
                    
                    # Calculate total processing time (dispatch_time - collected_time)
                    total_processing_time = 'pending'
                    if collected_time != 'pending' and dispatch_time != 'pending' and dispatch_time != 'null':
                        try:
                            # Handle different datetime formats
                            if 'T' in str(collected_time) and 'Z' in str(collected_time):
                                # ISO format
                                collected_dt = datetime.fromisoformat(str(collected_time).replace('Z', '+00:00'))
                            else:
                                # Standard format
                                collected_dt = datetime.strptime(str(collected_time), '%Y-%m-%d %H:%M:%S')
                            
                            if 'T' in str(dispatch_time) and 'Z' in str(dispatch_time):
                                # ISO format
                                dispatch_dt = datetime.fromisoformat(str(dispatch_time).replace('Z', '+00:00'))
                            else:
                                # Standard format
                                dispatch_dt = datetime.strptime(str(dispatch_time), '%Y-%m-%d %H:%M:%S')
                            
                            time_diff = dispatch_dt - collected_dt
                            total_seconds = int(time_diff.total_seconds())
                            total_processing_time = str(timedelta(seconds=total_seconds))
                        except (ValueError, TypeError) as e:
                            total_processing_time = 'pending'
                    
                    response_data.append({
                        "patient_id": patient_id,
                        "patient_name": patient.get('patientname', 'N/A'),
                        "age": patient.get('age', 'N/A'),
                        "date": registered_time,
                        "barcode": barcode,
                        "test_name": testname,
                        "department": test_data['department'],
                        "collected_time": collected_time,
                        "received_time": received_time,
                        "approval_time": approval_time,
                        "dispatch_time": dispatch_time,
                        "tat_time": tat_time,
                        "total_processing_time": total_processing_time
                    })
                
                # Mark this barcode as processed
                processed_barcodes[barcode] = True
            
            # Close MongoDB connection
            client.close()
            
            return Response(response_data, status=200)
            
        except Exception as e:
            return Response({
                "error": str(e)
            }, status=500)
