from rest_framework.response import Response
from django.http import JsonResponse, HttpResponse, Http404
from django.views.decorators.http import require_http_methods
from rest_framework import status
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Max
from datetime import datetime, timedelta
import json
import ast
import random
from django.forms.models import model_to_dict
from core.utils import get_employee_name
from core.pagination import paginate_queryset
from django.utils.timezone import make_aware
from django.db import transaction
import json
import re
from django.utils import timezone
from core.mongo_client import get_client
from bson import ObjectId
from gridfs import GridFS
import os
import traceback
import logging
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import gridfs

# auth
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRolePermission
from dotenv import load_dotenv

load_dotenv()

from ..serializers import PatientSerializer, BillingSerializer, AppointmentSerializer
from ..models import Patient, Billing, ClinicalName, RefBy, Appointment

logger = logging.getLogger(__name__)

# MongoDB and GridFS setup
MONGO_CLIENT = get_client()
MONGO_DB = MONGO_CLIENT.Diagnostics
FS = gridfs.GridFS(MONGO_DB)


@api_view(["GET", "POST"])
@permission_classes([HasRolePermission])
def appointment_booking(request):

    employee_id = (
        request.data.get('auth-user-id') or
        request.headers.get('auth-user-id') or
        "system"
    )

    if request.method == "GET":
        appointments = Appointment.objects.all().order_by("appointment_date")
        page_obj, page_meta = paginate_queryset(appointments, request)
        serializer = AppointmentSerializer(page_obj, many=True)
        data = list(serializer.data)
        for apt in data:
            if apt.get('sample_collector'):
                apt['sample_collector'] = get_employee_name(apt['sample_collector'])
        return Response({
            "success": True,
            "appointments": data,
            **page_meta
        })

    if request.method == "POST":
        # ✅ COPY request.data instead of mutating it
        data = request.data.copy()

        data["created_by"] = employee_id

        serializer = AppointmentSerializer(data=data)

        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Appointment booked successfully!",
                "appointment": serializer.data
            }, status=status.HTTP_201_CREATED)

        return Response({
            "success": False,
            "message": "Validation failed",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PATCH"])
@permission_classes([HasRolePermission])
def cancel_appointment(request, appointment_id):
    try:
        appointment = Appointment.objects.get(appointment_id=appointment_id)
        
        # Soft delete by changing status
        appointment.status = "Cancelled"
        
        employee_id = (
            request.data.get('auth-user-id') or
            request.headers.get('auth-user-id') or
            "system"
        )
        appointment.lastmodified_by = employee_id
        appointment.lastmodified_date = timezone.now()
        
        appointment.save()
        
        return Response({
            "success": True,
            "message": "Appointment cancelled successfully"
        })
    except Appointment.DoesNotExist:
        return Response({
            "success": False,
            "message": "Appointment not found"
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            "success": False,
            "message": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasRolePermission])
def get_appointments_by_date(request):
    try:
        from_date = request.GET.get('from_date')
        to_date = request.GET.get('to_date')

        appointments = Appointment.objects.all().order_by("appointment_date")
        
        if from_date:
            appointments = appointments.filter(appointment_date__gte=from_date)
        if to_date:
            appointments = appointments.filter(appointment_date__lte=to_date)

        page_obj, page_meta = paginate_queryset(appointments, request)
        serializer = AppointmentSerializer(page_obj, many=True)
        data = list(serializer.data)
        for apt in data:
            if apt.get('sample_collector'):
                apt['sample_collector'] = get_employee_name(apt['sample_collector'])

        return Response({
            "success": True,
            "appointments": data,
            **page_meta
        })
    except Exception as e:
        return Response({
            "success": False,
            "message": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@api_view(['POST'])
@permission_classes([HasRolePermission])
def create_patient(request):
    try:
        data = request.data.copy()
        patient_id = data.get("patient_id")
        if not patient_id:
            return Response({"error": "patient_id is required"}, status=400)

        if Patient.objects.filter(patient_id=patient_id).exists():
            return Response({"error": "Patient already exists"}, status=400)

        # Extract employee ID
        employee_id = (
            request.data.get('auth-user-id') or
            request.headers.get('auth-user-id') or
            "system"
        )

        # Create patient object
        patient_data = {
            "patient_id": patient_id,
            "patientname": data.get("patientname"),
            "age": data.get("age"),
            "age_type": data.get("age_type", "Years"),
            "gender": data.get("gender"),
            "phone": data.get("phone", ""),
            "email": data.get("email", ""),
            "address": data.get("address") if isinstance(data.get("address"), dict) else {},
            "created_by": employee_id,
            "created_date": timezone.now(),
        }

        serializer = PatientSerializer(data=patient_data)
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Patient created successfully",
                "patient_id": patient_id,
                "data": serializer.data
            }, status=201)

        return Response({
            "success": False,
            "error": "Patient creation failed",
            "details": serializer.errors
        }, status=400)

    except Exception as e:
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=500)
    
@csrf_exempt
@api_view(['PUT'])
@permission_classes([HasRolePermission])
def update_patient(request, patient_id):
    try:
        patient = Patient.objects.get(patient_id=patient_id)
        
        data = request.data.copy()
        
        employee_id = (
            request.data.get('auth-user-id') or
            request.headers.get('auth-user-id') or
            "system"
        )
        
        patient_data = {
            "patientname": data.get("patientname"),
            "age": data.get("age"),
            "age_type": data.get("age_type", "Years"),
            "gender": data.get("gender"),
            "phone": data.get("phone", ""),
            "email": data.get("email", ""),
            "address": data.get("address") if isinstance(data.get("address"), dict) else {},
            "lastmodified_by": employee_id,
            "lastmodified_date": timezone.now(),
        }

        # Filter out None values to allow partial updates
        patient_data = {k: v for k, v in patient_data.items() if v is not None}

        serializer = PatientSerializer(patient, data=patient_data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Patient updated successfully",
                "patient_id": patient_id,
                "data": serializer.data
            }, status=200)

        return Response({
            "success": False,
            "error": "Patient update failed",
            "details": serializer.errors
        }, status=400)
        
    except Patient.DoesNotExist:
        return Response({"success": False, "error": "Patient not found"}, status=404)
    except Exception as e:
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=500)
    

