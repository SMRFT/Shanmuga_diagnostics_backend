from rest_framework.response import Response
from django.http import JsonResponse
from datetime import datetime
from rest_framework.decorators import api_view
from rest_framework import  status
from core.mongo_client import get_client
from rest_framework import status
from datetime import datetime, timedelta
from django.conf import settings
from django.utils.timezone import make_aware
from datetime import datetime
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ...models import Patient
from ...models import BarcodeTestDetails
from django.http import JsonResponse
from datetime import datetime, timedelta
import os, json
from django.utils.timezone import make_aware
from ...models import SampleStatus, MBTestValue, Hmssamplestatus, Hmsbarcode,Billing
from dotenv import load_dotenv
load_dotenv()
from urllib.parse import unquote_plus
import re
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from django.utils.dateparse import parse_datetime
import pytz
import gridfs
import logging
from core.pagination import paginate_queryset
# Define IST timezone
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)

logger = logging.getLogger(__name__)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def mb_patient_test_sorting(request):
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
        client = get_client()
        db = client.Diagnostics
        core_testdetails_collection = db["core_testdetails"]

        # Filter test values by barcode
        tests = MBTestValue.objects.filter(barcode=barcode, date=formatted_date).values("pk","testdetails", "created_date", "is_preliminary")
        test_list = []

        for test in tests:
            test_created_date = test.get("created_date")
            is_preliminary = test.get("is_preliminary", False)
            record_id = test.get("pk")
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

            # Filter only approved tests and enrich with test_name from core_testdetails
            for test_item in testdetails_list:
                if test_item.get('approve') is True:
                    test_id = test_item.get('test_id')

                    # Fetch test_name from core_testdetails collection
                    if test_id:
                        core_test = core_testdetails_collection.find_one(
                            {"test_id": test_id},
                            {"test_name": 1, "_id": 0}
                        )

                        if core_test:
                            test_item['test_name'] = core_test.get('test_name', 'N/A')
                        else:
                            test_item['test_name'] = 'N/A'
                    else:
                        test_item['test_name'] = 'N/A'

                    # Add created_date to each test item
                    test_item['is_preliminary'] = is_preliminary
                    test_item['record_id'] = record_id
                    test_item['created_date'] = test_created_date.isoformat() if test_created_date else None
                    test_list.append(test_item)

        # Return barcode as key
        if test_list:
            return JsonResponse({barcode: {"testdetails": test_list}})
        else:
            return JsonResponse({'error': 'No records found for this barcode'}, status=404)

    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def mb_get_patient_test_details(request):
    barcode = request.GET.get('barcode')
    record_ids_param = request.GET.get('record_ids', '')
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
        if record_ids_param:
            record_ids = [rid.strip() for rid in record_ids_param.split(',') if rid.strip()]
            test_values = MBTestValue.objects.filter(barcode=barcode, _id__in=record_ids)
        else:
            test_values = MBTestValue.objects.filter(barcode=barcode)
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

        # Connect to MongoDB
        mongo_client = get_client()
        mongo_db = mongo_client.Diagnostics
        core_testdetails_collection = mongo_db.core_testdetails

        # Connect to global database for employee profiles
        global_db = mongo_client.Global
        profile_collection = global_db.backend_diagnostics_profile
        fs = gridfs.GridFS(global_db)

        # Helper function to get parameter details by index or test_code
        def get_parameter_from_core(core_test, parameter_type, test_code=None, param_index=None):
            """
            Get parameter details from core_testdetails
            Supports both dict (parameter_type-keyed) and list formats
            Uses param_index for accurate matching when available
            """
            core_parameters = core_test.get("parameters", {})
            params_list = []

            # Case 1: parameters is a dictionary with device_id keys
            if isinstance(core_parameters, dict):
                # Try to get parameters for the specific device_id
                if parameter_type and parameter_type != "N/A" and parameter_type in core_parameters:
                    params_list = core_parameters[parameter_type]
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

        # Helper function to get employee signature data
        def get_employee_signature_data(employee_id):
            """
            Fetch employee profile and signature image from MongoDB
            Returns dict with employeeName, designation, and signature base64
            """
            if not employee_id:
                return None

            try:
                # Get employee profile
                profile = profile_collection.find_one({"employeeId": employee_id})
                if not profile:
                    return None

                employee_name = profile.get("employeeName", "")
                designation = profile.get("designation", "")
                signature_file_id = profile.get("signatureFileId")

                signature_base64 = None
                if signature_file_id:
                    try:
                        # Convert string ID to ObjectId if needed
                        from bson import ObjectId
                        if isinstance(signature_file_id, str):
                            signature_file_id = ObjectId(signature_file_id)

                        # Fetch signature image from GridFS
                        signature_file = fs.get(signature_file_id)
                        signature_bytes = signature_file.read()

                        # Convert to base64
                        import base64
                        signature_base64 = base64.b64encode(signature_bytes).decode('utf-8')
                    except Exception as e:
                        logger.error(f"Error fetching signature for employee {employee_id}: {str(e)}")

                return {
                    "employeeName": employee_name,
                    "designation": designation,
                    "signatureBase64": signature_base64
                }
            except Exception as e:
                logger.error(f"Error fetching employee data for {employee_id}: {str(e)}")
                return None

        all_results = []
        # Collect all unique approvers across all test values
        all_approvers = set()

        # Process each TestValue record
        for test_value_record in test_values:
            # Filter for approved tests only
            approved_tests = []

            # Parse testdetails if it's a string
            test_details_list = test_value_record.testdetails
            if isinstance(test_details_list, str):
                try:
                    test_details_list = json.loads(test_details_list)
                except Exception as e:
                    logger.exception(f"Failed to parse test_details_list JSON: {e}")
                    test_details_list = []

            if not isinstance(test_details_list, list):
                test_details_list = []

            for test in test_details_list:
                # Check if the test is approved
                if test.get("approve") == True:  # Only include approved tests
                    test_id = test.get("test_id")
                    parameter_type = test.get("parameter_type")
                    parameters = test.get("parameters", [])
                    approve_by = test.get("approve_by", "")

                    # Collect approver ID
                    if approve_by:
                        all_approvers.add(approve_by)

                    # Fetch test details from core_testdetails
                    core_test = core_testdetails_collection.find_one({"test_id": test_id})

                    if not core_test:
                        # Fallback to original data if core_test not found
                        testname = test.get("testname")
                        department = test.get("department", "N/A")
                        specimen_type = test.get("specimen_type", "N/A")
                        is_AG_title = test.get("is_AG_title", "N/A")
                    else:
                        testname = core_test.get("test_name")
                        department = core_test.get("department", "N/A")
                        specimen_type = core_test.get("specimen_type", "N/A")
                        is_AG_title = core_test.get("is_AG_title", False)

                    comment = test.get("comment", "")
                    remarks = test.get("remarks", "")
                    colony_count = test.get("colony_count", "")
                    verified_by = test.get("verified_by", "N/A")
                    approve_time = test.get("approve_time", "N/A")
                    dispatch_time = test.get("dispatch_time", "N/A")

                    # Get sample status information
                    status = None
                    if sample_status.exists():
                        for sample_status_record in sample_status:
                            status_details = sample_status_record.testdetails
                            if isinstance(status_details, str):
                                try:
                                    status_details = json.loads(status_details)
                                except Exception as e:
                                    logger.exception(f"Failed to parse status_details JSON: {e}")
                                    status_details = []

                            if isinstance(status_details, list):
                                status = next(
                                    (s for s in status_details if s.get("test_id") == test_id or s.get("testname") == testname),
                                    None
                                )
                            if status:
                                break

                    samplecollected_time = status.get("samplecollected_time") if status else None
                    received_time = status.get("received_time") if status else None

                    # Build simplified test_detail with only required fields
                    test_detail = {
                        "test_id": test_id,
                        "parameter_type": parameter_type,
                        "record_id": str(test_value_record._id),              # ← ADD
                        "is_preliminary": test.get("is_preliminary", False),  # ← ADD
                        "testname": testname,
                        "department": department,
                        "specimen_type": specimen_type,
                        "is_AG_title": is_AG_title,
                        "remarks": remarks,
                        "colony_count": colony_count,
                        "verified_by": verified_by,
                        "approve_by": approve_by,  # Include approve_by in response
                        "approve_time": approve_time,
                        "dispatch_time": dispatch_time,
                        "samplecollected_time": samplecollected_time,
                        "received_time": received_time
                    }

                    # Handle parameters - extract only essential fields
                    if parameters and len(parameters) > 0 and core_test:
                        simplified_parameters = []

                        # Use index-based matching for accurate parameter retrieval
                        for param_index, param_value in enumerate(parameters):
                            test_code = param_value.get("test_code")
                            value = param_value.get("value", "")
                            result = param_value.get("result", "")
                            param_comment = param_value.get("comment", "")

                            # Get parameter definition using INDEX (most accurate)
                            param_def = get_parameter_from_core(
                                core_test,
                                parameter_type,
                                test_code=test_code,
                                param_index=param_index
                            )

                            if param_def:
                                simplified_param = {
                                    "test_name": param_def.get("test_name", ""),
                                    "test_code": test_code,
                                    "value": value,
                                    "result": result,
                                    "comment": param_comment
                                }
                                simplified_parameters.append(simplified_param)
                            else:
                                # Fallback if parameter definition not found
                                simplified_param = {
                                    "test_name": param_value.get("name", "N/A"),
                                    "test_code": test_code,
                                    "value": value,
                                    "result": result,
                                    "comment": param_comment
                                }
                                simplified_parameters.append(simplified_param)

                        test_detail["parameters"] = simplified_parameters
                    elif parameters and len(parameters) > 0:
                        # No core_test found, use original parameters with simplified fields
                        simplified_parameters = []
                        for param_value in parameters:
                            simplified_param = {
                                "test_name": param_value.get("name", "N/A"),
                                "test_code": param_value.get("test_code", ""),
                                "value": param_value.get("value", ""),
                                "result": param_value.get("result", ""),
                                "comment": param_value.get("comment", "")
                            }
                            simplified_parameters.append(simplified_param)
                        test_detail["parameters"] = simplified_parameters
                    else:
                        # No parameters - single test with value
                        test_detail["value"] = test.get("value", "")
                        test_detail["result"] = test.get("result", "")

                    approved_tests.append(test_detail)

            # Only add patient details if there are approved tests
            if approved_tests:
                patient_details = {
                    "patient_id": patient_id,
                    "patientname": patient.patientname if patient else "N/A",
                    "age": patient.age if patient else "N/A",
                    "age_type": patient.age_type if patient else "Years",
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

        # Fetch signature data for all approvers
        signatures_data = []
        for approver_id in all_approvers:
            sig_data = get_employee_signature_data(approver_id)
            if sig_data:
                signatures_data.append(sig_data)

        # Prepare final response
        response_data = {
            "patient_data": all_results[0] if len(all_results) == 1 else all_results,
            "signatures": signatures_data
        }

        return JsonResponse(response_data, safe=False)
    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)
