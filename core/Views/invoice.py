from rest_framework.response import Response
from django.http import JsonResponse
import json
from urllib.parse import quote_plus
from pymongo import MongoClient
import certifi
from ..models import Patient, ClinicalName, Billing
from ..serializers import BillingSerializer, ClinicalNameSerializer
from django.http import HttpResponse
from datetime import datetime, timedelta
from collections import defaultdict
from rest_framework import status
from datetime import datetime
from django.views.decorators.csrf import csrf_exempt
import pytz
import os

#auth

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv
import time
from datetime import datetime, timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt

load_dotenv()
def get_mongo_collection():
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client["Diagnostics"]
    return db["core_invoice"]



from datetime import timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt

from django.db.models.functions import Lower, Trim, Upper

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_all_patients(request):
    segment = request.GET.get('segment', 'B2B')
    clinical_name = request.GET.get('clinical_name', '')
    from_date = request.GET.get('from_date', '')
    to_date = request.GET.get('to_date', '')
    min_credit = request.GET.get('min_credit', '0')

    # Get billing records as before
    patients_qs = Billing.objects.filter(segment=segment)

    if clinical_name:
        patients_qs = patients_qs.filter(B2B=clinical_name)

    if from_date:
        from_date_parsed = parse_date(from_date)
        if from_date_parsed:
            patients_qs = patients_qs.filter(date__gte=from_date_parsed)

    if to_date:
        to_date_parsed = parse_date(to_date)
        if to_date_parsed:
            next_day = to_date_parsed + timedelta(days=1)
            patients_qs = patients_qs.filter(date__lt=next_day)

    # Exclude already invoiced patients
    collection = get_mongo_collection()
    invoices = list(collection.find({}, {"patients": 1}))

    invoiced_patient_ids = set()
    for invoice in invoices:
        if 'patients' in invoice and invoice['patients']:
            for patient in invoice['patients']:
                if 'patient_id' in patient:
                    invoiced_patient_ids.add(patient['patient_id'])

    if invoiced_patient_ids:
        patients_qs = patients_qs.exclude(patient_id__in=list(invoiced_patient_ids))

    patients_qs = patients_qs.order_by('-date')

    # Convert to list & apply credit filter
    patients = list(patients_qs)
    if min_credit:
        try:
            min_credit_value = float(min_credit)
            patients = [
                p for p in patients
                if p.credit_amount and float(p.credit_amount or 0) >= min_credit_value
            ]
        except ValueError:
            pass

    # ENHANCED DEBUGGING: Print actual patient IDs
    patient_ids = [p.patient_id for p in patients]
    # print(f"Billing patient IDs found: {patient_ids}")
    
    # Print each ID with quotes to see whitespace
    for i, pid in enumerate(patient_ids):
        print(f"Billing ID {i}: '{pid}' (length: {len(pid) if pid else 0})")

    # IMPROVED PATIENT MATCHING with normalization
    # Clean patient IDs by stripping whitespace and converting to uppercase
    cleaned_patient_ids = []
    for pid in patient_ids:
        if pid:  # Check if not None or empty
            cleaned_id = str(pid).strip().upper()  # Strip whitespace and uppercase
            cleaned_patient_ids.append(cleaned_id)
    
    # print(f"Cleaned billing patient IDs: {cleaned_patient_ids}")

    # Fetch patients with case-insensitive and whitespace-tolerant matching
    # Method 1: Using Django database functions (recommended)
    try:
        patients_with_names = Patient.objects.annotate(
            normalized_id=Upper(Trim('patient_id'))
        ).filter(
            normalized_id__in=cleaned_patient_ids
        )
        
        # Create lookup dictionary with normalized keys
        patients_dict = {
            patient.patient_id.strip().upper(): patient.patientname 
            for patient in patients_with_names
        }
        
    except Exception as e:
        print(f"Database function approach failed: {e}")
        # Fallback method: Manual filtering
        all_patients = Patient.objects.all()
        patients_dict = {}
        
        for patient in all_patients:
            if patient.patient_id:
                normalized_patient_id = str(patient.patient_id).strip().upper()
                if normalized_patient_id in cleaned_patient_ids:
                    patients_dict[normalized_patient_id] = patient.patientname

    # print(f"Patient names found: {len(patients_dict)}")
    # print(f"Matched patient data: {patients_dict}")

    # Debug: Show which IDs didn't match
    matched_ids = set(patients_dict.keys())
    unmatched_ids = set(cleaned_patient_ids) - matched_ids
    if unmatched_ids:
        print(f"Unmatched patient IDs: {unmatched_ids}")
        
        # Additional debugging: Check if these IDs exist in Patient table at all
        for unmatched_id in unmatched_ids:
            similar_patients = Patient.objects.filter(
                patient_id__icontains=unmatched_id.lower()
            )[:5]  # Limit to 5 results
            if similar_patients:
                print(f"Similar patient IDs for '{unmatched_id}': {[p.patient_id for p in similar_patients]}")

    # Serialize and add patient names
    serializer = BillingSerializer(patients, many=True)
    response_data = serializer.data
    
    # Add patientname to each billing record with improved matching
    for i, billing_data in enumerate(response_data):
        patient_id = billing_data.get('patient_id')
        if patient_id:
            # Normalize the billing patient_id for lookup
            normalized_billing_id = str(patient_id).strip().upper()
            billing_data['patientname'] = patients_dict.get(normalized_billing_id, 'Unknown Patient')
        else:
            billing_data['patientname'] = 'No Patient ID'

    return Response(response_data, status=status.HTTP_200_OK)