# MongoDB Connection Setup
def get_mongodb_connection():
    # MongoDB connection (shared, pooled client)
    client = get_client()
    db = client["Diagnostics"]
    return db, GridFS(db)

@csrf_exempt
@api_view(['POST'])
@permission_classes([HasRolePermission])
def create_bill(request):
    """
    Create a bill and upload prescription file to GridFS if provided.
    """
    try:
        data = request.data.copy()
        prescription_file = request.FILES.get('prescription_file')

        # Validate patient
        patient_id = data.get("patient_id")
        appointment_id = data.get("appointment_id")
        
        if not patient_id:
            return Response({"error": "patient_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            patient = Patient.objects.get(patient_id=patient_id)
        except Patient.DoesNotExist:
            return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)

        # employee_id
        employee_id = (
            request.data.get('auth-user-id') or
            request.headers.get('auth-user-id') or
            "system"
        )

        # Handle date
        raw_date = data.get("date")
        billing_date = timezone.now()

        if raw_date:
            try:
                if "T" in raw_date:
                    billing_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    if timezone.is_naive(billing_date):
                        billing_date = timezone.make_aware(billing_date)
                else:
                    dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
                    billing_date = timezone.make_aware(dt)
            except Exception as e:
                logger.error(f"Failed to parse billing date '{raw_date}': {e}")
                billing_date = timezone.now()

        # Convert blank fields
        def s(val, default="0"):
            return str(default) if val in [None, ""] else str(val)

        # History
        patient_history = data.get("patient_history", "")
        patient_history = patient_history.strip() if patient_history.strip() else None

        # Emergency boolean
        emergency = data.get("emergency", False)
        if isinstance(emergency, str):
            emergency = emergency.lower() in ["true", "1", "yes"]

        # Default prescription file id
        # Upload prescription file if exists
        prescription_file_id = None

        if prescription_file:
            try:
                db, fs = get_mongodb_connection()

                file_content = prescription_file.read()

                file_id = fs.put(
                    file_content,
                    filename=prescription_file.name,
                    content_type=prescription_file.content_type,
                    patient_id=patient_id,
                    uploaded_date=datetime.now()
                )

                prescription_file_id = str(file_id)

            except Exception as e:
                return Response({
                    "error": "File upload failed",
                    "details": str(e)
                }, status=500)

        # Create billing record
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
            "testdetails": (
                json.loads(data.get("testdetails")) if isinstance(data.get("testdetails"), str) 
                else data.get("testdetails") if isinstance(data.get("testdetails"), (list, dict)) 
                else []
            ),
            "totalAmount": s(data.get("totalAmount")),
            "discount": s(data.get("discount")),
            "payment_method": (
                json.loads(data.get("payment_method")) if isinstance(data.get("payment_method"), str)
                else data.get("payment_method") if isinstance(data.get("payment_method"), dict)
                else {}
            ),
            "MultiplePayment": (
                json.loads(data.get("MultiplePayment")) if isinstance(data.get("MultiplePayment"), str)
                else data.get("MultiplePayment") if isinstance(data.get("MultiplePayment"), list)
                else []
            ),
            "credit_amount": s(data.get("credit_amount")),
            "status": data.get("status", "Registered"),
            "is_emergency": emergency,
            "patient_history": patient_history,

            # ✅ Store GridFS ID
            "prescription_file_id": prescription_file_id,

            "created_by": employee_id,
            "created_date": timezone.now(),
        }

        serializer = BillingSerializer(data=billing_data)

        if serializer.is_valid():
            billing = serializer.save()
            
            # If this bill originated from an appointment, update its status
            if appointment_id:
                try:
                    appointment = Appointment.objects.get(appointment_id=appointment_id)
                    appointment.status = "Registered"
                    appointment.lastmodified_by = employee_id
                    appointment.lastmodified_date = timezone.now()
                    appointment.save()
                except Appointment.DoesNotExist:
                    pass
            
            return Response({
                "success": True,
                "message": "Bill created successfully",
                "bill_id": str(billing.id),
                "patient_id": patient_id,
                "prescription_file_id": prescription_file_id,
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)

        return Response({
            "success": False,
            "error": "Validation failed",
            "details": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

@api_view(['GET'])
@permission_classes([HasRolePermission])
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
@permission_classes([HasRolePermission])
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
@permission_classes([HasRolePermission])
def update_bill(request):
    """
    Updated bill update function to handle MongoDB collection updates
    """
    try:
        # Extract employee_id from request header/body
        employee_id = (
            request.data.get('auth-user-id') or
            request.headers.get('auth-user-id') or
            "system"
        )
        
        # MongoDB connection
        collection = MONGO_DB.core_billing
        bill_id = request.data.get("bill_id")
        patient_id = request.data.get("patient_id")
        bill_date_str = request.data.get("date")
        
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
            except Exception as e:
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
                except Exception as e:
                    pass
        
        # Find the existing record(s)
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
        
        # Process testdetails
        testdetails = request.data.get("testdetails", [])
        if isinstance(testdetails, list):
            testdetails_json = json.dumps(testdetails)
        elif isinstance(testdetails, str):
            testdetails_json = testdetails
        else:
            testdetails_json = json.dumps([])
        
        # Amount Calculations
        try:
            total_amount = float(request.data.get("totalAmount", "0"))
        except (ValueError, TypeError):
            total_amount = 0.0
            
        discount_raw = str(request.data.get("discount", "0")).strip()
        discount_amount = 0.0
        if discount_raw.endswith('%'):
            try:
                pct = float(discount_raw.strip('%'))
                discount_amount = (total_amount * pct) / 100.0
            except (ValueError, TypeError):
                pass
        else:
            try:
                discount_amount = float(discount_raw)
            except (ValueError, TypeError):
                pass
                
        net_amount = total_amount - discount_amount
        if net_amount < 0:
            net_amount = 0.0
        
        # Handle emergency field
        emergency = request.data.get("emergency", billing_record.get("is_emergency", False))
        if isinstance(emergency, str):
            emergency = emergency.lower() in ['true', '1', 'yes']
        else:
            emergency = bool(emergency)
        
        # Handle patient_history
        patient_history = request.data.get("patient_history", billing_record.get("patient_history"))
        if patient_history == "":
            patient_history = None
        
        # Payment Method Handling
        payment_method_data = request.data.get("payment_method", {})
        multiple_payment_data = request.data.get("MultiplePayment")
        credit_amount = 0
        
        update_data = {
            "bill_no": bill_no,
            "bill_date": bill_date,
            "testdetails": testdetails_json,
            "totalAmount": str(total_amount),
            "netAmount": str(net_amount),
            "discount": str(discount_raw),
            "credit_amount": str(credit_amount),
            "status": "Billed",
            "is_emergency": emergency,
            "patient_history": patient_history,
            "lastmodified_by": employee_id,
            "lastmodified_date": datetime.now(),
        }
        
        if isinstance(payment_method_data, dict):
            payment_method = payment_method_data.get("paymentmethod", "")
            if payment_method == "Multiple Payment":
                if isinstance(multiple_payment_data, str):
                    try:
                        processed_multiple_payments = json.loads(multiple_payment_data)
                    except Exception as e:
                        logger.error(f"Failed to parse MultiplePayment JSON: {e}")
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
        
        # Update MongoDB Document
        result = collection.update_one({"_id": record_id}, {"$set": update_data})
        
        if result.matched_count == 0:
            return Response({"error": "Failed to find billing record for update"}, status=500)
        
        # Prepare Response
        try:
            response_multiple_payment = json.loads(update_data.get("MultiplePayment", "[]"))
        except Exception as e:
            logger.error(f"Failed to parse MultiplePayment for response: {e}")
            response_multiple_payment = []

        try:
            response_payment_method = json.loads(update_data.get("payment_method", "{}"))
        except Exception as e:
            logger.error(f"Failed to parse payment_method for response: {e}")
            response_payment_method = {}

        try:
            response_testdetails = json.loads(update_data.get("testdetails", "[]"))
        except Exception as e:
            logger.error(f"Failed to parse testdetails for response: {e}")
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
                "testdetails": response_testdetails,
                "is_emergency": emergency,
                "patient_history": patient_history
            }
        }, status=200)
    except Exception as e:
        logger.error(f"Error in update_bill: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=500)
        

@api_view(['GET'])
@permission_classes([HasRolePermission])
def patient_get(request):
    """
    Get patient details by patient_id or phone.
    If searching by phone and multiple patients exist, return all of them.
    Handles both Django ORM and MongoDB data formats.
    """
    try:
        patient_id = request.GET.get('patient_id')
        phone = request.GET.get('phone')
        
        if patient_id:
            # Single patient lookup by ID
            patient = Patient.objects.filter(patient_id=patient_id).first()
            if patient:
                serializer = PatientSerializer(patient)
                patient_data = serializer.data
                
                # Parse address if it's a string
                if isinstance(patient_data.get('address'), str):
                    try:
                        patient_data['address'] = json.loads(patient_data['address'])
                    except Exception as e:
                        logger.error(f"Failed to parse patient address JSON: {e}")
                        patient_data['address'] = {"area": "", "pincode": ""}

                return Response({
                    'success': True, 
                    'data': patient_data, 
                    'patient_id': patient.patient_id,
                    'multiple': False
                }, status=200)
        elif phone:
            # Remove any non-numeric characters
            phone_clean = re.sub(r'\D', '', phone)
            
            # Search in both Django ORM (exact match and contains)
            patients = Patient.objects.filter(phone=phone_clean) | Patient.objects.filter(phone__icontains=phone_clean)
            
            # Also search in MongoDB directly for better coverage
            try:
                client = get_client()
                db = client.Diagnostics
                mongo_patients = list(db.core_patient.find({"phone": {"$regex": phone_clean}}))
                
                # Convert MongoDB results to match serializer format
                combined_results = []
                seen_ids = set()
                
                # Add Django ORM results
                if patients.exists():
                    for patient in patients:
                        serializer = PatientSerializer(patient)
                        patient_data = serializer.data
                        
                        # Parse address if it's a string
                        if isinstance(patient_data.get('address'), str):
                            try:
                                patient_data['address'] = json.loads(patient_data['address'])
                            except Exception as e:
                                logger.error(f"Failed to parse patient address JSON: {e}")
                                patient_data['address'] = {"area": "", "pincode": ""}

                        combined_results.append(patient_data)
                        seen_ids.add(patient.patient_id)
                
                # Add MongoDB results not already in Django results
                for mongo_patient in mongo_patients:
                    if mongo_patient.get('patient_id') not in seen_ids:
                        # Parse address if string
                        address = mongo_patient.get('address', {})
                        if isinstance(address, str):
                            try:
                                address = json.loads(address)
                            except Exception as e:
                                logger.error(f"Failed to parse mongo patient address JSON: {e}")
                                address = {"area": "", "pincode": ""}
                        
                        # Format MongoDB data to match serializer output
                        patient_data = {
                            'patient_id': mongo_patient.get('patient_id'),
                            'patientname': mongo_patient.get('patientname'),
                            'age': mongo_patient.get('age'),
                            'age_type': mongo_patient.get('age_type', 'Years'),
                            'gender': mongo_patient.get('gender'),
                            'phone': mongo_patient.get('phone', ''),
                            'email': mongo_patient.get('email', ''),
                            'address': address,
                            'emergency': mongo_patient.get('emergency', False),
                            'patient_history': mongo_patient.get('patient_history', ''),
                            'prescription_file_id': mongo_patient.get('prescription_file_id', ''),
                            'created_by': mongo_patient.get('created_by', 'system'),
                            'created_date': mongo_patient.get('created_date'),
                            'lastmodified_by': mongo_patient.get('lastmodified_by', 'system'),
                            'lastmodified_date': mongo_patient.get('lastmodified_date')
                        }
                        combined_results.append(patient_data)
                        seen_ids.add(mongo_patient.get('patient_id'))
                
                if len(combined_results) > 0:
                    if len(combined_results) > 1:
                        # Multiple patients found
                        return Response({
                            'success': True,
                            'data': combined_results,
                            'multiple': True,
                            'count': len(combined_results)
                        }, status=200)
                    else:
                        # Single patient found
                        return Response({
                            'success': True,
                            'data': combined_results[0],
                            'patient_id': combined_results[0]['patient_id'],
                            'multiple': False
                        }, status=200)
                
            except Exception as mongo_error:
                logger.error(f"MongoDB search error: {mongo_error}")
                # Fall back to Django ORM only
                if patients.exists():
                    if patients.count() > 1:
                        serializer = PatientSerializer(patients, many=True)
                        return Response({
                            'success': True,
                            'data': serializer.data,
                            'multiple': True,
                            'count': patients.count()
                        }, status=200)
                    else:
                        serializer = PatientSerializer(patients.first())
                        return Response({
                            'success': True,
                            'data': serializer.data,
                            'patient_id': patients.first().patient_id,
                            'multiple': False
                        }, status=200)
        else:
            return Response({
                'success': False, 
                'error': 'Please provide patient_id or phone'
            }, status=400)
        
        # If no patient found
        return Response({
            'success': False, 
            'error': 'Patient not found'
        }, status=404)
        
    except Exception as e:
        logger.error(f"Error in patient_get: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return Response({
            'success': False, 
            'error': 'Internal server error', 
            'details': str(e)
        }, status=500)


