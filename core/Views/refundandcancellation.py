from rest_framework.response import Response
from django.http import JsonResponse
import json
import logging
from urllib.parse import quote_plus
from core.mongo_client import get_client
import certifi
from ..models import Billing, Patient
from core.pagination import paginate_queryset
from datetime import datetime
from django.utils.timezone import make_aware
import random
from django.db.models import Q
from django.core.mail import send_mail
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings  #
from datetime import datetime, date 
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
import os
#auth

from ..serializers import BillingSerializer
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def search_refund(request):
    if request.method == "GET":
        patient_id = request.GET.get('patient_id')
        select_date = request.GET.get('date')  # Expected in YYYY-MM-DD format
        if not patient_id or not select_date:
            return JsonResponse({"error": "Patient ID and Date are required"}, status=400)

        try:
            selected_date = datetime.strptime(select_date, "%Y-%m-%d")
            start_of_day = make_aware(datetime.combine(selected_date, datetime.min.time()))
            end_of_day = make_aware(datetime.combine(selected_date, datetime.max.time()))

            patients = Billing.objects.filter(
                patient_id=patient_id,
                date__gte=start_of_day,
                date__lt=end_of_day
            )

            # Paginate before serializing so enrichment below only runs on the current page
            page_obj, page_meta = paginate_queryset(patients, request)

            # Use serializer so patientname is included
            serializer = BillingSerializer(page_obj, many=True)
            result = serializer.data

            # MongoDB connection to get test details
            client = get_client()
            db = client.Diagnostics
            tests_collection = db["core_testdetails"]

            # Process refund filtering
            for patient in result:
                if 'testdetails' in patient and isinstance(patient['testdetails'], list):
                    # Enrich test details with test_name from test master
                    for test in patient['testdetails']:
                        if 'test_id' in test:
                            test_master = tests_collection.find_one({"test_id": test['test_id']})
                            if test_master:
                                test['test_name'] = test_master.get('test_name', 'Unknown Test')
                                test['test_code'] = test_master.get('test_code', '')
                                # Use MRP from test master if not in testdetails
                                if 'MRP' not in test or not test['MRP']:
                                    test['MRP'] = test_master.get('MRP', 0)
                    
                    all_refunded = all(test.get('refund', False) for test in patient['testdetails'])

                    if all_refunded:
                        patient['all_refunded'] = True
                        patient['testdetails'] = []
                    else:
                        patient['all_refunded'] = False
                        patient['testdetails'] = [
                            test for test in patient['testdetails'] if not test.get('refund', False)
                        ]

            return JsonResponse({"patients": result, **page_meta}, safe=False)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({"error": str(e)}, status=500)



# Temporary dictionary to hold OTPs (non-persistent)
otp_storage_refund = {}

@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def generate_otp_refund(request):
    if request.method == "POST":
        try:
            data = request.data 
            email = data.get("email")
            patient_details = data.get("patient_details", {})

            if not email:
                return JsonResponse({"error": "Email is required"}, status=400)
           
            otp = str(random.randint(100000, 999999))  # Generate 6-digit OTP
            otp_storage_refund[email] = otp  # Store in a temporary dictionary
           
            # Construct a professional email message with patient details
            subject = "Refund Verification OTP"
            message = f"""Hi Sir/ Madam,

A refund request has been initiated with the following details:

Patient Information:
- Patient ID: {patient_details.get('patient_id', 'N/A')}
- Patient Name: {patient_details.get('patient_name', 'N/A')}

Refund Details:
- Tests: {patient_details.get('tests', 'N/A')}
- Total Refund Amount: ₹{patient_details.get('total_refund_amount', 'N/A')}

Reason for Refund:
{patient_details.get('reason', 'No reason provided')}

Your OTP for verifying this refund is: {otp}

Please enter this OTP to process the refund. 
This OTP will expire shortly.

Best regards,
Shanmuga Diagnostics"""

            from_email = settings.EMAIL_HOST_USER

            try:
                send_mail(subject, message, from_email, [email])
                return JsonResponse({
                    "message": "OTP sent successfully", 
                    "otp": otp  # Only for testing, remove in production
                }, status=200)
            except Exception as e:
                return JsonResponse({"error": str(e)}, status=500)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Invalid request method."}, status=405)

