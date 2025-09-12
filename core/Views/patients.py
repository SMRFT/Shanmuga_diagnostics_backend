from rest_framework.response import Response
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from rest_framework import status
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta
from django.db.models import Max
from django.forms.models import model_to_dict
from django.db import transaction
import json
import re
from pymongo import MongoClient
from bson import ObjectId
import os
import traceback

# auth
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission

from ..serializers import PatientSerializer, BillingSerializer
from ..models import Patient, Billing,ClinicalName,RefBy

@csrf_exempt
@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def create_patient(request):
    try:
        data = request.data.copy()
        patient_id = data.get("patient_id")
        if not patient_id:
            return Response({"error": "patient_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        if Patient.objects.filter(patient_id=patient_id).exists():
            return Response({"error": "Patient already exists"}, status=status.HTTP_400_BAD_REQUEST)

        # Extract employee_id from request header/body
        employee_id = (
            request.data.get('auth-user-id') or
            request.headers.get('auth-user-id') or
            "system"
        )

        # create_patient
        patient_data = {
            "patient_id": patient_id,
            "patientname": data.get("patientname"),
            "age": data.get("age"),
            "age_type": data.get("age_type", "Year"),
            "gender": data.get("gender"),
            "phone": data.get("phone", ""),
            "email": data.get("email", ""),
            # ✅ ensure JSONField always gets dict or None, not ""
            "address": data.get("address") if isinstance(data.get("address"), dict) else {},
            "created_by": employee_id,  # Use auth-user-id
            "lastmodified_by": employee_id,  # Use auth-user-id
            "lastmodified_date": timezone.now(),
        }

        serializer = PatientSerializer(data=patient_data)
        if serializer.is_valid():
            patient = serializer.save()
            return Response({
                "success": True,
                "message": "Patient created successfully",
                "patient_id": patient_id,
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)
        else:
            return Response({
                "success": False,
                "error": "Patient creation failed",
                "details": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

@csrf_exempt
@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def create_bill(request):
    """
    Create billing record ONLY. Do not set bill_no or bill_date here
    (they are generated at update time).
    """
    try:
        data = request.data.copy()
        patient_id = data.get("patient_id")
        if not patient_id:
            return Response({"error": "patient_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            patient = Patient.objects.get(patient_id=patient_id)
        except Patient.DoesNotExist:
            return Response({"success": False, "error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)

        # Extract employee_id from request header/body
        employee_id = (
            request.data.get('auth-user-id') or
            request.headers.get('auth-user-id') or
            "system"
        )

        raw_date = data.get("date")
        billing_date = timezone.now()
        if raw_date:
            try:
                if 'T' in raw_date:
                    # ISO format
                    billing_date = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
                    if timezone.is_naive(billing_date):
                        billing_date = timezone.make_aware(billing_date, timezone.get_current_timezone())
                else:
                    # "YYYY-MM-DD HH:mm:ss"
                    dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
                    billing_date = timezone.make_aware(dt, timezone.get_current_timezone())
            except Exception:
                billing_date = timezone.now()

        def s(val, default="0"):
            if val is None or val == "":
                return str(default)
            try:
                return str(val)
            except Exception:
                return str(default)

        # create_bill
        billing_data = {
            "patient_id": patient_id,
            "date": billing_date,
            "lab_id": data.get("lab_id", ""),
            "segment": data.get("segment", "Walk-in"),
            "B2B": data.get("B2B", ""),
            "salesMapping": data.get("salesMapping", ""),
            "sample_collector": data.get("sample_collector", ""),
            "refby": data.get("refby", ""),
            "branch": data.get("branch", ""),
            "testdetails": data.get("testdetails") if isinstance(data.get("testdetails"), (list, dict)) else [],
            "totalAmount": s(data.get("totalAmount"), "0"),
            "discount": s(data.get("discount"), "0"),
            "payment_method": data.get("payment_method") if isinstance(data.get("payment_method"), dict) else {},
            "MultiplePayment": data.get("MultiplePayment") if isinstance(data.get("MultiplePayment"), list) else [],
            "credit_amount": s(data.get("credit_amount"), "0"),
            "status": data.get("status", "Registered"),
            "created_by": employee_id,  # Use auth-user-id
            "lastmodified_by": employee_id,  # Use auth-user-id
            "lastmodified_date": timezone.now(),
        }

        serializer = BillingSerializer(data=billing_data)
        if serializer.is_valid():
            billing = serializer.save()
            return Response({
                "success": True,
                "message": "Bill created successfully",
                "patient_id": patient_id,
                "bill_id": str(billing.id),
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)

        else:
            return Response({
                "success": False,
                "error": "Bill creation failed",
                "details": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_latest_patient_id(request):
    try:
        max_patient = Patient.objects.aggregate(max_pid=Max('patient_id'))['max_pid']
        max_num = 0
        if max_patient:
            match = re.match(r'^SD0*(\d+)$', max_patient, re.IGNORECASE)
            if match:
                max_num = int(match.group(1))
        new_patient_id = f"SD{max_num + 1:04d}"
        return Response({"patient_id": new_patient_id}, status=200)
    except Exception as e:
        return Response({"success": False, "error": "Failed to generate patient ID", "details": str(e)}, status=500)

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_latest_bill_no(request):
    try:
        today = datetime.now().strftime('%Y%m%d')
        last_bill = Billing.objects.filter(bill_no__startswith=today).order_by("-bill_no").first()
        next_id = (int(last_bill.bill_no[-4:]) + 1) if last_bill else 1
        return Response({"success": True, "bill_no": f"{today}{next_id:04d}"}, status=200)
    except Exception as e:
        return Response({"success": False, "error": "Failed to generate bill number", "details": str(e)}, status=500)


@csrf_exempt
@api_view(['PUT'])
@permission_classes([HasRoleAndDataPermission])
def update_bill(request):
    """
    Updated bill update function to handle MongoDB collection updates ONLY:
    - Updates existing MongoDB document only
    - Does NOT create new documents
    - Does NOT sync with Django model
    - All fields stored as JSON strings: testdetails, payment_method, MultiplePayment
    - Properly calculates and stores netAmount and credit_amount
    - If multiple records exist for patient_id + date, prefer updating 'Registered' record
    """
    try:
        # Extract employee_id from request header/body
        employee_id = (
            request.data.get('auth-user-id') or
            request.headers.get('auth-user-id') or
            "system"
        )
        # MongoDB connection
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_billing
        bill_id = request.data.get("bill_id")
        patient_id = request.data.get("patient_id")
        bill_date_str = request.data.get("date")
        print(f"Received update request - bill_id: {bill_id}, patient_id: {patient_id}")
        query = {}
        # Build query to find the EXISTING record
        if bill_id:
            try:
                if isinstance(bill_id, str) and len(bill_id) == 24:
                    query = {"_id": ObjectId(bill_id)}
                elif isinstance(bill_id, dict) and "$oid" in bill_id:
                    query = {"_id": ObjectId(bill_id["$oid"])}
                else:
                    query = {"_id": ObjectId(str(bill_id))}
                print(f"Using bill_id query: {query}")
            except Exception as e:
                print(f"Error creating ObjectId from bill_id {bill_id}: {e}")
                return Response({"error": f"Invalid bill_id format: {bill_id}"}, status=400)
        else:
            if not patient_id:
                return Response({"error": "Provide bill_id or patient_id"}, status=400)
            query = {"patient_id": patient_id}
            if bill_date_str:
                try:
                    if isinstance(bill_date_str, dict) and "$date" in bill_date_str:
                        dt = datetime.fromisoformat(bill_date_str["$date"].replace('Z', ''))
                    elif 'T' in str(bill_date_str):
                        dt = datetime.fromisoformat(str(bill_date_str).replace('Z', ''))
                    else:
                        dt = datetime.strptime(str(bill_date_str), "%Y-%m-%d")
                    start_date = dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    end_date = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                    query["date"] = {"$gte": start_date, "$lte": end_date}
                    print(f"Using patient_id + date query: {query}")
                except Exception as e:
                    print(f"Date parsing error: {e}")
                    print(f"Using patient_id only query: {query}")
        print(f"Final MongoDB Query: {query}")
        # ====== Find the existing record(s) ======
        billing_record = None
        if bill_id:
            billing_record = collection.find_one(query)
        else:
            matching_records = list(collection.find(query).sort("date", -1))
            if not matching_records:
                return Response({"error": "Billing record not found. Cannot update non-existing record."}, status=404)
            # Prefer Registered record if exists
            registered_record = next((rec for rec in matching_records if rec.get("status") == "Registered"), None)
            billing_record = registered_record if registered_record else matching_records[0]
        if not billing_record:
            return Response({"error": "Billing record not found"}, status=404)
        record_id = billing_record["_id"]
        bill_no = billing_record.get('bill_no')
        bill_date = billing_record.get('bill_date')
        # Keep existing bill_no and bill_date if missing
        if not bill_no:
            today = datetime.now().strftime('%Y%m%d')
            last_bill_cursor = collection.find(
                {"bill_no": {"$regex": f"^{today}"}},
                {"bill_no": 1}
            ).sort("bill_no", -1).limit(1)
            last_bill_list = list(last_bill_cursor)
            next_id = (int(last_bill_list[0]['bill_no'][-4:]) + 1) if last_bill_list else 1
            bill_no = f"{today}{next_id:04d}"
        if not bill_date:
            bill_date = datetime.now()
        # ====== Process testdetails ======
        testdetails = request.data.get("testdetails", [])
        if isinstance(testdetails, list):
            testdetails_json = json.dumps(testdetails)
        elif isinstance(testdetails, str):
            testdetails_json = testdetails
        else:
            testdetails_json = json.dumps([])
        # ====== Amount Calculations ======
        total_amount = float(request.data.get("totalAmount", "0"))
        discount = float(request.data.get("discount", "0"))
        net_amount = total_amount - discount
        if net_amount < 0:
            net_amount = 0
        # ====== Payment Method Handling ======
        payment_method_data = request.data.get("payment_method", {})
        multiple_payment_data = request.data.get("MultiplePayment")
        credit_amount = 0
        update_data = {
            "bill_no": bill_no,
            "bill_date": bill_date,
            "testdetails": testdetails_json,
            "totalAmount": str(total_amount),
            "netAmount": str(net_amount),
            "discount": str(discount),
            "credit_amount": str(credit_amount),
            "status": "Billed",   # Always update to billed
            "lastmodified_by": employee_id,
            "lastmodified_date": datetime.now(),
        }
        if isinstance(payment_method_data, dict):
            payment_method = payment_method_data.get("paymentmethod", "")
            if payment_method == "Multiple Payment":
                if isinstance(multiple_payment_data, str):
                    try:
                        processed_multiple_payments = json.loads(multiple_payment_data)
                    except:
                        processed_multiple_payments = []
                elif isinstance(multiple_payment_data, list):
                    processed_multiple_payments = []
                    for payment in multiple_payment_data:
                        processed_payment = {
                            "amount": str(payment.get("amount", "0")),
                            "paymentMethod": payment.get("paymentMethod", "Cash"),
                            "paymentDetails": payment.get("paymentDetails", "")
                        }
                        processed_multiple_payments.append(processed_payment)
                else:
                    processed_multiple_payments = []
                update_data["MultiplePayment"] = json.dumps(processed_multiple_payments)
                update_data["payment_method"] = json.dumps({"paymentmethod": "Multiple Payment"})
            else:
                if payment_method == "Credit":
                    credit_amount = net_amount
                payment_method_obj = {
                    "paymentmethod": payment_method,
                    "paymentDetails": payment_method_data.get("paymentDetails", "")
                }
                update_data["payment_method"] = json.dumps(payment_method_obj)
                update_data["MultiplePayment"] = json.dumps([])
                update_data["credit_amount"] = str(credit_amount)
        # ====== Update MongoDB Document ======
        result = collection.update_one({"_id": record_id}, {"$set": update_data})
        if result.matched_count == 0:
            return Response({"error": "Failed to find billing record for update"}, status=500)
        # ====== Prepare Response ======
        try:
            response_multiple_payment = json.loads(update_data.get("MultiplePayment", "[]"))
        except:
            response_multiple_payment = []
        try:
            response_payment_method = json.loads(update_data.get("payment_method", "{}"))
        except:
            response_payment_method = {}
        try:
            response_testdetails = json.loads(update_data.get("testdetails", "[]"))
        except:
            response_testdetails = []
        return Response({
            "success": True,
            "message": "Bill updated successfully",
            "bill_no": bill_no,
            "bill_date": bill_date.isoformat() if bill_date else None,
            "data": {
                "bill_id": str(record_id),
                "patient_id": billing_record.get("patient_id"),
                "totalAmount": update_data["totalAmount"],
                "netAmount": update_data["netAmount"],
                "discount": update_data["discount"],
                "credit_amount": update_data["credit_amount"],
                "payment_method": response_payment_method,
                "MultiplePayment": response_multiple_payment,
                "testdetails": response_testdetails
            }
        }, status=200)
    except Exception as e:
        print(f"Error in update_bill: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=500)
        

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def patient_get(request):
    try:
        patient_id = request.GET.get('patient_id')
        phone = request.GET.get('phone')
        patientname = request.GET.get('patientname')
        
        patient = None
        
        if patient_id:
            patient = Patient.objects.filter(patient_id=patient_id).first()
        elif phone:
            patient = Patient.objects.filter(phone=phone).first()
        elif patientname:
            patient = Patient.objects.filter(patientname__icontains=patientname).first()
        else:
            return Response({'success': False, 'error': 'Please provide patient_id, phone, or patientname'}, status=400)
        
        if patient:
            serializer = PatientSerializer(patient)
            data = serializer.data
            return Response({'success': True, 'data': data, 'patient_id': patient.patient_id}, status=200)
        else:
            return Response({'success': False, 'error': 'Patient not found'}, status=404)
    except Exception as e:
        return Response({'success': False, 'error': 'Internal server error', 'details': str(e)}, status=500)

from django.utils.timezone import make_aware
from datetime import datetime, timedelta
from django.forms.models import model_to_dict


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_patients_by_date(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    single_date = request.GET.get('date')

    if single_date and not (start_date and end_date):
        start_date = single_date
        end_date = single_date

    if start_date and end_date:
        try:
            # Convert to timezone-aware datetimes
            start_date_parsed = make_aware(datetime.strptime(start_date, '%Y-%m-%d'))
            end_date_parsed = make_aware(
                datetime.strptime(end_date, '%Y-%m-%d')
                + timedelta(days=1) - timedelta(seconds=1)
            )

            # Query Billing records
            patients = Billing.objects.filter(
                date__gte=start_date_parsed,
                date__lte=end_date_parsed
            )

            patient_data = []
            for patient in patients:
                try:
                    patient_dict = model_to_dict(patient)

                    # --- Match with Patient model ---
                    try:
                        patient_info = Patient.objects.get(patient_id=patient.patient_id)
                        patient_dict['patientname'] = patient_info.patientname
                        patient_dict['gender'] = patient_info.gender
                        patient_dict['age'] = patient_info.age
                    except Patient.DoesNotExist:
                        # fallback values if patient not found
                        patient_dict.setdefault('patientname', 'Unknown')
                        patient_dict.setdefault('gender', 'N/A')
                        patient_dict.setdefault('age', 'N/A')

                    # --- Handle testdetails ---
                    tests = getattr(patient, 'testdetails', [])
                    if isinstance(tests, str):
                        try:
                            tests = json.loads(tests) if tests and tests != '\"[]\"' else []
                        except json.JSONDecodeError:
                            tests = []

                    valid_tests = [
                        test for test in tests
                        if not test.get('refund', False) and not test.get('cancellation', False)
                    ]
                    patient_dict['testdetails'] = valid_tests

                    # --- Ensure required fields ---
                    patient_dict.setdefault('phone', 'N/A')
                    patient_dict.setdefault('segment', 'N/A')

                    patient_data.append(patient_dict)

                except Exception as patient_error:
                    print(f"Error processing patient {getattr(patient, 'patient_id', 'unknown')}: {patient_error}")
                    continue

            print(f"Found {len(patient_data)} patients for date range {start_date} to {end_date}")
            return JsonResponse({'success': True, 'data': patient_data}, safe=False)

        except ValueError as e:
            print(f"Date parsing error: {e}")
            return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)
        except Exception as e:
            print(f"Unexpected error: {e}")
            return JsonResponse({'error': 'An error occurred while fetching patients.'}, status=500)

    return JsonResponse({
        'error': 'start_date and end_date parameters are required, or provide a single date parameter.'
    }, status=400)




@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def patient_get(request):
    patient_id = request.GET.get('patient_id')
    phone = request.GET.get('phone')
    patientname = request.GET.get('patientname')

    try:
        patient = None

        # Check for patient_id
        if patient_id:
            patient = Patient.objects.filter(patient_id=patient_id).first()
        # Check for phone
        elif phone:
            patient = Patient.objects.filter(phone=phone).first()
        # Check for patientname (case-insensitive, partial match)
        elif patientname:
            patient = Patient.objects.filter(patientname__icontains=patientname).first()
        else:
            return JsonResponse({'error': 'Please provide either patient_id, phone, or patientname'}, status=400)

        # If patient is found, return patient data
        if patient:
            patient_data = {
                'patient_id': patient.patient_id,
                'patientname': patient.patientname,
                'age': patient.age,
                'gender': patient.gender,
                'phone': patient.phone,
                'address': patient.address,
                'email': patient.email,
   
            }
            return JsonResponse(patient_data)
        else:
            return JsonResponse({'error': 'Patient not found'}, status=404)
   
    except Exception as e:
        return JsonResponse({'error': f'Error fetching patient details: {str(e)}'}, status=500)

@api_view(['GET'])
# @permission_classes([HasRoleAndDataPermission])
def patient_overview(request):
    patients = Billing.objects.all()
    serializer = BillingSerializer(patients, many=True)  # Serialize the queryset
    return Response(serializer.data)



@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_patientsbyb2b(request):
    """Fetch patients registered on a given date with payment mode options based on segment"""
    date_str = request.GET.get('date', None)  # Get date from request parameters
    if not date_str:
        return Response({"error": "Date parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()  # Convert to date object
        next_day = selected_date + timedelta(days=1)
        patients = Billing.objects.filter(date__gte=selected_date, date__lt=next_day)
        result = []
        for patient in patients:
            patient_data = BillingSerializer(patient).data
            # Default payment options (all enabled)
            payment_options = {
                'credit': True,
                'partialpayment': True,
                'cash': True,
                'upi': True,
                'card': True,
            }
            if patient.segment == 'B2B' and patient.B2B:
                try:
                    clinical_info = ClinicalName.objects.get(referrerCode=patient.lab_id)
                    if clinical_info.b2bType == 'Cash':
                        payment_options['credit'] = False  # Disable credit
                        payment_options['partialpayment'] = False  # Disable partial payment
                except ClinicalName.DoesNotExist:
                    pass  # Keep default payment options if no matching clinical info
            patient_data['payment_options'] = payment_options
            result.append(patient_data)
        return Response(result, status=status.HTTP_200_OK)
    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)