@api_view(['GET'])
@permission_classes([HasRolePermission])
def get_patients_by_date(request):
    from django.db.models import Q

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    single_date = request.GET.get('date')
    search = request.GET.get('search', '').strip()

    if single_date and not (start_date and end_date):
        start_date = single_date
        end_date = single_date

    if not (start_date and end_date):
        return Response({
            'error': 'start_date and end_date parameters are required, or provide a single date parameter.'
        }, status=400)

    try:
        # Convert to timezone-aware datetime
        start_date_parsed = make_aware(datetime.strptime(start_date, '%Y-%m-%d'))
        end_date_parsed = make_aware(
            datetime.strptime(end_date, '%Y-%m-%d')
            + timedelta(days=1) - timedelta(seconds=1)
        )

        # Fetch Billing records
        patients = Billing.objects.filter(
            date__gte=start_date_parsed,
            date__lte=end_date_parsed
        ).order_by('date')

        if search:
            name_patient_ids = Patient.objects.filter(
                patientname__icontains=search
            ).values_list('patient_id', flat=True)
            patients = patients.filter(
                Q(patient_id__icontains=search) |
                Q(lab_id__icontains=search) |
                Q(patient_id__in=name_patient_ids)
            )

        # ------------------ MongoDB Connection ------------------
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = get_client()
        db = client["Global"]
        profile_col = db.backend_diagnostics_profile

        # Collect unique lastmodified_by values
        lastmodified_users = set(
            patients.values_list('lastmodified_by', flat=True)
        )
        lastmodified_users.discard(None)

        # Convert all to string (important)
        lastmodified_users = [str(user) for user in lastmodified_users]

        # Fetch matching profiles
        profiles = profile_col.find(
            {"employeeId": {"$in": lastmodified_users}},
            {"employeeId": 1, "employeeName": 1}
        )

        # Create mapping dictionary
        profile_map = {
            str(profile["employeeId"]): profile.get("employeeName", "Unknown")
            for profile in profiles
        }
        # ---------------------------------------------------------

        patient_data = []

        page_obj, page_meta = paginate_queryset(patients, request)

        for patient in page_obj:
            try:
                patient_dict = model_to_dict(patient)

                # -------- Patient Model Data --------
                try:
                    patient_info = Patient.objects.get(
                        patient_id=patient.patient_id
                    )
                    patient_dict['patientname'] = patient_info.patientname
                    patient_dict['gender'] = patient_info.gender
                    patient_dict['age'] = patient_info.age
                    patient_dict['phone'] = patient_info.phone
                except Patient.DoesNotExist:
                    patient_dict['patientname'] = 'Unknown'
                    patient_dict['gender'] = 'N/A'
                    patient_dict['age'] = 'N/A'
                    patient_dict['phone'] = 'N/A'

                # -------- Profile Name Mapping --------
                username = str(patient_dict.get("lastmodified_by"))
                patient_dict["lastmodified_name"] = profile_map.get(
                    username, "Unknown"
                )

                # -------- Handle testdetails --------
                tests = getattr(patient, 'testdetails', [])

                if isinstance(tests, str):
                    try:
                        tests = json.loads(tests) if tests and tests != '\"[]\"' else []
                    except json.JSONDecodeError:
                        tests = []

                valid_tests = [
                    test for test in tests
                    if not test.get('refund', False)
                    and not test.get('cancellation', False)
                ]

                patient_dict['testdetails'] = valid_tests
                patient_dict.setdefault('segment', 'N/A')

                patient_data.append(patient_dict)

            except Exception as patient_error:
                logger.error(
                    f"Error processing patient {getattr(patient, 'patient_id', 'unknown')}: {patient_error}"
                )
                continue

        return Response({
            'success': True,
            'data': patient_data,
            **page_meta
        })

    except ValueError:
        return Response({
            'error': 'Invalid date format. Use YYYY-MM-DD.'
        }, status=400)

    except Exception as e:
        logger.error(f"Error in get_patients_by_date: {str(e)}")
        return Response({
            'error': 'An error occurred while fetching patients.'
        }, status=500)
    