@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def verify_and_process_refund(request):
    try:
        data = request.data   # ✅ DRF-safe

        email = data.get("email")
        entered_otp = str(data.get("otp"))
        patient_id = data.get("patient_id")
        selected_test_ids = data.get("selected_tests")  # Now expecting test_ids

        # ✅ Validate inputs
        if not email or not entered_otp or not patient_id or not selected_test_ids:
            return JsonResponse(
                {"error": "Email, OTP, Patient ID, and selected tests are required."},
                status=400
            )

        # ✅ Remove null/empty values from selected_test_ids
        selected_test_ids = [t for t in selected_test_ids if t is not None and str(t).strip()]

        if not selected_test_ids:
            return JsonResponse(
                {"error": "Selected tests cannot be empty."},
                status=400
            )

        # Convert to integers for comparison
        try:
            selected_test_ids = [int(tid) for tid in selected_test_ids]
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "Invalid test IDs provided."},
                status=400
            )

        # ✅ Verify OTP
        stored_otp = otp_storage_refund.get(email)
        if stored_otp is None:
            return JsonResponse({"error": "OTP expired or not found"}, status=400)

        if str(stored_otp) != entered_otp:
            return JsonResponse({"error": "Invalid OTP"}, status=400)

        # MongoDB
        client = get_client()
        db = client.Diagnostics
        patients_collection = db["core_billing"]
        tests_collection = db["core_test"]

        patient_record = patients_collection.find_one({"patient_id": patient_id})
        if not patient_record:
            return JsonResponse({"error": "Patient not found."}, status=404)

        # Parse testdetails
        testdetails_raw = patient_record.get("testdetails", [])
        if isinstance(testdetails_raw, str):
            test_list = json.loads(testdetails_raw)
        else:
            test_list = testdetails_raw

        current_datetime = datetime.now().isoformat()
        refunded_tests = []

        # Update tests by test_id
        for test in test_list:
            test_id = test.get("test_id")
            if test_id in selected_test_ids:
                test["refund"] = True
                test["refunded_date"] = current_datetime
                
                # Get test name from test master for response
                test_master = tests_collection.find_one({"test_id": test_id})
                test_name = test_master.get('test_name', f'Test ID {test_id}') if test_master else f'Test ID {test_id}'
                refunded_tests.append({
                    "test_id": test_id,
                    "test_name": test_name
                })

        # Update MongoDB
        patients_collection.update_one(
            {"patient_id": patient_id},
            {"$set": {"testdetails": json.dumps(test_list)}}
        )

        # Clear OTP after successful processing
        del otp_storage_refund[email]

        return JsonResponse({
            "message": f"Refund updated for {len(refunded_tests)} test(s)",
            "refunded_tests": refunded_tests,
            "refunded_date": current_datetime
        }, status=200)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def search_cancellation(request):
    if request.method == "GET":
        patient_id = request.GET.get('patient_id')
        select_date = request.GET.get('date')  # Expected in YYYY-MM-DD format
        if not patient_id or not select_date:
            return JsonResponse({"error": "Patient ID and Date are required"}, status=400)

        try:
            selected_date = datetime.strptime(select_date, "%Y-%m-%d")
            start_of_day = make_aware(datetime.combine(selected_date, datetime.min.time()))
            end_of_day = make_aware(datetime.combine(selected_date, datetime.max.time()))

            # Fetch billing records
            billings = Billing.objects.filter(
                patient_id=patient_id,
                bill_date__gte=start_of_day,
                bill_date__lte=end_of_day
            )

            # Paginate before serializing so enrichment below only runs on the current page
            page_obj, page_meta = paginate_queryset(billings, request)

            # Serialize to include patientname
            serializer = BillingSerializer(page_obj, many=True)
            result = serializer.data

            # MongoDB connection
            client = get_client()
            db = client.Diagnostics
            tests_collection = db["core_testdetails"]

            for patient in result:
                if 'testdetails' in patient and isinstance(patient['testdetails'], list):
                    cleaned_tests = []
                    for test in patient['testdetails']:
                        if not isinstance(test, dict):
                            continue
                        # Fix common JSON issues if test details are stored as string
                        if isinstance(test, str):
                            try:
                                test = json.loads(test)
                                if not isinstance(test, dict):
                                    continue
                            except json.JSONDecodeError:
                                logger.error(f"❌ Invalid test JSON: {test}")
                                continue

                        # Enrich test from MongoDB
                        test_id = test.get('test_id')
                        if test_id:
                            test_master = tests_collection.find_one({"test_id": test_id})
                            if test_master:
                                test['test_name'] = test_master.get('test_name', 'Unknown Test')
                                test['test_code'] = test_master.get('test_code', '')
                                if 'MRP' not in test or not test['MRP']:
                                    test['MRP'] = test_master.get('MRP', 0)
                        cleaned_tests.append(test)

                    all_cancelled = all(test.get('cancellation', False) for test in cleaned_tests)

                    patient['all_cancelled'] = all_cancelled
                    if all_cancelled:
                        patient['testdetails'] = []
                    else:
                        patient['testdetails'] = [
                            test for test in cleaned_tests if not test.get('cancellation', False)
                        ]

            # Note: `client` is the shared, pooled MongoClient — do not close it here.
            return JsonResponse({"patients": result, **page_meta}, safe=False)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({"error": str(e)}, status=500)
             

