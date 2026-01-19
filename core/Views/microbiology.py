from rest_framework.response import Response
from django.http import JsonResponse
from datetime import datetime
from rest_framework.decorators import api_view
from rest_framework import  status
from pymongo import MongoClient
from rest_framework import status
from datetime import datetime, timedelta
from django.conf import settings  
from django.utils.timezone import make_aware
from datetime import datetime
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ..models import Patient
from ..models import BarcodeTestDetails
from django.http import JsonResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
import os, json
from django.utils.timezone import make_aware
from ..models import SampleStatus, MBTestValue, Hmssamplestatus, Hmsbarcode
from dotenv import load_dotenv
load_dotenv()
from urllib.parse import unquote_plus
import re
from django.views.decorators.csrf import csrf_exempt


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def micro_biology_testvalue(request):
    try:
        # Get query parameters
        from_date_str = request.query_params.get('from_date', None)
        to_date_str = request.query_params.get('to_date', None)
        date_str = request.query_params.get('date', None)
        source = request.query_params.get('source', 'all')
        
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
        
        # Helper function to safely convert datetime to string
        def safe_datetime_to_string(dt_obj):
            if dt_obj is None:
                return None
            if isinstance(dt_obj, str):
                return dt_obj
            if hasattr(dt_obj, 'isoformat'):
                return dt_obj.isoformat()
            return str(dt_obj)
        
        # ============================================
        # Connect to MongoDB for test details
        # ============================================
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        test_details_collection = db.core_testdetails
        
        # Cache test details by test_id
        test_details_cache = {}
        
        def get_test_details(test_id):
            """Fetch test details from MongoDB and cache"""
            if test_id not in test_details_cache:
                test_detail = test_details_collection.find_one(
                    {"test_id": test_id},
                    {
                        "_id": 0,
                        "test_id": 1,
                        "test_name": 1,
                        "department": 1,
                        "collection_container": 1
                    }
                )
                test_details_cache[test_id] = test_detail
            return test_details_cache[test_id]
        
        # ============================================
        # OPTIMIZATION 1: Collect all barcodes first
        # ============================================
        all_barcodes = set()
        sample_data_by_barcode = {}
        
        # Collect barcodes from HMS
        if source in ['hms', 'all']:
            hms_samples = Hmssamplestatus.objects.filter(
                date__gte=start_of_range,
                date__lte=end_of_range
            ).select_related().order_by('-created_date')
            
            for sample in hms_samples:
                barcode = sample.barcode
                all_barcodes.add(barcode)
                if barcode not in sample_data_by_barcode:
                    sample_data_by_barcode[barcode] = {
                        'source': 'hms',
                        'sample': sample,
                        'created_date': sample.created_date
                    }
        
        # Collect barcodes from Regular
        if source in ['regular', 'all']:
            regular_samples = SampleStatus.objects.filter(
                date__gte=start_of_range, 
                date__lte=end_of_range
            ).select_related().order_by('-created_date')
            
            for sample in regular_samples:
                barcode = sample.barcode
                all_barcodes.add(barcode)
                if barcode not in sample_data_by_barcode or sample.created_date > sample_data_by_barcode[barcode]['created_date']:
                    sample_data_by_barcode[barcode] = {
                        'source': 'regular',
                        'sample': sample,
                        'created_date': sample.created_date
                    }
        
        # ============================================
        # OPTIMIZATION 2: Bulk fetch all related data
        # ============================================
        
        # Fetch ALL TestValues for all barcodes in ONE query
        test_values_by_barcode = {}
        if all_barcodes:
            all_test_values = MBTestValue.objects.filter(
                barcode__in=all_barcodes
            ).order_by('barcode', '-created_date', '-lastmodified_date')
            
            # Group by barcode for O(1) lookup
            for tv in all_test_values:
                if tv.barcode not in test_values_by_barcode:
                    test_values_by_barcode[tv.barcode] = []
                test_values_by_barcode[tv.barcode].append(tv)
        
        # Fetch all HMS barcode details in one query
        hms_barcodes_dict = {}
        if source in ['hms', 'all'] and all_barcodes:
            hms_barcodes = Hmsbarcode.objects.filter(
                barcode__in=all_barcodes
            ).select_related()
            hms_barcodes_dict = {hb.barcode: hb for hb in hms_barcodes}
        
               
        # Fetch all regular barcode details in one query
        regular_barcodes_dict = {}
        if source in ['regular', 'all'] and all_barcodes:
            regular_barcodes = BarcodeTestDetails.objects.filter(
                barcode__in=all_barcodes
            )
            regular_barcodes_dict = {rb.barcode: rb for rb in regular_barcodes}
        
        # ============================================
        # OPTIMIZATION 3: Process with cached data
        # ============================================
        
        def parse_testdetails(testdetails_raw):
            """Cache-friendly test details parser"""
            try:
                return json.loads(testdetails_raw) if isinstance(testdetails_raw, str) else testdetails_raw
            except (json.JSONDecodeError, TypeError):
                return []
        
        def enrich_test_with_details(test):
            """Enrich test with details from MongoDB"""
            test_id = test.get('test_id')
            if test_id:
                test_detail = get_test_details(test_id)
                if test_detail:
                    test['testname'] = test_detail.get('test_name', test.get('testname', 'N/A'))
                    test['department'] = test_detail.get('department', test.get('department', 'N/A'))
                    test['container'] = test_detail.get('collection_container', test.get('container', 'N/A'))
            return test
        
        def match_test_values(test, barcode, test_values_by_barcode):
                """Optimized test value matching with pre-fetched data using test_id"""
                test_id = test.get('test_id')
                
                # Initialize default values
                test.update({
                    'rerun': False,
                    'approve': False,
                    'test_value_exists': False,
                    'approve_time': None,
                    'rerun_time': None,
                    'approve_by': None,
                    'value': None,
                    'remarks': None,
                    'comment': None,
                    'verified_by': None
                })
                
                # If no test_id, cannot match
                if not test_id:
                    return test
                
                # Use cached test values for this barcode
                test_values = test_values_by_barcode.get(barcode, [])
                
                for tv in test_values:
                    tv_details = parse_testdetails(tv.testdetails)
                    
                    for tv_test in tv_details:
                        tv_test_id = tv_test.get('test_id')
                        
                        # Match based on test_id
                        if tv_test_id == test_id:
                            test.update({
                                'test_value_exists': True,
                                'approve': bool(tv_test.get('approve', False)),
                                'rerun': bool(tv_test.get('rerun', False)),
                                'approve_time': tv_test.get('approve_time'),
                                'rerun_time': tv_test.get('rerun_time'),
                                'approve_by': tv_test.get('approve_by'),
                                'value': tv_test.get('value'),
                                'remarks': tv_test.get('remarks'),
                                'comment': tv_test.get('comment'),
                                'verified_by': tv_test.get('verified_by')
                            })
                            return test
                
                return test
        combined_results = []
        
        # Process HMS samples
        if source in ['hms', 'all']:
            for barcode, data in sample_data_by_barcode.items():
                if data['source'] != 'hms':
                    continue
                
                sample_status = data['sample']
                testdetails = parse_testdetails(sample_status.testdetails)
                
                filtered_tests = [
                    test for test in testdetails
                    if test.get('samplestatus') in ['Received']
                ]
                
                if not filtered_tests:
                    continue
                
                # Use cached barcode and billing data
                patient_name = "Unknown Patient"
                patient_id = "Unknown ID"
                age = "Unknown"
                gender = "Unknown"
                IPOPType = "Unknown"
                
                barcode_details = hms_barcodes_dict.get(barcode)
                if barcode_details:                    
                        patient_name = barcode_details.patientname
                        patient_id = barcode_details.patient_id
                        age = barcode_details.age
                        gender = barcode_details.gender
                        location_id = barcode_details.location_id
                        IPOPType = barcode_details.IPOPType
                elif hasattr(sample_status, 'patient_id'):
                    patient_id = sample_status.patient_id
                
                # Enrich tests with MongoDB details and match test values
                updated_tests = []
                for test in filtered_tests:
                    enriched_test = enrich_test_with_details(test)

                    # FILTER: Only allow Microbiology
                    if enriched_test.get('department') != 'Microbiology':
                        continue

                    matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                    updated_tests.append(matched_test)
                # Skip patient if no microbiology
                if not updated_tests:
                    continue

                
                combined_results.append({
                    'id': sample_status.id,
                    'created_by': sample_status.created_by,
                    'created_date': safe_datetime_to_string(sample_status.created_date),
                    'lastmodified_by': sample_status.lastmodified_by,
                    'lastmodified_date': safe_datetime_to_string(sample_status.lastmodified_date),
                    'patient_id': patient_id,
                    'patientname': patient_name,
                    'age': age,
                    'gender': gender,
                    'location_id': location_id,
                    'opiptype': IPOPType,
                    'barcode': barcode,
                    'date': safe_datetime_to_string(sample_status.date),
                    'testdetails': updated_tests,
                    'data_source': 'hms_django_model'
                })
        
        # Process Regular samples
        if source in ['regular', 'all']:
            for barcode, data in sample_data_by_barcode.items():
                if data['source'] != 'regular':
                    continue
                
                sample_status = data['sample']
                testdetails = parse_testdetails(sample_status.testdetails)
                
                filtered_tests = [
                    test for test in testdetails
                    if test.get('samplestatus') in ['Received']
                ]
                
                if not filtered_tests:
                    continue
                
                # Use cached barcode data
                barcode_details = regular_barcodes_dict.get(barcode)
                if barcode_details:
                    patient_name = barcode_details.patientname
                    patient_id = barcode_details.patient_id
                    age = barcode_details.age
                    gender = barcode_details.gender
                    is_emergency = barcode_details.is_emergency
                    patient_history = barcode_details.patient_history
                else:
                    patient_name = "Unknown Patient"
                    patient_id = sample_status.patient_id if hasattr(sample_status, 'patient_id') else "Unknown ID"
                    age = "Unknown"
                    gender = "Unknown"
                
                # Enrich tests with MongoDB details and match test values
                updated_tests = []
                for test in filtered_tests:
                    enriched_test = enrich_test_with_details(test)

                    # FILTER: Only allow Microbiology
                    if enriched_test.get('department') != 'Microbiology':
                        continue
                   
                    matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                    updated_tests.append(matched_test)
                # Skip patient if no microbiology
                if not updated_tests:
                    continue
                
                combined_results.append({
                    'id': sample_status.id,
                    'created_by': sample_status.created_by,
                    'created_date': safe_datetime_to_string(sample_status.created_date),
                    'lastmodified_by': sample_status.lastmodified_by,
                    'lastmodified_date': safe_datetime_to_string(sample_status.lastmodified_date),
                    'patient_id': patient_id,
                    'patientname': patient_name,
                    'age': age,
                    'gender': gender,
                    'is_emergency': is_emergency,
                    'patient_history': patient_history,
                    'barcode': barcode,
                    'date': safe_datetime_to_string(sample_status.date),
                    'testdetails': updated_tests,
                    'data_source': 'regular_django_model'
                })
        
        # ============================================
        # CHC MongoDB Processing (Optimized)
        # ============================================
        if source in ['chc', 'all']:
            try:
                corp = client.Corporatehealthcheckup
                sample_collection = corp.core_sample
                billing_collection = corp.core_billing
                patient_collection = corp.core_employeeregistration

                chc_query = {
                    "created_date": {"$gte": start_of_range, "$lt": end_of_range}
                }
                chc_samples = list(sample_collection.find(chc_query).sort("created_date", -1))
                
                # Bulk fetch CHC barcodes
                chc_barcodes = [s.get('barcode') for s in chc_samples if s.get('barcode')]
                
                # Bulk fetch billing and patient data
                billing_map = {b['barcode']: b for b in billing_collection.find({"barcode": {"$in": chc_barcodes}})}
                employee_ids = [b.get('employee_id') for b in billing_map.values() if b.get('employee_id')]
                patient_map = {p['employee_id']: p for p in patient_collection.find({"employee_id": {"$in": employee_ids}})}
                
                # Bulk fetch TestValues for CHC barcodes
                chc_test_values_by_barcode = {}
                if chc_barcodes:
                    chc_tvs = MBTestValue.objects.filter(barcode__in=chc_barcodes).order_by('barcode', '-created_date')
                    for tv in chc_tvs:
                        if tv.barcode not in chc_test_values_by_barcode:
                            chc_test_values_by_barcode[tv.barcode] = []
                        chc_test_values_by_barcode[tv.barcode].append(tv)
                
                chc_processed = {}
                for record in chc_samples:
                    barcode = record.get('barcode', '')
                    if not barcode or barcode in chc_processed:
                        continue
                    
                    testdetails = parse_testdetails(record.get('testdetails'))
                    filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Received']]
                    
                    if not filtered_tests:
                        continue
                    
                    billing = billing_map.get(barcode, {})
                    employee_id = billing.get("employee_id")
                    patient = patient_map.get(employee_id, {})
                    
                    # Enrich tests with MongoDB details and match test values
                    updated_tests = []
                    for test in filtered_tests:
                        enriched_test = enrich_test_with_details(test)

                        # FILTER: Only allow Microbiology
                        if enriched_test.get('department') != 'Microbiology':
                            continue

                        matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                        updated_tests.append(matched_test)
                    # Skip patient if no microbiology
                    if not updated_tests:
                        continue
                    
                    combined_results.append({
                        'id': str(record.get('_id')),
                        'created_by': record.get('created_by', ''),
                        'created_date': safe_datetime_to_string(record.get('created_date')),
                        'lastmodified_by': record.get('lastmodified_by', ''),
                        'lastmodified_date': safe_datetime_to_string(record.get('lastmodified_date')),
                        'barcode': barcode,
                        'location_id': record.get('company_id', ''),
                        'patient_id': employee_id or 'Unknown ID',
                        'patientname': patient.get("employee_name", "Unknown Patient"),
                        'date': safe_datetime_to_string(record.get('date')),
                        'age': patient.get("age", "Unknown"),
                        'gender': patient.get("gender", "Unknown"),
                        'testdetails': updated_tests,
                        'data_source': 'chc_mongodb'
                    })
                    chc_processed[barcode] = True
                
            except Exception as e:
                print(f"CHC MongoDB error: {str(e)}")
        
        # ============================================
        # Regular MongoDB Processing (Optimized)
        # ============================================
        if source in ['regular', 'all']:
            try:
                franchise_db = client.franchise
                sample_collection = franchise_db.franchise_sample
                billing_collection = franchise_db.franchise_billing
                patient_collection = franchise_db.franchise_patient
                
                mongo_query = {
                    "created_date": {"$gte": start_of_range, "$lt": end_of_range}
                }
                mongodb_samples = list(sample_collection.find(mongo_query).sort("created_date", -1))
                
                # Bulk fetch billing and patient data
                mongo_barcodes = [s.get('barcode') for s in mongodb_samples if s.get('barcode')]
                billing_map = {b['barcode']: b for b in billing_collection.find({"barcode": {"$in": mongo_barcodes}})}
                patient_ids = [b.get('patient_id') for b in billing_map.values() if b.get('patient_id')]
                patient_map = {p['patient_id']: p for p in patient_collection.find({"patient_id": {"$in": patient_ids}})}
                
                # Bulk fetch TestValues
                mongo_test_values_by_barcode = {}
                if mongo_barcodes:
                    mongo_tvs = MBTestValue.objects.filter(barcode__in=mongo_barcodes).order_by('barcode', '-created_date')
                    for tv in mongo_tvs:
                        if tv.barcode not in mongo_test_values_by_barcode:
                            mongo_test_values_by_barcode[tv.barcode] = []
                        mongo_test_values_by_barcode[tv.barcode].append(tv)
                
                mongo_processed = {}
                for record in mongodb_samples:
                    barcode = record.get('barcode', '')
                    if not barcode or barcode in all_barcodes or barcode in mongo_processed:
                        continue
                    
                    testdetails = parse_testdetails(record.get('testdetails'))
                    filtered_tests = [t for t in testdetails if t.get('samplestatus') in ['Received']]
                    
                    if not filtered_tests:
                        continue
                    
                    patient_id = billing_map.get(barcode, {}).get('patient_id')
                    patient = patient_map.get(patient_id, {})
                    
                    # Enrich tests with MongoDB details and match test values
                    updated_tests = []
                    for test in filtered_tests:
                        enriched_test = enrich_test_with_details(test)

                        # FILTER: Only allow Microbiology
                        if enriched_test.get('department') != 'Microbiology':
                            continue

                        matched_test = match_test_values(enriched_test, barcode, test_values_by_barcode)
                        updated_tests.append(matched_test)
                    # Skip patient if no microbiology
                    if not updated_tests:
                        continue
                    
                    combined_results.append({
                        'id': str(record.get('_id', '')),
                        'created_by': record.get('created_by', ''),
                        'created_date': safe_datetime_to_string(record.get('created_date')),
                        'lastmodified_by': record.get('lastmodified_by', ''),
                        'lastmodified_date': safe_datetime_to_string(record.get('lastmodified_date')),
                        'patient_id': patient_id or 'Unknown ID',
                        'patientname': patient.get('patientname', 'Unknown Patient'),
                        'age': patient.get('age', 'Unknown'),
                        'gender': patient.get('gender', 'Unknown'),
                        'phoneNumber': patient.get('phoneNumber', ''),
                        'email': patient.get('email', ''),
                        'city': patient.get('city', ''),
                        'area': patient.get('area', ''),
                        'pincode': patient.get('pincode', ''),
                        'dateOfBirth': patient.get('dateOfBirth', ''),
                        'barcode': barcode,
                        'location_id': record.get('franchise_id', ''),
                        'date': safe_datetime_to_string(record.get('created_date')),
                        'testdetails': updated_tests,
                        'data_source': 'mongodb'
                    })
                    mongo_processed[barcode] = True
                
            except Exception as e:
                print(f"MongoDB error: {str(e)}")
        
        # Close MongoDB connection
        client.close()
        
        # Sort results
        combined_results.sort(key=lambda x: x.get('date', ''), reverse=True)
        
        return Response(combined_results, status=status.HTTP_200_OK)
        
    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def mb_compare_test_details(request):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    core_testdetails_collection = db.core_testdetails

    barcode = request.GET.get('barcode')
    test_name_filter = request.GET.get('test_name')
    test_id = request.GET.get('test_id')
    parameter_type = request.GET.get('parameter_type')  # NEW: GNB, GPC, or Normal

    if not barcode:
        return JsonResponse({'error': 'Barcode parameter is required'}, status=400)

    final_test_data = []

    def normalize_parameters(parameters):
        """Normalize parameters to a dict keyed by device-id string -> list of parameter dicts."""
        if not parameters:
            return {}
        if isinstance(parameters, dict):
            normalized = {}
            for k, v in parameters.items():
                if v is None:
                    normalized[str(k)] = []
                elif isinstance(v, list):
                    normalized[str(k)] = v
                elif isinstance(v, dict):
                    normalized[str(k)] = [v]
                else:
                    normalized[str(k)] = [v] if v is not None else []
            return normalized
        if isinstance(parameters, list):
            return {"N/A": parameters}
        return {}

    # Build query for core_testdetails
    query = {}
    if test_name_filter:
        query["test_name"] = test_name_filter
    if test_id:
        try:
            query["test_id"] = int(test_id)
        except (ValueError, TypeError):
            client.close()
            return JsonResponse({
                'error': 'Invalid test_id parameter - must be a number'
            }, status=400)

    # Get test details from core_testdetails
    if test_name_filter or test_id:
        test_details_list = list(core_testdetails_collection.find(query))
    else:
        client.close()
        return JsonResponse({
            'error': 'Either test_name or test_id parameter is required'
        }, status=400)

    if not test_details_list:
        client.close()
        return JsonResponse({
            'error': f'No test details found for the given parameters'
        }, status=404)

    # Process each test detail
    for test_detail in test_details_list:
        test_name = test_detail.get("test_name")
        test_id = test_detail.get("test_id")
        
        # NEW: Handle "Normal" parameter type - return minimal data
        if parameter_type == "Normal":
            test_code = test_detail.get("test_code", f"{(test_name or '').replace(' ', '').upper()}01")
            final_test_data.append({
                "barcode": barcode,
                "test_id": test_id,
                "parameter_type": parameter_type,
                "test_code": test_code,
                "testname": test_name,
                "department": test_detail.get("department", "N/A"),
                "specimen_type": test_detail.get("specimen_type", "N/A"),
                "NABL": test_detail.get("NABL", False),
            })
            continue  # Skip parameter processing for Normal type
        
        raw_parameters = test_detail.get("parameters", {})
        parameters = normalize_parameters(raw_parameters)

        # Filter by parameter_type (GNB or GPC) if specified
        if parameter_type and isinstance(raw_parameters, dict):
            # Check if the parameter_type exists in the raw parameters
            if parameter_type in raw_parameters:
                # Only use the specified parameter type
                parameters = {parameter_type: raw_parameters[parameter_type]}
            else:
                # Parameter type not found, skip this test
                continue

        # Handle tests without parameters
        if not parameters:
            test_code = test_detail.get("test_code", f"{(test_name or '').replace(' ', '').upper()}01")
            final_test_data.append({
                "barcode": barcode,
                "test_id": test_id,
                "parameter_type": parameter_type,
                "testname": test_name,
                "test_code": test_code,
                "department": test_detail.get("department", "N/A"),
                "specimen_type": test_detail.get("specimen_type", "N/A"),
                "NABL": test_detail.get("NABL", "N/A"),
                "sub_title": None,
                "value_option": test_detail.get("value_option", []),
            })
            continue

        # Handle parameterized tests (GNB/GPC)
        # If parameter_type is specified, use it as the device_id
        # Otherwise, use the first available device
        if parameter_type and parameter_type in parameters:
            selected_device = parameter_type
        else:
            selected_device = sorted(parameters.keys())[0] if parameters else "N/A"

        if selected_device and selected_device in parameters:
            param_list = parameters[selected_device] or []

            for param in param_list:
                if not isinstance(param, dict):
                    continue

                test_code = param.get("test_code")
                if not test_code:
                    continue

                final_test_data.append({
                    "barcode": barcode,
                    "test_id": test_id,
                    "parameter_type": parameter_type,
                    "testname": test_name,
                    "test_code": test_code,
                    "parameter_name": param.get("test_name"),
                    "department": test_detail.get("department"),
                    "specimen_type": test_detail.get("specimen_type", param.get("specimen_type")),
                    "NABL": test_detail.get("NABL", "N/A"),
                    "sub_title": param.get("sub_title"),
                    "value_option": param.get("value_option"),
                })

    client.close()

    if not final_test_data:
        return JsonResponse({
            'error': f'No test structure found for parameter type: {parameter_type}' if parameter_type else 'No test structure found'
        }, status=404)

    response_data = {
        'success': True,
        'barcode': barcode,
        'parameter_type': parameter_type,  # Include in response
        'test_count': len(final_test_data),
        'data': final_test_data,
    }
    return Response(response_data, status=200)