@api_view(['GET'])
def patient_overview(request):
    patients = Billing.objects.all()
    page_obj, page_meta = paginate_queryset(patients, request)
    serializer = BillingSerializer(page_obj, many=True)
    # NOTE: response shape changed from a bare JSON array to a paginated
    # object ({"data": [...], total_count, total_pages, current_page}) to
    # bound the payload as billing records accumulate; update any frontend
    # caller that expected a raw array here.
    return Response({"data": serializer.data, **page_meta})


@api_view(['GET'])
@permission_classes([HasRolePermission])
def get_patientsbyb2b(request):
    """Fetch patients registered on a given date with payment mode options based on segment"""
    date_str = request.GET.get('date', None)
    if not date_str:
        return Response({"error": "Date parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
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
                        payment_options['credit'] = False
                        payment_options['partialpayment'] = False
                except ClinicalName.DoesNotExist:
                    pass
            patient_data['payment_options'] = payment_options
            result.append(patient_data)
        return Response(result, status=status.HTTP_200_OK)
    except ValueError:
        return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)
    


from datetime import date, datetime
from urllib.parse import quote_plus
from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
import json
import os



@api_view(['GET'])
@permission_classes([HasRolePermission])
def dashboard_data(request):
    try:
        # -------------------------
        # Request params
        # -------------------------
        from_date = request.GET.get('from_date')
        to_date = request.GET.get('to_date')
        payment_method = request.GET.get('payment_method')

        # Default → today
        if not from_date and not to_date:
            today = date.today()
            from_date = today.strftime('%Y-%m-%d')
            to_date = today.strftime('%Y-%m-%d')

        if from_date:
            from_date = datetime.strptime(from_date, '%Y-%m-%d')

        if to_date:
            to_date = datetime.strptime(to_date, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59
            )

        # -------------------------
        # MongoDB
        # -------------------------
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = get_client()
        db = client["Diagnostics"]
        billing_col = db.core_billing   # ✅ NEW COLLECTION

        # -------------------------
        # Query
        # -------------------------
        query = {}

        if from_date and to_date:
            query['date'] = {'$gte': from_date, '$lte': to_date}

        if payment_method:
            if payment_method == "PartialPayment":
                query['payment_method'] = {'$regex': 'PartialPayment', '$options': 'i'}
            else:
                query['$or'] = [
                    {'payment_method': {'$regex': f'"paymentmethod":"{payment_method}"', '$options': 'i'}},
                    {'PartialPayment': {'$regex': f'"method":"{payment_method}"', '$options': 'i'}}
                ]

        bills = list(billing_col.find(query))

        # -------------------------
        # Helpers
        # -------------------------
        def safe_float(val):
            try:
                return float(val)
            except Exception as e:
                logger.error(f"safe_float conversion failed for {val!r}: {e}")
                return 0.0

        def parse_json(val, default):
            if isinstance(val, dict):
                return val
            if not val:
                return default
            try:
                return json.loads(val)
            except Exception as e:
                logger.error(f"parse_json failed for {val!r}: {e}")
                return default

        # -------------------------
        # Dashboard variables
        # -------------------------
        total_patients = len(bills)
        total_revenue = 0.0

        payment_methods = {
            'Cash': 0,
            'Card': 0,
            'UPI': 0,
            'Credit': 0,
            'PartialPayment': 0
        }

        payment_method_amounts = {
            'Cash': 0.0,
            'Card': 0.0,
            'UPI': 0.0,
            'Credit': 0.0,
            'PartialPayment': 0.0
        }

        segments = {
            'B2B': 0,
            'Walk-in': 0,
            'Home Collection': 0
        }

        b2b_clients = {}

        total_credit = 0.0
        credit_paid = 0.0

        # -------------------------
        # Process bills
        # -------------------------
        for bill in bills:
            amount = safe_float(bill.get('totalAmount'))
            total_revenue += amount

            # Segment
            segment = bill.get('segment')
            if segment in segments:
                segments[segment] += 1

            if segment == 'B2B':
                name = bill.get('B2B')
                if name:
                    b2b_clients[name] = b2b_clients.get(name, 0) + 1

            # Payment
            payment_info = parse_json(bill.get('payment_method'), {})
            method = payment_info.get('paymentmethod')

            if method == 'PartialPayment':
                payment_methods['PartialPayment'] += 1

                partial = parse_json(bill.get('PartialPayment'), {})
                actual_method = partial.get('method')
                credit_amt = safe_float(partial.get('credit'))

                if actual_method in payment_method_amounts:
                    payment_method_amounts[actual_method] += (amount - credit_amt)

                payment_method_amounts['Credit'] += credit_amt
                payment_method_amounts['PartialPayment'] += amount
                total_credit += credit_amt

            elif method in payment_methods:
                payment_methods[method] += 1
                payment_method_amounts[method] += amount

            # Direct credit
            total_credit += safe_float(bill.get('credit_amount'))

            # Credit paid
            credit_details = parse_json(bill.get('credit_details'), [])
            if isinstance(credit_details, list):
                for c in credit_details:
                    credit_paid += safe_float(c.get('amount_paid'))

        credit_pending = total_credit - credit_paid

        # -------------------------
        # Response
        # -------------------------
        response = {
            'total_patients': total_patients,
            'total_revenue': round(total_revenue, 2),
            'payment_methods': payment_methods,
            'payment_method_amounts': {
                k: round(v, 2) for k, v in payment_method_amounts.items()
            },
            'segments': segments,
            'b2b_clients': dict(
                sorted(b2b_clients.items(), key=lambda x: x[1], reverse=True)
            ),
            'credit_statistics': {
                'total_credit': round(total_credit, 2),
                'credit_paid': round(credit_paid, 2),
                'credit_pending': round(credit_pending, 2)
            }
        }

        return JsonResponse({'success': True, 'data': response})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)