@api_view(['GET'])
@csrf_exempt
@permission_classes([ HasRoleAndDataPermission])
def get_clinicalname_invoice(request):
    if request.method == 'GET':
        # Filter clinical names with b2bType "Carry Credit"
        clinicalname = ClinicalName.objects.filter(b2bType="Credit")
        serializer = ClinicalNameSerializer(clinicalname, many=True)
        return Response(serializer.data)

# Function to get MongoDB collection




from django.db.models.functions import Upper, Trim

@api_view(["POST"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def generate_invoice(request):
    collection = get_mongo_collection()
    try:
        # === Parse request payload directly from DRF ===
        raw_data = request.data  

        # === Extract employee id ===
        employee_id = (
            raw_data.get("auth-user-id")
            or request.headers.get("auth-user-id")
            or "system"
        )

        # === Validate patient_ids ===
        patient_ids = [patient.get("patient_id") for patient in raw_data.get("patients", [])]
        filtered_patients = []
        
        # === FETCH PATIENT NAMES - Using same approach as get_all_patients ===
        # print(f"Invoice patient IDs found: {patient_ids}")
        
        # Clean patient IDs by stripping whitespace and converting to uppercase
        cleaned_patient_ids = []
        for pid in patient_ids:
            if pid:  # Check if not None or empty
                cleaned_id = str(pid).strip().upper()  # Strip whitespace and uppercase
                cleaned_patient_ids.append(cleaned_id)
        
        # print(f"Cleaned invoice patient IDs: {cleaned_patient_ids}")

        # Fetch patients with case-insensitive and whitespace-tolerant matching
        try:
            patients_with_names = Patient.objects.annotate(
                normalized_id=Upper(Trim('patient_id'))
            ).filter(
                normalized_id__in=cleaned_patient_ids
            )
            
            # Create lookup dictionary with normalized keys
            patients_dict = {
                patient.patient_id.strip().upper(): patient.patientname 
                for patient in patients_with_names
            }
            
        except Exception as e:
            print(f"Database function approach failed: {e}")
            # Fallback method: Manual filtering
            all_patients = Patient.objects.all()
            patients_dict = {}
            
            for patient in all_patients:
                if patient.patient_id:
                    normalized_patient_id = str(patient.patient_id).strip().upper()
                    if normalized_patient_id in cleaned_patient_ids:
                        patients_dict[normalized_patient_id] = patient.patientname

        # print(f"Patient names found for invoice: {len(patients_dict)}")
        # print(f"Matched patient data: {patients_dict}")

        # === Build filtered_patients with patient names ===
        for patient in raw_data.get("patients", []):
            patient_id = patient.get("patient_id")
            
            # Normalize patient_id for lookup
            normalized_patient_id = str(patient_id).strip().upper() if patient_id else ""
            patientname = patients_dict.get(normalized_patient_id, "Unknown Patient")
            
            filtered_patients.append({
                "patient_id": patient.get("patient_id"),
                "patientname": patientname,  # ADD PATIENT NAME HERE
                "testdetails": patient.get("testdetails", []),
                "payment_method": patient.get("payment_method"),
                "credit_amount": patient.get("credit_amount"),
                "proportionalCredit": patient.get("proportionalCredit"),
                "proportion": patient.get("proportion"),
                "bill_date": patient.get("bill_date"),
            })

        # === Validate ClinicalName has Carry Credit type ===
        clinical_name = raw_data.get("clinicalName")
        if clinical_name:
            carry_credit_clinical = ClinicalName.objects.filter(
                clinicalname=clinical_name,
                b2bType="Credit"
            ).first()

            if not carry_credit_clinical:
                return JsonResponse(
                    {"error": "Selected clinical name does not have 'Carry Credit' type"},
                    status=400,
                )

        # === Generate invoice number ===
        today = datetime.now().strftime("%Y-%m-%d")
        month_key = datetime.now().strftime("%Y-%m")  # YYYY-MM

        count_for_month = collection.count_documents({
            "invoiceMonth": month_key
        }) + 1

        invoice_number = f"INV{month_key}{count_for_month:04d}"

        # === Build invoice data (only keep necessary fields) ===
        data = {
            "clinicalName": raw_data.get("clinicalName"),
            "generateDate": raw_data.get("generateDate"),
            "fromDate": raw_data.get("fromDate"),
            "toDate": raw_data.get("toDate"),
            "totalCreditAmount": raw_data.get("totalCreditAmount"),
            "paidAmount": raw_data.get("paidAmount"),
            "pendingAmount": raw_data.get("pendingAmount"),
            "patients": filtered_patients,  # NOW INCLUDES PATIENT NAMES
            "paymentDetails": raw_data.get("paymentDetails", {}),
            "proportionalCredits": raw_data.get("proportionalCredits", []),
            "paymentHistory": raw_data.get("paymentHistory", []),

            # Auto-generated metadata
            "invoiceNumber": invoice_number,
            "invoiceDate": today,
            "invoiceMonth": month_key,
            "status": "Generated",
            "isRegeneratable": True,
            "createdBy": employee_id,
            "createdAt": datetime.now().isoformat(),
            "lastModifiedBy": employee_id,
            "lastModifiedAt": datetime.now().isoformat(),
        }

        # === Insert invoice into MongoDB ===
        result = collection.insert_one(data)

        return JsonResponse(
            {
                "message": "Credit Invoice stored successfully",
                "id": str(result.inserted_id),
                "invoiceNumber": invoice_number,
                "createdBy": employee_id,
                "createdAt": data["createdAt"],
                "status": "success",
                "patientsProcessed": len(filtered_patients),
                "patientsWithNames": len([p for p in filtered_patients if p["patientname"] != "Unknown Patient"]),
            },
            status=201,
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)




@api_view(["GET"])
@permission_classes([ HasRoleAndDataPermission])
def get_invoices(request):
    collection = get_mongo_collection()
    # Sort by generation date descending to show latest invoices first
    invoices = list(collection.find({}, {"_id": 0}).sort("generatedAt", -1))
    return JsonResponse(invoices, safe=False)

@api_view(['PUT'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def update_invoice(request, invoice_number):
    """Update the invoice with total, paid, and pending amounts, and track payment history."""
    collection = get_mongo_collection()

    if request.method == "PUT":
        try:
            # ✅ Use DRF's parsed data
            data = request.data  
            employee_id = (data.get("auth-user-id")
            or request.headers.get("auth-user-id")
            or "system"
        )
            new_credit_amount = data.get("totalCreditAmount")
            paid_amount = data.get("paidAmount", "0.00")
            pending_amount = data.get("pendingAmount", "0.00")
            new_payment_details = data.get("paymentDetails", "{}")
            new_payment_history = data.get("paymentHistory", "[]")
            proportional_credits = data.get("proportionalCredits", "[]")

            if new_credit_amount is None:
                return JsonResponse({"error": "Missing totalCreditAmount field"}, status=400)

            # Fetch existing invoice
            existing_invoice = collection.find_one({"invoiceNumber": invoice_number})
            existing_payment_details = existing_invoice.get("paymentDetails", [])

            # Normalize to list
            if isinstance(existing_payment_details, dict):
                existing_payment_details = [existing_payment_details]
            elif isinstance(existing_payment_details, str):
                try:
                    parsed = json.loads(existing_payment_details)
                    if isinstance(parsed, dict):
                        existing_payment_details = [parsed]
                    elif isinstance(parsed, list):
                        existing_payment_details = parsed
                except Exception:
                    existing_payment_details = []

            # Add new entry
            new_payment_detail_entry = (
                json.loads(new_payment_details)
                if isinstance(new_payment_details, str)
                else new_payment_details
            )
            updated_payment_details = existing_payment_details + [new_payment_detail_entry]

            update_data = {
                "totalCreditAmount": new_credit_amount,
                "paidAmount": paid_amount,
                "pendingAmount": pending_amount,
                "paymentDetails": updated_payment_details,
                "paymentHistory": (
                    json.loads(new_payment_history)
                    if isinstance(new_payment_history, str)
                    else new_payment_history
                ),
                "proportionalCredits": (
                    json.loads(proportional_credits)
                    if isinstance(proportional_credits, str)
                    else proportional_credits
                ),
                "lastModifiedBy": employee_id,
                "lastModifiedAt": datetime.now().isoformat(),
                "status": "Updated" if float(paid_amount) > 0 else "Generated",
            }

            result = collection.update_one(
                {"invoiceNumber": invoice_number}, {"$set": update_data}
            )

            if result.matched_count == 0:
                return JsonResponse({"error": "Invoice not found"}, status=404)

            # update patient credits
            try:
                proportional_data = (
                    json.loads(proportional_credits)
                    if isinstance(proportional_credits, str)
                    else proportional_credits
                )

                for patient_credit in proportional_data:
                    patient_id = patient_credit.get("patient_id")
                    new_credit = patient_credit.get("proportionalCredit", "0.00")

                    Billing.objects.filter(patient_id=patient_id).update(
                        credit_amount=new_credit
                    )

            except Exception as e:
                print(f"Error updating patient credits: {e}")

            return JsonResponse(
                {
                    "message": "Invoice and patient credits updated successfully",
                    "pendingAmount": pending_amount,
                    "paidAmount": paid_amount,
                    "paymentDetails": updated_payment_details,
                    "paymentHistory": update_data["paymentHistory"],
                    "proportionalCredits": update_data["proportionalCredits"],
                    "status": update_data["status"],
                },
                status=200,
            )

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


        

@api_view(['DELETE'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def delete_invoice(request, invoice_id):
    """Delete an invoice based on invoice_id"""
    collection = get_mongo_collection()

    if request.method == "DELETE":
        try:
            result = collection.delete_one({"invoiceNumber": invoice_id})

            if result.deleted_count == 0:
                return JsonResponse({"error": "Invoice not found"}, status=404)

            return JsonResponse({
                "message": "Invoice deleted successfully",
                "invoiceNumber": invoice_id
            }, status=200)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Invalid request method"}, status=400)

# New endpoint for regenerating invoices
@api_view(['POST'])
@csrf_exempt
@permission_classes([ HasRoleAndDataPermission])
def regenerate_invoice(request, invoice_number):
    """Create a new invoice based on existing invoice data"""
    collection = get_mongo_collection()
    
    if request.method == "POST":
        try:
            # Find the existing invoice
            existing_invoice = collection.find_one({"invoiceNumber": invoice_number})
            
            if not existing_invoice:
                return JsonResponse({"error": "Original invoice not found"}, status=404)
            
            # Create new invoice data based on existing one
            new_invoice_data = {
                "clinicalName": existing_invoice.get("clinicalName"),
                "generateDate": datetime.now().strftime("%Y-%m-%d"),
                "fromDate": existing_invoice.get("fromDate"),
                "toDate": existing_invoice.get("toDate"),
                "invoiceNumber": "INV-" + str(int(time.time()))[-6:],  # Generate new invoice number
                "totalCreditAmount": existing_invoice.get("totalCreditAmount"),
                "paidAmount": "0.00",
                "pendingAmount": existing_invoice.get("totalCreditAmount"),
                "patients": existing_invoice.get("patients", []),
                "paymentDetails": {},
                "proportionalCredits": [],
                "paymentHistory": [],
                "generatedAt": datetime.now().isoformat(),
                "status": "Regenerated",
                "originalInvoice": invoice_number
            }
            
            # Insert the new invoice
            result = collection.insert_one(new_invoice_data)
            
            return JsonResponse({
                "message": "Invoice regenerated successfully",
                "newInvoiceNumber": new_invoice_data["invoiceNumber"],
                "originalInvoice": invoice_number,
                "id": str(result.inserted_id)
            }, status=201)
            
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    
    return JsonResponse({"error": "Invalid request method"}, status=400)

@api_view(['GET'])
@csrf_exempt
@permission_classes([ HasRoleAndDataPermission])
def get_invoice_patients(request, invoice_number):
    """Get patients for a specific invoice"""
    collection = get_mongo_collection()
    
    try:
        invoice = collection.find_one({"invoiceNumber": invoice_number})
        
        if not invoice:
            return JsonResponse({"error": "Invoice not found"}, status=404)
        
        patients = invoice.get("patients", [])
        return JsonResponse({"patients": patients}, status=200)
        
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)




from bson import ObjectId
from datetime import datetime, timedelta
import json
from collections import defaultdict
from django.http import JsonResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response
from pymongo import MongoClient
import os

def convert_to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


from collections import defaultdict
from datetime import datetime, timedelta
from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
import os, json
from pymongo import MongoClient

def convert_to_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def patient_report(request):
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if not start_date_str or not end_date_str:
        return JsonResponse({"error": "Start date and end date are required"}, status=400)

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

    # MongoDB Connection
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    patients_collection = db["core_billing"]
    invoice_collection = db["core_invoice"]

    report_by_date = defaultdict(lambda: {
        'gross_amount': 0,
        'discount': 0,
        'due_amount': 0,
        'net_amount': 0,
        'pending_amount': 0,
        'total_collection': 0,
        'credit_payment_received': 0,
        'refund_amount': 0,
        'payment_totals': {
            'Cash': 0, 'UPI': 0, 'Neft': 0,
            'Cheque': 0, 'Credit': 0, 'PartialPayment': 0,
            'Credit Card': 0
        }
    })

    # ✅ Patients query within range
    patients = patients_collection.find({
        "date": {"$gte": start_date, "$lt": end_date}
    })

    for patient in patients:
        patient_date = patient.get('date')
        if isinstance(patient_date, dict) and "$date" in patient_date:
            patient_date = datetime.fromisoformat(patient_date["$date"].replace("Z", "+00:00"))
        elif isinstance(patient_date, str):
            try:
                patient_date = datetime.fromisoformat(patient_date.replace("Z", "+00:00"))
            except:
                continue

        if not isinstance(patient_date, datetime):
            continue

        date_key = patient_date.strftime("%Y-%m-%d")
        gross_amount = convert_to_float(patient.get('totalAmount', 0))
        discount = convert_to_float(patient.get('discount', 0))
        due_amount = convert_to_float(patient.get('credit_amount', 0))

        report_by_date[date_key]['gross_amount'] += gross_amount
        report_by_date[date_key]['discount'] += discount
        report_by_date[date_key]['due_amount'] += due_amount

        # ✅ Handle payment methods
        payment_method = patient.get('payment_method', '')
        payment_method_dict = {}
        if isinstance(payment_method, str) and payment_method.strip():
            try:
                payment_method_dict = json.loads(payment_method)
            except:
                payment_method_dict = {}
        elif isinstance(payment_method, dict):
            payment_method_dict = payment_method

        if payment_method_dict.get("paymentmethod") == "Multiple Payment":
            multiple = patient.get("MultiplePayment", "[]")
            try:
                multiple = json.loads(multiple) if isinstance(multiple, str) else multiple
            except:
                multiple = []

            for m in multiple:
                method = m.get("paymentMethod")
                amt = convert_to_float(m.get("amount", 0))
                if method in report_by_date[date_key]['payment_totals']:
                    report_by_date[date_key]['payment_totals'][method] += amt
                else:
                    report_by_date[date_key]['payment_totals'][method] = amt

        else:
            method = payment_method_dict.get("paymentmethod")
            if method in report_by_date[date_key]['payment_totals']:
                report_by_date[date_key]['payment_totals'][method] += gross_amount
            else:
                if method:
                    report_by_date[date_key]['payment_totals'][method] = gross_amount

        # ✅ Handle Refunds
        test_list = []
        testdetails = patient.get('testdetails', [])
        if isinstance(testdetails, str) and testdetails.strip():
            try:
                test_list = json.loads(testdetails)
            except:
                test_list = []
        elif isinstance(testdetails, list):
            test_list = testdetails

        for test in test_list:
            if isinstance(test, dict) and test.get('refund') is True:
                refunded_date_str = test.get('refunded_date')
                if refunded_date_str:
                    try:
                        if 'T' in refunded_date_str:
                            refund_date = datetime.fromisoformat(refunded_date_str.replace("Z", "+00:00")).date()
                        else:
                            refund_date = datetime.strptime(refunded_date_str, "%Y-%m-%d").date()

                        if start_date.date() <= refund_date < end_date.date():
                            refund_date_key = refund_date.strftime("%Y-%m-%d")
                            test_amount = convert_to_float(test.get('amount', 0))
                            report_by_date[refund_date_key]['refund_amount'] += test_amount
                    except:
                        continue

    # ✅ Process Invoices (Credit Payments)
    invoices = invoice_collection.find({
        "paymentDetails.paymentDate": {
            "$gte": start_date.strftime("%Y-%m-%d"),
            "$lt": end_date.strftime("%Y-%m-%d")
        }
    })

    for invoice in invoices:
        payment_details = invoice.get('paymentDetails', [])
        if isinstance(payment_details, str):
            try:
                payment_details = json.loads(payment_details)
            except:
                payment_details = []

        for payment in payment_details:
            payment_date_str = payment.get('paymentDate')
            if not payment_date_str:
                continue
            try:
                payment_date = datetime.strptime(payment_date_str, "%Y-%m-%d").date()
                if start_date.date() <= payment_date < end_date.date():
                    payment_date_key = payment_date.strftime("%Y-%m-%d")
                    amount_paid = convert_to_float(payment.get('paymentAmount', 0))
                    method = payment.get('paymentMethod', '')

                    report_by_date[payment_date_key]['credit_payment_received'] += amount_paid
                    if method in report_by_date[payment_date_key]['payment_totals']:
                        report_by_date[payment_date_key]['payment_totals'][method] += amount_paid
                    else:
                        report_by_date[payment_date_key]['payment_totals'][method] = amount_paid
            except:
                continue

    # 📊 Final Report
    report_list = []
    for date, data in sorted(report_by_date.items()):
        net_amount = data['gross_amount'] - (data['discount'] + data['due_amount'])
        total_collection = net_amount + data['credit_payment_received'] - data['refund_amount']
        report_list.append({
            'date': date,
            'gross_amount': round(data['gross_amount'], 2),
            'discount': round(data['discount'], 2),
            'due_amount': round(data['due_amount'], 2),
            'credit_payment_received': round(data['credit_payment_received'], 2),
            'refund_amount': round(data['refund_amount'], 2),
            'net_amount': round(net_amount, 2),
            'total_collection': round(total_collection, 2),
            'payment_totals': {k: round(v, 2) for k, v in data['payment_totals'].items()},
        })

    client.close()
    return Response({'report': report_list})