@api_view([ 'POST'])
@permission_classes([HasRoleAndDataPermission])
def mb_save_test_value(request):
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
                test_id = test.get('test_id')
                if not test_id:
                    return Response({"error": "Missing test_id in test details"}, status=status.HTTP_400_BAD_REQUEST)
            
            test_value_record = MBTestValue.objects.create(
              
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


# 🟢 3️⃣ Process testname filter once
def normalize_testname(name):
    if not name:
        return ""
    normalized = re.sub(r'\s+', ' ', name.strip().lower())
    normalized = normalized.replace('&', 'and').replace('/', ' ')
    return normalized


@api_view([ 'GET'])
@permission_classes([HasRoleAndDataPermission])
def mb_get_test_values(request):
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    db = client.franchise
    billing_col = db.franchise_billing
    patient_col = db.franchise_patient

    corp = client.Corporatehealthcheckup
    billing_collection = corp.core_billing
    patient_collection = corp.core_employeeregistration
    
    # BarcodeTestDetails collection
    diagnostics = client.Diagnostics
    barcode_test_col = diagnostics.core_barcodetestdetails
    # NEW: core_testdetails collection for test metadata
    test_details_col = diagnostics.core_testdetails

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    patient_id_filter = request.GET.get('patient_id')
    testname_filter = request.GET.get('testname')
    emergency_filter = request.GET.get('emergency')

    # Date filters - apply early
    patients = MBTestValue.objects.all()
    if from_date and to_date:
        try:
            parsed_from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
            parsed_to_date = datetime.strptime(to_date, '%Y-%m-%d').date()
            patients = patients.filter(date__gte=parsed_from_date, date__lte=parsed_to_date)
        except ValueError:
            return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

    patients = patients.order_by('-date')
    patients_list = list(patients)
    
    if not patients_list:
        return JsonResponse([], safe=False)

    # Extract all barcodes upfront
    barcodes = [str(p.barcode).zfill(0) for p in patients_list]
    
    # ============================================
    # BULK FETCH ALL DATA SOURCES AT ONCE
    # ============================================
    
    # 1. Fetch all BarcodeTestDetails with is_emergency and patient_history
    barcode_details_docs = list(barcode_test_col.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "is_emergency": 1, "patient_history": 1, "_id": 0}
    ))
    barcode_details_dict = {
        doc['barcode']: {
            'patient_id': doc.get('patient_id'),
            'is_emergency': doc.get('is_emergency', False),
            'patient_history': doc.get('patient_history', '')
        }
        for doc in barcode_details_docs
    }
    
    # Get all patient IDs from barcode details
    barcode_patient_ids = [info['patient_id'] for info in barcode_details_dict.values() if info['patient_id']]
    
    # Fetch all Patient records
    patients_dict = {
        p.patient_id: {'name': p.patientname, 'age': p.age}
        for p in Patient.objects.filter(patient_id__in=barcode_patient_ids).only('patient_id', 'patientname', 'age')
    }
    
    # 2. NEW: Fetch patient details from Hmsbarcode (PRIMARY SOURCE)
    hms_barcodes_dict = {
        b.barcode: {
            'patient_id': b.patient_id,
            'name': b.patientname,
            'age': b.age,
            'billnumber': b.billnumber,
            'gender': b.gender,
            'ref_doctor': b.ref_doctor,
            'testdetails': b.testdetails
        }
        for b in Hmsbarcode.objects.filter(barcode__in=barcodes).only(
            'barcode', 'patient_id', 'patientname', 'age', 'billnumber', 'gender', 'ref_doctor', 'testdetails'
        )
    }
    
    # 3. MongoDB franchise - bulk fetch (fallback)
    mongo_billing_docs = list(billing_col.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "patient_id": 1, "_id": 0}
    ))
    mongo_billing_dict = {doc['barcode']: doc.get('patient_id') for doc in mongo_billing_docs}
    
    mongo_patient_ids = list(mongo_billing_dict.values())
    mongo_patient_docs = list(patient_col.find(
        {"patient_id": {"$in": mongo_patient_ids}},
        {"patient_id": 1, "patientname": 1, "age": 1, "_id": 0}
    ))
    mongo_patient_dict = {
        doc['patient_id']: {'name': doc.get('patientname', 'N/A'), 'age': doc.get('age', 'N/A')}
        for doc in mongo_patient_docs
    }
    
    # 4. Corporate - bulk fetch (fallback)
    corp_billing_docs = list(billing_collection.find(
        {"barcode": {"$in": barcodes}},
        {"barcode": 1, "employee_id": 1, "_id": 0}
    ))
    corp_billing_dict = {doc['barcode']: doc.get('employee_id') for doc in corp_billing_docs}
    
    corp_employee_ids = list(corp_billing_dict.values())
    corp_employee_docs = list(patient_collection.find(
        {"employee_id": {"$in": corp_employee_ids}},
        {"employee_id": 1, "employee_name": 1, "age": 1, "_id": 0}
    ))
    corp_employee_dict = {
        doc['employee_id']: {'name': doc.get('employee_name', 'N/A'), 'age': doc.get('age', 'N/A')}
        for doc in corp_employee_docs
    }
    
    # NEW: Collect all unique test_ids from all patients
    all_test_ids = set()
    for patient in patients_list:
        try:
            test_details = json.loads(patient.testdetails) if isinstance(patient.testdetails, str) else patient.testdetails
            for test in test_details:
                if test.get('test_id'):
                    all_test_ids.add(test['test_id'])
        except (json.JSONDecodeError, TypeError):
            continue
    
    # NEW: Bulk fetch test details from MongoDB core_testdetails
    test_metadata_docs = list(test_details_col.find(
        {"test_id": {"$in": list(all_test_ids)}},
        {
            "test_id": 1, 
            "test_name": 1, 
            "specimen_type": 1, 
            "collection_container": 1,
            "department": 1,
            "parameters": 1,
            "_id": 0
        }
    ))
    
    # Create lookup dictionary: test_id -> test metadata
    test_metadata_dict = {doc['test_id']: doc for doc in test_metadata_docs}
    
    # Helper function to get parameter details from test metadata
    def get_parameter_details(test_id, test_code, parameter_type=None, param_index=None):
        """Get parameter details from MongoDB test metadata by test_code and index"""
        test_meta = test_metadata_dict.get(test_id)
        if not test_meta:
            return None
        
        # Get parameters - it can be either a dict or a list
        params_data = test_meta.get('parameters', [])
        
        # Case 1: parameters is a dictionary with parameter_type keys
        if isinstance(params_data, dict):
            # If device_id is provided and exists in parameters, use it
            if parameter_type and parameter_type in params_data:
                params_list = params_data[parameter_type]
            else:
                # Get first device's parameters
                parameter_types = test_meta.get('parameter_type', [])
                if parameter_types and len(parameter_types) > 0:
                    first_device = parameter_types[0]
                    params_list = params_data.get(first_device, [])
                else:
                    # No device_id found, try to get first value from dict
                    params_list = next(iter(params_data.values())) if params_data else []
        
        # Case 2: parameters is a list (current case for PT test)
        elif isinstance(params_data, list):
            params_list = params_data
        
        else:
            return None
        
        # Ensure params_list is actually a list
        if not isinstance(params_list, list):
            params_list = []
        
        # If param_index is provided, use it directly (faster and more accurate)
        if param_index is not None and 0 <= param_index < len(params_list):
            param = params_list[param_index]
            return {
                'parameter_name': param.get('test_name'),
                'department': param.get('department') or test_meta.get('department'),
                'sub_title': param.get('sub_title', ''),
                'value_option': param.get('value_option', [])
            }
        
        # Fallback: Find matching parameter by test_code (may not be unique!)
        matching_params = [p for p in params_list if isinstance(p, dict) and p.get('test_code') == test_code]
        
        if matching_params:
            # If multiple params have same test_code, return first one
            # (This is a limitation - ideally use param_index)
            param = matching_params[0]
            return {
                'parameter_name': param.get('test_name'),
                'department': param.get('department') or test_meta.get('department'),
                'sub_title': param.get('sub_title', ''),
                'value_option': param.get('value_option', [])
            }
        
        return None
    
    # ============================================
    # PROCESS PATIENTS USING PRE-FETCHED DATA
    # ============================================
    
    filter_normalized = None
    if testname_filter and testname_filter != 'undefined':
        testname_filter = unquote_plus(testname_filter)
        filter_normalized = normalize_testname(testname_filter)
    
    patient_data = []
    
    for patient in patients_list:
        barcode_val = str(patient.barcode).zfill(0)
        
        try:
            test_details = json.loads(patient.testdetails) if isinstance(patient.testdetails, str) else patient.testdetails
        except (json.JSONDecodeError, TypeError):
            test_details = []
        
        # Initialize variables
        patient_name, patient_age, current_patient_id = "N/A", "N/A", None
        is_emergency = False
        patient_history = ""
        
        # PRIORITY 1: Get from Hmsbarcode (PRIMARY SOURCE)
        if barcode_val in hms_barcodes_dict:
            hms_info = hms_barcodes_dict[barcode_val]
            current_patient_id = hms_info['patient_id']
            patient_name = hms_info['name']
            patient_age = hms_info['age']
        
        # PRIORITY 2: BarcodeTestDetails lookup (includes emergency status and history)
        if barcode_val in barcode_details_dict:
            barcode_info = barcode_details_dict[barcode_val]
            if not current_patient_id:
                current_patient_id = barcode_info['patient_id']
            is_emergency = barcode_info['is_emergency']
            patient_history = barcode_info['patient_history']
            
            if current_patient_id in patients_dict and patient_name == "N/A":
                patient_info = patients_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age = patient_info['age']
        
        # PRIORITY 3: MongoDB franchise lookup (fallback)
        if not current_patient_id and barcode_val in mongo_billing_dict:
            current_patient_id = mongo_billing_dict[barcode_val]
            if current_patient_id in mongo_patient_dict:
                patient_info = mongo_patient_dict[current_patient_id]
                patient_name = patient_info['name']
                patient_age = patient_info['age']
        
        # PRIORITY 4: Corporate lookup (fallback)
        if not current_patient_id and barcode_val in corp_billing_dict:
            current_patient_id = corp_billing_dict[barcode_val]
            if current_patient_id in corp_employee_dict:
                corp_info = corp_employee_dict[current_patient_id]
                patient_name = corp_info['name']
                patient_age = corp_info['age']
        
        # Final fallback
        if not current_patient_id:
            current_patient_id = getattr(patient, 'patient_id', None)
        
        # Patient ID filter
        if patient_id_filter and current_patient_id != patient_id_filter:
            continue
        
        # Emergency filter
        if emergency_filter:
            if emergency_filter == 'emergency' and not is_emergency:
                continue
            elif emergency_filter == 'normal' and is_emergency:
                continue
        
        # NEW: Enrich test details with metadata from MongoDB
        enriched_test_details = []
        for test in test_details:
            test_id = test.get('test_id')
            test_code = test.get('test_code')
            parameter_type = test.get('parameter_type')
            parameters = test.get('parameters', [])
            
            # Get test metadata
            test_meta = test_metadata_dict.get(test_id, {})
            
            # Check if test has parameters array (like CBC with 20 parameters or PT with 4 parameters)
            if parameters and isinstance(parameters, list) and len(parameters) > 0:
                # This is a test with multiple parameters (e.g., CBC, PT)
                # Enrich each parameter with its metadata
                enriched_parameters = []
                
                for param_index, param in enumerate(parameters):
                    param_test_code = param.get('test_code')
                    param_value = param.get('value')
                    param_comment = param.get('comment', '')
                    param_result = param.get('result', '')
                    
                    # Get parameter-specific details from MongoDB using INDEX
                    # This is more reliable than test_code when multiple params share the same test_code
                    param_details = get_parameter_details(test_id, param_test_code, parameter_type, param_index=param_index)
                    
                    if param_details:
                        enriched_param = {
                            'test_code': param_test_code,
                            'parameter_name': param_details.get('parameter_name'),
                            'value': param_value,
                            'department': param_details.get('department', 'N/A'),
                            'sub_title': param_details.get('sub_title', ''),
                            'value_option': param_details.get('value_option', []),
                            'comment': param_comment,
                            'result': param_result
                        }
                    else:
                        # No matching parameter found in MongoDB
                        enriched_param = {
                            'test_code': param_test_code,
                            'parameter_name': None,
                            'value': param_value,
                            'department': 'N/A',
                            'sub_title': '',
                            'value_option': [],
                            'comment': param_comment,
                            'result': param_result
                        }
                    
                    enriched_parameters.append(enriched_param)
                
                # Create enriched test with parameters
                enriched_test = {
                    **test,  # Keep all existing fields
                    'test_name': test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type': test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department': test_meta.get('department', test.get('department', 'N/A')),
                    'parameters': enriched_parameters  # Replace with enriched parameters
                }
                
            else:
                # This is a single test with test_code (not parameters array)
                # Start with base test info from metadata
                enriched_test = {
                    **test,  # Keep all existing fields from core_testvalue
                    'test_name': test_meta.get('test_name', test.get('test_name', 'N/A')),
                    'specimen_type': test_meta.get('specimen_type', test.get('specimen_type', 'N/A')),
                    'collection_container': test_meta.get('collection_container', test.get('collection_container', 'N/A')),
                    'department': test_meta.get('department', test.get('department', 'N/A')),
                }
                
                # If test_code exists, get parameter-specific details
                if test_code and test_code != 'N/A':
                    param_details = get_parameter_details(test_id, test_code, parameter_type)
                    
                    if param_details:
                        # Override with parameter-specific details
                        enriched_test.update({
                            'parameter_name': param_details.get('parameter_name'), 
                            'department': param_details.get('department', enriched_test['department']),
                        })
                    else:
                        # No matching parameter found, use defaults
                        enriched_test.update({
                            'parameter_name': None,
                        })
                else:
                    # No test_code, this is a main test without parameters
                    enriched_test.update({
                        'parameter_name': None,
                    })
            
            enriched_test_details.append(enriched_test)
        
        # Replace test_details with enriched version
        test_details = enriched_test_details
        
        # Testname filtering
        if filter_normalized:
            filtered_test_details = []
            for test in test_details:
                test_name = test.get('test_name', '')
                test_normalized = normalize_testname(test_name)
                match_found = (
                    test_normalized == filter_normalized or
                    filter_normalized in test_normalized or
                    test_normalized in filter_normalized or
                    all(word in test_normalized for word in filter_normalized.split() if len(word) > 2)
                )
                if match_found:
                    filtered_test_details.append(test)
            test_details = filtered_test_details
        
        # Only pending tests
        filtered_tests = [
            test for test in test_details
            if not test.get('approve', False) and not test.get('rerun', False)
        ]
        
        if not filtered_tests:
            continue
        
        patient_data.append({
            "patient_id": current_patient_id,
            "patientname": patient_name,
            "age": patient_age,
            "barcode": barcode_val,
            "date": patient.date,
            "created_date": patient.created_date,
            "is_emergency": is_emergency,
            "patient_history": patient_history,
            "testdetails": filtered_tests
        })
    
    return JsonResponse(patient_data, safe=False)