@api_view(['PATCH'])
@permission_classes([HasRolePermission])
def update_credit_amount(request):
    try:
        data = request.data
        bill_no = data.get("bill_no")
        
        if not bill_no:
             return Response({"error": "bill_no is required in payload"}, status=400)
        
        # 1. Update Django Billing Model
        billing = Billing.objects.filter(bill_no=bill_no).first()
        if not billing:
            return Response({"error": "Billing record not found"}, status=404)

        new_credit_amount = data.get("credit_amount")
        amount_paid = data.get("amount_paid")
        paid_date = data.get("paid_date")
        payment_method = data.get("payment_method")

        if new_credit_amount is not None:
             Billing.objects.filter(bill_no=bill_no).update(credit_amount=str(new_credit_amount))

        # 2. Update MongoDB core_billing Collection (critical for reports)
        client = get_client()
        db = client.Diagnostics
        collection = db.core_billing
        
        # Create the new credit detail entry
        new_credit_detail = {
            "credit_amount": str(new_credit_amount), # Store current total status
            "amount_paid": amount_paid if amount_paid is not None else 0,
            "paid_date": paid_date,
            "payment_method": payment_method,
            "remaining_amount": str(new_credit_amount)
        }
        
        # Fetch existing document to handle credit_details being a string
        billing_doc = collection.find_one({"bill_no": bill_no})
        
        if billing_doc:
            existing_details = billing_doc.get("credit_details", [])
            
            # Parse if it's a string (which causes the $push error)
            if isinstance(existing_details, str):
                try:
                    credit_details_list = json.loads(existing_details)
                except json.JSONDecodeError:
                    credit_details_list = []
            elif isinstance(existing_details, list):
                credit_details_list = existing_details
            else:
                credit_details_list = []
                
            # Append new detail
            credit_details_list.append(new_credit_detail)
            
            # Update back as string (to maintain consistency if that's the pattern) or list
            # The error showed it was a string, so we'll save it back as a string to be safe,
            # or we could save as list directly. Given report.py parses it, string is safe.
            # However, saving as a list is generally better for MongoDB. 
            # But let's stick to the observed pattern to avoid breaking other unknown readers.
            updated_details_str = json.dumps(credit_details_list)
            
            update_result = collection.update_one(
                {"bill_no": bill_no},
                {
                    "$set": {
                        "credit_amount": str(new_credit_amount),
                        "credit_details": updated_details_str
                    }
                }
            )
        else:
             return Response({"error": "Billing record not found in MongoDB"}, status=404)

        return Response({"success": True, "message": "Credit amount updated successfully"}, status=200)

    except Exception as e:
        logger.error(f"Error updating credit amount: {str(e)}")
        return Response({"error": str(e)}, status=500)