# Temporary dictionary to hold OTPs (non-persistent)
otp_storage_cancellation = {}

@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def generate_otp_cancellation(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body) if isinstance(request.body, bytes) else request.data
            email = data.get("email")
            patient_details = data.get("patient_details", {})

            if not email:
                return JsonResponse({"error": "Email is required"}, status=400)
           
            otp = str(random.randint(100000, 999999))  # Generate 6-digit OTP
            otp_storage_cancellation[email] = otp  # Store in a temporary dictionary
           
            # Construct a professional email message with patient details
            subject = "Cancellation Verification OTP"
            message = f"""Hi Sir/ Madam,

A cancellation request has been initiated with the following details:

Patient Information:
- Patient ID: {patient_details.get('patient_id', 'N/A')}
- Patient Name: {patient_details.get('patient_name', 'N/A')}

Cancellation Details:
- Tests: {patient_details.get('tests', 'N/A')}
- Total Cancellation Amount: ₹{patient_details.get('total_cancellation_amount', 'N/A')}

Reason for Cancellation:
{patient_details.get('reason', 'No reason provided')}

Your OTP for verifying this cancellation is: {otp}

Please enter this OTP to process the cancellation. 
This OTP will expire shortly.

Best regards,
Shanmuga Diagnostics"""

            from_email = settings.EMAIL_HOST_USER

            try:
                send_mail(subject, message, from_email, [email])
                return JsonResponse({
                    "message": "OTP sent successfully", 
                    "otp": otp  # Only for testing, remove in production
                }, status=200)
            except Exception as e:
                return JsonResponse({"error": str(e)}, status=500)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Invalid request method."}, status=405)