@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def mb_approve_test_detail(request, barcode):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_mbtestvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode")
        created_date_str = update_data.get("created_date")
        test_id = update_data.get("test_id")
        approve_time = update_data.get("approve_time")  # Get from frontend
        if not (barcode and created_date_str and test_id):
            return JsonResponse({
                "error": "barcode, created_date, and test_id required"
            }, status=400)
        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
    except Exception as e:
        return JsonResponse({"error": f"Invalid request format: {str(e)}"}, status=400)
    # Query with barcode and created_date
    query = {"barcode": barcode, "created_date": created_date}
    test_value = collection.find_one(query)
    if not test_value:
        return JsonResponse({
            "error": "Patient record not found for given barcode & created_date."
        }, status=404)
    try:
        test_details = json.loads(test_value.get("testdetails", "[]"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to decode test details."}, status=500)
    # Find the test by test_id
    test_found = False
    for test_detail in test_details:
        if test_detail.get("test_id") == test_id:
            test_detail["approve"] = update_data.get("approve", False)
            if test_detail["approve"]:
                # Use approve_time from frontend if provided
                if approve_time:
                    test_detail["approve_time"] = approve_time
                if "approve_by" in update_data:
                    test_detail["approve_by"] = update_data["approve_by"]
            test_found = True
            break
    if not test_found:
        return JsonResponse({"error": "Test not found with given test_id."}, status=404)
    # Update the document
    result = collection.update_one(
        query,
        {"$set": {"testdetails": json.dumps(test_details)}}
    )
    if result.modified_count > 0:
        return JsonResponse({"message": "Test detail approved successfully."})
    return JsonResponse({"error": "Failed to update test detail."}, status=500)

@api_view(["PATCH"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def mb_rerun_test_detail(request, barcode):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db.core_mbtestvalue
    try:
        update_data = request.data
        barcode = update_data.get("barcode")
        created_date_str = update_data.get("created_date")
        test_id = update_data.get("test_id")
        rerun_time = update_data.get("rerun_time")  # Get from frontend
        if not (barcode and created_date_str and test_id):
            return JsonResponse({
                "error": "barcode, created_date, and test_id required"
            }, status=400)
        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
    except Exception as e:
        return JsonResponse({"error": f"Invalid request format: {str(e)}"}, status=400)
    # Query with barcode and created_date
    query = {"barcode": barcode, "created_date": created_date}
    test_value = collection.find_one(query)
    if not test_value:
        return JsonResponse({
            "error": "Patient record not found for given barcode & created_date."
        }, status=404)
    try:
        test_details = json.loads(test_value.get("testdetails", "[]"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Failed to decode test details."}, status=500)
    # Find the test by test_id
    test_found = False
    for test_detail in test_details:
        if test_detail.get("test_id") == test_id:
            test_detail["rerun"] = update_data.get("rerun", False)
            if test_detail["rerun"]:
                # Use rureun_time from frontend if provided
                if rerun_time:
                    test_detail["rerun_time"] = rerun_time
                if "rerun_by" in update_data:
                    test_detail["rerun_by"] = update_data["rerun_by"]
            test_found = True
            break
    if not test_found:
        return JsonResponse({"error": "Test not found with given test_id."}, status=404)
    # Update the document
    result = collection.update_one(
        query,
        {"$set": {"testdetails": json.dumps(test_details)}}
    )
    if result.modified_count > 0:
        return JsonResponse({"message": "Test detail rerun Initiated."})
    return JsonResponse({"error": "Failed to update test detail."}, status=500)
    