import ast

def safe_parse_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            try:
                # Fallback for Python stringified lists (single quotes)
                parsed = ast.literal_eval(data)
                if isinstance(parsed, list):
                    return parsed
            except Exception as e:
                logger.error(f"safe_parse_list ast.literal_eval failed: {e}")
                pass
    return []

from django.db.models import Q
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

@api_view(["GET"])
@permission_classes([HasRolePermission])
def get_patient_list(request):
    try:
        page = int(request.GET.get('page', 1))
        limit = int(request.GET.get('limit', 10))
        search_query = request.GET.get('search', '').strip()

        patients = Patient.objects.all().order_by('-created_date', '-patient_id')

        if search_query:
            patients = patients.filter(
                Q(patient_id__icontains=search_query) |
                Q(patientname__icontains=search_query) |
                Q(phone__icontains=search_query)
            )

        paginator = Paginator(patients, limit)
        
        try:
            paginated_patients = paginator.page(page)
        except PageNotAnInteger:
            paginated_patients = paginator.page(1)
        except EmptyPage:
            paginated_patients = paginator.page(paginator.num_pages)

        serializer = PatientSerializer(paginated_patients, many=True)

        return Response({
            "success": True,
            "patients": serializer.data,
            "total_pages": paginator.num_pages,
            "current_page": paginated_patients.number,
            "total_count": paginator.count
        }, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error in get_patient_list: {str(e)}")
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["GET"])
@permission_classes([HasRolePermission])
def get_patient_full_record(request, patient_id):
    try:
        # Fetch Patient Data
        try:
            patient = Patient.objects.get(patient_id=patient_id)
            patient_data = PatientSerializer(patient).data
        except Patient.DoesNotExist:
            return Response({"success": False, "error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)

        # Fetch Bills using Django ORM
        bills_queryset = Billing.objects.filter(patient_id=patient_id).order_by('-date')
        bills = BillingSerializer(bills_queryset, many=True).data

        return Response({
            "success": True,
            "patient": patient_data,
            "bills": bills
        }, status=status.HTTP_200_OK)
    
    except Exception as e:
        logger.error(f"Error in get_patient_full_record: {str(e)}")
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
def get_prescription_image(request, file_id):
    try:
        if not ObjectId.is_valid(file_id):
            return HttpResponse(status=400, content="Invalid file ID")
        
        try:
            grid_out = FS.get(ObjectId(file_id))
        except gridfs.errors.NoFile:
            return HttpResponse(status=404, content="File not found")

        # Usually grid_out.content_type holds the mime type if it was set during upload
        content_type = getattr(grid_out, 'content_type', 'image/jpeg')
        if not content_type:
            content_type = 'image/jpeg'
            
        response = HttpResponse(grid_out.read(), content_type=content_type)
        response['Content-Disposition'] = f'inline; filename="{getattr(grid_out, "filename", file_id)}"'
        return response

    except Exception as e:
        logger.error(f"Error in get_prescription_image: {str(e)}")
        return HttpResponse(status=500, content="Internal server error")

@api_view(["GET"])
@permission_classes([HasRolePermission])
def patient_record_dashboard(request):
    try:
        from core.models import Patient, Billing
        from django.db.models import Q
        from datetime import datetime, timedelta
        
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        sample_collector = request.GET.get('sample_collector')
        sales_mapping = request.GET.get('salesMapping')
        search_query = request.GET.get('search', '').strip()
        export = request.GET.get('export') == 'true'
        segment = request.GET.get('segment')
        
        page = int(request.GET.get('page', 1))
        limit = int(request.GET.get('limit', 10))
        
        # Start with all bills
        bills = Billing.objects.all()
        
        if start_date and end_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                bills = bills.filter(date__gte=start_dt, date__lt=end_dt)
            except ValueError:
                pass

        if sample_collector:
            bills = bills.filter(sample_collector__icontains=sample_collector)
            
        if sales_mapping:
            bills = bills.filter(salesMapping__icontains=sales_mapping)
            
        if segment:
            bills = bills.filter(segment__iexact=segment)
            
        # Search patient details
        if search_query:
            patients = Patient.objects.filter(
                Q(patient_id__icontains=search_query) |
                Q(patientname__icontains=search_query) |
                Q(phone__icontains=search_query)
            ).values_list('patient_id', flat=True)
            bills = bills.filter(patient_id__in=patients)
            
        # Sort by latest
        bills = bills.order_by('-date')
        
        # Calculate total revenue for filtered bills
        total_revenue = 0
        all_bills = list(bills)
        for bill in all_bills:
            try:
                amt = float(bill.totalAmount) if bill.totalAmount else 0
                total_revenue += amt
            except Exception as e:
                logger.error(f"Failed to parse bill totalAmount for revenue calculation: {e}")
                pass
                
        # Handle export
        if export:
            export_data = []
            patient_ids = [b.patient_id for b in all_bills]
            patients_qs = Patient.objects.filter(patient_id__in=patient_ids)
            patient_map = {p.patient_id: p for p in patients_qs}
            
            for bill in all_bills:
                patient = patient_map.get(bill.patient_id)
                patient_name = patient.patientname if patient else "N/A"
                phone = patient.phone if patient else "N/A"
                
                export_data.append({
                    "Patient ID": bill.patient_id,
                    "Name": patient_name,
                    "Phone": phone,
                    "Bill No": bill.bill_no,
                    "Date": bill.date.strftime('%Y-%m-%d %H:%M') if bill.date else "",
                    "Sample Collector": get_employee_name(bill.sample_collector) if bill.sample_collector else bill.sample_collector,
                    "Sales Mapping": bill.salesMapping,
                    "B2B": bill.B2B,
                    "Amount": bill.totalAmount,
                    "Status": bill.status
                })
            return Response({"success": True, "data": export_data})
            
        # Pagination
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        paginator = Paginator(all_bills, limit)
        try:
            paginated_bills = paginator.page(page)
        except PageNotAnInteger:
            paginated_bills = paginator.page(1)
        except EmptyPage:
            paginated_bills = paginator.page(paginator.num_pages)
            
        # Prepare response data
        patient_ids = [b.patient_id for b in paginated_bills]
        patients_qs = Patient.objects.filter(patient_id__in=patient_ids)
        patient_map = {p.patient_id: p for p in patients_qs}
        
        from core.serializers import BillingSerializer
        
        result = []
        for bill in paginated_bills:
            patient = patient_map.get(bill.patient_id)
            bill_data = BillingSerializer(bill).data
            if patient:
                bill_data['patient_details'] = {
                    'patientname': patient.patientname,
                    'age': patient.age,
                    'age_type': patient.age_type,
                    'gender': patient.gender,
                    'phone': patient.phone
                }
            else:
                bill_data['patient_details'] = {}
            if bill_data.get('sample_collector'):
                bill_data['sample_collector'] = get_employee_name(bill_data['sample_collector'])
            
            result.append(bill_data)
            
        return Response({
            "success": True,
            "data": result,
            "total_revenue": total_revenue,
            "total_records": paginator.count,
            "total_pages": paginator.num_pages,
            "current_page": paginated_bills.number
        })

    except Exception as e:
        logger.error(f"Error in patient_record_dashboard: {str(e)}")
        return Response({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
