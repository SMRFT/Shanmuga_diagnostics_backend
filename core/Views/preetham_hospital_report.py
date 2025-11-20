from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from pymongo import MongoClient
from datetime import datetime, timedelta
import os
import json
import traceback

# auth
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission

@api_view(['GET'])
@csrf_exempt
def preetham_hospital_report(request):
    """
    Simplified report for PREETHAM HOSPITAL B2B patients
    Filters by date range and B2B = "PREETHAM HOSPITAL"
    """
    try:
        # MongoDB setup
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        billing_collection = db["core_billing"]

        # Get date filters
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")

        # Validate dates
        if not from_date or not to_date:
            return JsonResponse(
                {"error": "Both 'from_date' and 'to_date' are required (YYYY-MM-DD format)"},
                status=400
            )

        try:
            from_date_parsed = datetime.strptime(from_date, "%Y-%m-%d")
            to_date_parsed = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            return JsonResponse(
                {"error": "Invalid date format. Use YYYY-MM-DD."},
                status=400
            )

        # Query: Filter by date range AND B2B = "PREETHAM HOSPITAL"
        query = {
            "date": {"$gte": from_date_parsed, "$lt": to_date_parsed},
            "B2B": "PREETHAM HOSPITAL"
        }

        billing_records = list(billing_collection.find(query))

        if not billing_records:
            return JsonResponse([], safe=False)

        # Format response data
        formatted_data = []
        for record in billing_records:
            patient_id = record.get("patient_id", "N/A")
            
            # Parse amounts
            try:
                total_amount = int(float(record.get("totalAmount", 0) or 0))
            except:
                total_amount = 0

            try:
                discount = int(float(record.get('discount', 0) or 0))
            except:
                discount = 0

            # Parse test names
            test_names_str = record.get("test_names", "")
            test_list = [name.strip() for name in test_names_str.split(",") if name.strip()] if test_names_str else []

            # Format date
            formatted_date = record["date"].strftime("%Y-%m-%d") if record.get("date") else "N/A"

            formatted_data.append({
                "date": formatted_date,
                "patient_id": patient_id,
                "patient_name": record.get("patientname", "N/A"),
                "age": record.get("age", "N/A"),
                "gender": record.get("gender", "N/A"),
                "phone": record.get("phone", "N/A"),
                "bill_no": record.get("bill_no", "N/A"),
                "barcode": record.get("barcode", "N/A"),
                "test_names": test_list,
                "no_of_tests": len(test_list),
                "total_amount": total_amount,
                "discount": discount,
                "status": record.get("status", "Registered"),
                "refby": record.get("refby", "N/A"),
                "sample_collector": record.get("sample_collector", "N/A"),
            })

        return JsonResponse(formatted_data, safe=False)

    except Exception as e:
        print(f"Error in preetham_hospital_report: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)
    
    
# from ..models import Patient, SampleStatus, Billing, TestValue, BarcodeTestDetails
# @api_view(['GET'])
# @permission_classes([HasRoleAndDataPermission])
# def get_patient_test_details(request):
#     barcode = request.GET.get('barcode')
#     # Check if barcode is provided
#     if not barcode:
#         return JsonResponse({'error': 'Barcode is required'}, status=400)
#     try:
#         # Get patient_id and bill_no from BarcodeTestDetails using barcode
#         barcode_details = BarcodeTestDetails.objects.filter(barcode=barcode).first()
#         if not barcode_details:
#             return JsonResponse({'error': 'No barcode details found for the given barcode'}, status=404)
#         patient_id = barcode_details.patient_id
#         bill_no = barcode_details.bill_no
#         # Get TestValue records using patient_id and barcode
#         test_values = TestValue.objects.filter( barcode=barcode)
#         if not test_values.exists():
#             return JsonResponse({'error': 'No test records found for the given barcode'}, status=404)
#         # Get patient details from Patient model using patient_id
#         patient = Patient.objects.filter(patient_id=patient_id).first()
#         # Get billing details from Billing model using bill_no
#         billing = Billing.objects.filter(bill_no=bill_no).first()
#         # Get sample status
#         sample_status = SampleStatus.objects.filter(patient_id=patient_id)
#         # Get barcodes information
#         barcodes = []
#         try:
#             tests = json.loads(barcode_details.testdetails) if isinstance(barcode_details.testdetails, str) else barcode_details.testdetails
#             barcodes = [test.get("barcode") for test in tests if test.get("barcode")]
#         except (json.JSONDecodeError, AttributeError):
#             barcodes = []
#         all_results = []
#         # Process each TestValue record
#         for test_value_record in test_values:
#             # Filter for approved tests only
#             approved_tests = []
#             for test in test_value_record.testdetails:
#                 # Check if the test is approved
#                 if test.get("approve") == True:  # Only include approved tests
#                     testname = test.get("testname")
#                     department = test.get("department", "N/A")
#                     NABL = test.get("NABL", "N/A")
#                     verified_by = test.get("verified_by", "N/A")
#                     approve_by = test.get("approve_by", "N/A")
#                     approve_time = test.get("approve_time", "N/A")
#                     parameters = test.get("parameters", [])
#                     # Get sample status information
#                     status = None
#                     if sample_status.exists():
#                         for sample_status_record in sample_status:
#                             status = next(
#                                 (status for status in sample_status_record.testdetails
#                                  if status.get("testname") == testname), None)
#                             if status:
#                                 break
#                     samplecollected_time = status.get("samplecollected_time") if status else None
#                     received_time = status.get("received_time") if status else None
#                     test_detail = {
#                         "department": department,
#                         "NABL": NABL,
#                         "testname": testname,
#                         "verified_by": verified_by,
#                         "approve_by": approve_by,
#                         "approve_time": approve_time,
#                         "samplecollected_time": samplecollected_time,
#                         "received_time": received_time
#                     }
#                     if parameters:
#                         test_detail["parameters"] = parameters
#                     else:
#                         test_detail.update({
#                             "method": test.get("method", ""),
#                             "specimen_type": test.get("specimen_type", ""),
#                             "value": test.get("value", ""),
#                             "unit": test.get("unit", ""),
#                             "reference_range": test.get("reference_range", ""),
#                             "sub_title": test.get("sub_title", "")
#                         })
#                     approved_tests.append(test_detail)
#             # Only add patient details if there are approved tests
#             if approved_tests:
#                 patient_details = {
#                     "patient_id": patient_id,
#                     "patientname": patient.patientname,
#                     "age": patient.age,
#                     "age_type": patient.age_type,
#                     "gender": patient.gender if patient else "N/A",
#                     "date": test_value_record.date,
#                     "barcode": test_value_record.barcode,
#                     "bill_no": bill_no,
#                     "barcodes": barcodes,
#                     "testdetails": approved_tests,
#                     "refby": billing.refby if billing else "N/A",
#                     "B2B": billing.B2B if billing else False,
#                     "branch": billing.branch if billing else "N/A",
#                 }
#                 all_results.append(patient_details)
#         if not all_results:
#             return JsonResponse({'error': 'No approved test records found'}, status=404)
#         # If only one result, return it directly; otherwise return array
#         if len(all_results) == 1:
#             return JsonResponse(all_results[0], safe=False)
#         else:
#             return JsonResponse(all_results, safe=False)
#     except Exception as e:
#         return JsonResponse({'error': str(e)}, status=500)
