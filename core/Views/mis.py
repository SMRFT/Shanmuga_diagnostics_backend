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
IST = pytz.timezone('Asia/Kolkata')
@permission_classes([HasRoleAndDataPermission])
class ConsolidatedDataView(APIView):
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
            # Step 1: Get all billing records and filter by date in Python
            billing_records = Billing.objects.all()
            
            response_data = []
            processed_barcodes = {}  # Track processed barcodes to avoid duplicates
            
            for billing in billing_records:
                # Filter by bill_date in Python instead of database
                if billing.bill_date:
                    bill_date = billing.bill_date
                    if hasattr(bill_date, 'date'):
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
                
                # Step 3: Get sample status data based on barcode
                try:
                    sample_status = SampleStatus.objects.get(barcode=barcode)
                except SampleStatus.DoesNotExist:
                    continue
                
                # Step 4: Get ALL test value records for this barcode (not just the latest)
                test_value_records = TestValue.objects.filter(barcode=barcode).order_by('-created_date', '-lastmodified_date')
                
                # Get patient details
                try:
                    patient = Patient.objects.get(patient_id=patient_id)
                except Patient.DoesNotExist:
                    continue
                
                # Parse JSON fields
                try:
                    if isinstance(sample_status.testdetails, str):
                        sample_tests = json.loads(sample_status.testdetails)
                    else:
                        sample_tests = sample_status.testdetails
                        
                except json.JSONDecodeError:
                    continue
                
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
                        'department': test.get('department', 'N/A'),
                        'collected_time': test.get('samplecollected_time', 'pending'),
                        'received_time': test.get('received_time', 'pending'),
                        'approval_time': 'pending',
                        'dispatch_time': 'pending'
                    }
                
                # Then, update with the corresponding test values if available
                for testname, test_data in consolidated_test_data.items():
                    if testname in test_values_by_name:
                        tv = test_values_by_name[testname]
                        test_data['approval_time'] = tv.get('approve_time', 'pending')
                        test_data['dispatch_time'] = tv.get('dispatch_time', 'pending')
                
                # Process each unique test
                for testname, test_data in consolidated_test_data.items():
                    collected_time = test_data['collected_time']
                    received_time = test_data['received_time']
                    approval_time = test_data['approval_time']
                    dispatch_time = test_data['dispatch_time']
                    
                    # Calculate total processing time
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
                    
                    # Format bill_date for response
                    if billing.bill_date:
                        if hasattr(billing.bill_date, 'strftime'):
                            formatted_date = billing.bill_date.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            formatted_date = str(billing.bill_date)
                    else:
                        formatted_date = 'N/A'
                    
                    response_data.append({
                        "patient_id": patient_id,
                        "patient_name": patient.patientname,
                        "age": patient.age,
                        "date": formatted_date,
                        "barcode": barcode,
                        "test_name": testname,
                        "department": test_data['department'],
                        "collected_time": collected_time,
                        "received_time": received_time,
                        "approval_time": approval_time,
                        "dispatch_time": dispatch_time,
                        "total_processing_time": total_processing_time
                    })
                
                # Mark this barcode as processed
                processed_barcodes[barcode] = True
            
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
            # Step 1: Get all billing records from HmspatientBilling and filter by date in Python
            billing_records = HmspatientBilling.objects.all()
            
            response_data = []
            processed_barcodes = {}  # Track processed barcodes to avoid duplicates
            
            for billing in billing_records:
                # Filter by date in Python instead of database
                if billing.date:
                    bill_date = billing.date
                    if hasattr(bill_date, 'date'):
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
                
                billnumber = billing.billnumber
                patient_id = billing.patient_id
                
                if not billnumber:
                    continue
                
                # Step 2: Get barcode from Hmsbarcode based on billnumber
                try:
                    barcode_details = Hmsbarcode.objects.get(billnumber=billnumber)
                    barcode = barcode_details.barcode
                except Hmsbarcode.DoesNotExist:
                    continue
                
                # Skip if we've already processed this barcode
                if barcode in processed_barcodes:
                    continue
                
                # Step 3: Get sample status data based on barcode
                try:
                    sample_status = Hmssamplestatus.objects.get(barcode=barcode)
                except Hmssamplestatus.DoesNotExist:
                    continue
                
                # Step 4: Get ALL test value records for this barcode (not just the latest)
                test_value_records = TestValue.objects.filter(barcode=barcode).order_by('-created_date', '-lastmodified_date')
                
                # Parse JSON fields from sample status
                try:
                    if isinstance(sample_status.testdetails, str):
                        sample_tests = json.loads(sample_status.testdetails)
                    else:
                        sample_tests = sample_status.testdetails
                        
                except json.JSONDecodeError:
                    continue
                
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
                        'department': test.get('department', 'N/A'),
                        'collected_time': test.get('samplecollected_time', 'pending'),
                        'received_time': test.get('received_time', 'pending'),
                        'approval_time': 'pending',
                        'dispatch_time': 'pending'
                    }
                
                # Then, update with the corresponding test values if available
                for testname, test_data in consolidated_test_data.items():
                    if testname in test_values_by_name:
                        tv = test_values_by_name[testname]
                        test_data['approval_time'] = tv.get('approve_time', 'pending')
                        test_data['dispatch_time'] = tv.get('dispatch_time', 'pending')
                
                # Process each unique test
                for testname, test_data in consolidated_test_data.items():
                    collected_time = test_data['collected_time']
                    received_time = test_data['received_time']
                    approval_time = test_data['approval_time']
                    dispatch_time = test_data['dispatch_time']
                    
                    # Calculate total processing time
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
                    
                    # Format bill_date for response
                    if billing.date:
                        if hasattr(billing.date, 'strftime'):
                            formatted_date = billing.date.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            formatted_date = str(billing.date)
                    else:
                        formatted_date = 'N/A'
                    
                    response_data.append({
                        "patient_id": patient_id,
                        "patient_name": billing.patientname,  # Get from HmspatientBilling
                        "age": billing.age,  # Get from HmspatientBilling
                        "gender": billing.gender,  # Get from HmspatientBilling
                        "phone": billing.phone,  # Get from HmspatientBilling
                        "ref_doctor": billing.ref_doctor,  # Get from HmspatientBilling
                        "date": formatted_date,
                        "barcode": barcode,
                        "test_name": testname,
                        "department": test_data['department'],
                        "collected_time": collected_time,
                        "received_time": received_time,
                        "approval_time": approval_time,
                        "dispatch_time": dispatch_time,
                        "total_processing_time": total_processing_time
                    })
                
                # Mark this barcode as processed
                processed_barcodes[barcode] = True
            
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
                
                # Process each unique test
                for testname, test_data in consolidated_test_data.items():
                    collected_time = test_data['collected_time']
                    received_time = test_data['received_time']
                    approval_time = test_data['approval_time']
                    dispatch_time = test_data['dispatch_time']
                    
                    # Calculate total processing time
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
                    
                    # Format registrationDate for response
                    if billing.get('registrationDate'):
                        registration_date = billing['registrationDate']
                        if hasattr(registration_date, 'strftime'):
                            formatted_date = registration_date.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            formatted_date = str(registration_date)
                    else:
                        formatted_date = 'N/A'
                    
                    response_data.append({
                        "patient_id": patient_id,
                        "patient_name": patient.get('patientname', 'N/A'),
                        "age": patient.get('age', 'N/A'),
                        "date": formatted_date,
                        "barcode": barcode,
                        "test_name": testname,
                        "department": test_data['department'],
                        "collected_time": collected_time,
                        "received_time": received_time,
                        "approval_time": approval_time,
                        "dispatch_time": dispatch_time,
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