@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def verify_and_process_cancellation(request):
    try:
        data = request.data  # DRF-safe

        email = data.get("email")
        entered_otp = str(data.get("otp"))
        patient_id = data.get("patient_id")
        selected_test_ids = data.get("selected_tests")  # Now expecting test_ids

        # ✅ Validate inputs
        if not email or not entered_otp or not patient_id or not selected_test_ids:
            return JsonResponse(
                {"error": "Email, OTP, Patient ID, and selected tests are required."},
                status=400
            )

        # ✅ Remove null/empty values from selected_test_ids
        selected_test_ids = [t for t in selected_test_ids if t is not None and str(t).strip()]

        if not selected_test_ids:
            return JsonResponse(
                {"error": "Selected tests cannot be empty."},
                status=400
            )

        # Convert to integers for comparison
        try:
            selected_test_ids = [int(tid) for tid in selected_test_ids]
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "Invalid test IDs provided."},
                status=400
            )

        # ✅ Verify OTP
        stored_otp = otp_storage_cancellation.get(email)
        if stored_otp is None:
            return JsonResponse({"error": "OTP expired or not found"}, status=400)

        if str(stored_otp) != entered_otp:
            return JsonResponse({"error": "Invalid OTP"}, status=400)

        # MongoDB
        client = get_client()
        db = client.Diagnostics
        patients_collection = db["core_billing"]
        tests_collection = db["core_test"]

        # Get today's date
        current_date = datetime.now().date()
        start_time = make_aware(datetime.combine(current_date, datetime.min.time()))
        end_time = make_aware(datetime.combine(current_date, datetime.max.time()))

        # Find billing record for today
        billing = Billing.objects.filter(
            patient_id=patient_id,
            bill_date__range=(start_time, end_time)
        ).first()

        if not billing:
            return JsonResponse({"error": "Patient billing record not found for today."}, status=404)

        # Parse testdetails
        testdetails_raw = billing.testdetails
        if isinstance(testdetails_raw, str):
            test_list = json.loads(testdetails_raw)
        else:
            test_list = testdetails_raw

        current_datetime = datetime.now().isoformat()
        cancelled_tests = []
        refund_amount = 0

        # Update tests by test_id
        for test in test_list:
            test_id = test.get("test_id")
            if test_id in selected_test_ids:
                test["cancellation"] = True
                test["cancelled_date"] = current_datetime
                
                # Calculate refund amount
                amount = float(test.get('amount', 0) or test.get('MRP', 0))
                refund_amount += amount
                
                # Get test name from test master for response
                test_master = tests_collection.find_one({"test_id": test_id})
                test_name = test_master.get('test_name', f'Test ID {test_id}') if test_master else f'Test ID {test_id}'
                cancelled_tests.append({
                    "test_id": test_id,
                    "test_name": test_name,
                    "amount": amount
                })

        if refund_amount == 0:
            return JsonResponse({"error": "No matching tests found for cancellation."}, status=400)

        # Update MongoDB (core_billing collection)
        patient_record = patients_collection.find_one({"patient_id": patient_id})
        if patient_record:
            patients_collection.update_one(
                {"patient_id": patient_id},
                {"$set": {"testdetails": json.dumps(test_list)}}
            )

        # Update Django Billing model
        billing.testdetails = json.dumps(test_list)
        
        # Update total amount
        current_total = float(billing.totalAmount or 0)
        updated_total = max(0, current_total - refund_amount)
        billing.totalAmount = str(updated_total)
        
        # Update credit amount if applicable
        if hasattr(billing, 'credit_amount') and billing.credit_amount:
            current_credit = float(billing.credit_amount or 0)
            updated_credit = max(0, current_credit - refund_amount)
            billing.credit_amount = str(updated_credit)
        
        billing.save()

        # Clear OTP after successful processing
        del otp_storage_cancellation[email]

        return JsonResponse({
            "message": f"Cancellation processed successfully for {len(cancelled_tests)} test(s)",
            "refund_amount": refund_amount,
            "cancelled_tests": cancelled_tests,
            "cancelled_date": current_datetime
        }, status=200)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def logs_api(request):
    """Combined API endpoint for both refund and cancellation logs"""
    try:
        client = get_client()
        db = client.Diagnostics
        patient_collection = db['core_billing']
        
        # Get query parameters
        log_type = request.GET.get('type', 'refund')  # Default to refund if not specified
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        search = request.GET.get('search', '').strip().lower()
        
        # Base query - default to current date if no dates provided
        query = {}
        
        # Apply date filters
        if start_date or end_date:
            query['date'] = {}
            if start_date:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                query['date']['$gte'] = start_date
            if end_date:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                # Add 1 day to end_date to include the full day
                end_date = end_date.replace(hour=23, minute=59, second=59)
                query['date']['$lte'] = end_date
        else:
            # Default to current date if no dates provided
            today = datetime.now()
            today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today.replace(hour=23, minute=59, second=59, microsecond=999999)
            query['date'] = {'$gte': today_start, '$lte': today_end}
        
        patients = list(patient_collection.find(query))
        results = []
        
        for patient in patients:
            try:
                # Parse testdetails JSON string (not testname)
                tests = json.loads(patient.get('testdetails', '[]'))
                
                if log_type == 'refund':
                    filtered_tests = [test for test in tests if test.get('refund') is True]
                elif log_type == 'cancellation':
                    filtered_tests = [test for test in tests if test.get('cancellation') is True]
                else:
                    filtered_tests = []
                
                if filtered_tests:
                    total_amount = sum(float(test.get('MRP', 0)) for test in filtered_tests)
                    test_details = [
                        f"{test.get('test_name', 'Unknown Test')} (₹{float(test.get('MRP', 0)):.2f})"
                        for test in filtered_tests
                    ]
                    
                    results.append({
                        'id': str(patient.get('_id')),
                        'patient_id': patient.get('patient_id'),
                        'patientname': patient.get('patientname'),
                        'bill_no': patient.get('bill_no'),
                        'date': patient.get('date').isoformat() if isinstance(patient.get('date'), datetime) else str(patient.get('date')),
                        'testname': ", ".join(test_details),
                        'amount': total_amount,
                        'tests': filtered_tests,
                        'count': len(filtered_tests),
                        'reason': patient.get(f"{log_type}_reason", f"Test {log_type.title()}ed")
                    })
            
            except (json.JSONDecodeError, AttributeError, KeyError) as e:
                logger.error(f"Error processing patient {patient.get('_id')}: {str(e)}")
                continue

        if search:
            results = [
                r for r in results
                if search in (r.get('patientname') or '').lower()
                or search in str(r.get('bill_no') or '').lower()
                or search in (r.get('testname') or '').lower()
            ]

        page_obj, page_meta = paginate_queryset(results, request)
        # Response shape changed for pagination: previously returned a bare JSON array,
        # now returns {"data": [...], "total_pages":.., "current_page":.., "total_count":..}.
        return JsonResponse({"data": list(page_obj), **page_meta}, safe=False)
    
    except Exception as e:
        logger.error(f"Error in logs_api: {str(e)}")
        return JsonResponse({"error": str(e)}, status=500)

