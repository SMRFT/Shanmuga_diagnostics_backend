from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from core.mongo_client import get_client
from datetime import datetime, timedelta
from bson.errors import InvalidId
from core.utils import get_employee_name
import json
import os
import traceback
import logging

from ..models import Billing

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission, HasRolePermission
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def preetham_hospital_report(request):
    """
    Preetham Hospital Report API
    - Barcode fetched ONLY from top-level barcode field
    - Unified search across patient_id, patient_name, barcode
    """

    try:
        client = get_client()
        db = client.Diagnostics

        billing_col = db.core_billing
        barcode_col = db.core_barcodetestdetails
        patient_col = db.core_patient
        sample_col = db.core_samplestatus

        # -------------------------
        # Query params
        # -------------------------
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        search = request.GET.get("search", "").strip()

        if not from_date or not to_date:
            return JsonResponse(
                {"error": "from_date and to_date are required"},
                status=400
            )

        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)

        # -------------------------
        # Billing records
        # -------------------------
        billing_records = list(
            billing_col.find({
                "date": {"$gte": from_dt, "$lt": to_dt},
                "B2B": "PREETHAM HOSPITAL"
            })
        )

        if not billing_records:
            return JsonResponse([], safe=False)

        bill_nos = [b["bill_no"] for b in billing_records if b.get("bill_no")]
        patient_ids = list(
            set(b["patient_id"] for b in billing_records if b.get("patient_id"))
        )

        # -------------------------
        # Patient data
        # -------------------------
        patients = list(
            patient_col.find(
                {"patient_id": {"$in": patient_ids}},
                {"_id": 0}
            )
        )
        patient_map = {p["patient_id"]: p for p in patients}

        # -------------------------
        # Barcode + testdetails (KEY FIX)
        # -------------------------
        barcode_docs = list(
            barcode_col.find(
                {"bill_no": {"$in": bill_nos}},
                {"_id": 0}
            )
        )

        barcode_map = {}
        test_map = {}

        for doc in barcode_docs:
            bill_no = doc.get("bill_no")

            # ✅ BARCODE ONLY FROM TOP LEVEL
            barcode_map[bill_no] = doc.get("barcode", "N/A")

            # Parse testdetails
            raw_testdetails = doc.get("testdetails", "[]")
            try:
                testdetails = json.loads(raw_testdetails)
            except Exception as e:
                logger.error(f"Failed to parse testdetails for bill_no {bill_no}: {e}")
                testdetails = []

            # Deduplicate tests (parameter-based tests appear once)
            unique_tests = {}
            for t in testdetails:
                key = (
                    t.get("testname"),
                    t.get("collection_container")
                )
                unique_tests[key] = {
                    "testname": t.get("testname"),
                    "container": t.get("collection_container"),
                    "amount": t.get("amount", 0)
                }

            test_map[bill_no] = list(unique_tests.values())

        # -------------------------
        # Status determination (TestValues & SampleStatus)
        # -------------------------
        
        # 1. Check core_testvalue (Tests with results)
        test_value_col = db.core_testvalue
        test_value_docs = list(test_value_col.find(
             {"barcode": {"$in": list(barcode_map.values())}},
             {"barcode": 1, "testdetails": 1, "_id": 0}
        ))
        
        # Determine if Approved or just Tested
        test_status_map = {} # barcode -> "Approved" or "Tested"
        
        for doc in test_value_docs:
            bc = doc.get("barcode")
            if not bc: continue
            
            raw_details = doc.get("testdetails", "[]")
            is_approved = False
            
            try:
                if isinstance(raw_details, str):
                    details = json.loads(raw_details)
                else:
                    details = raw_details
                    
                if isinstance(details, list):
                    # Check if ANY test is approved or has verified_by/approve_by
                    # The user example shows "approve": true inside test object
                    for t in details:
                         # Check strict approval flags
                         if t.get("approve") is True or t.get("approve_by"):
                             is_approved = True
                             break
            except Exception as e:
                logger.error(f"Failed to parse testvalue testdetails for barcode {bc}: {e}")
                pass

            test_status_map[bc] = "Approved" if is_approved else "Tested"

        # 2. Check core_samplestatus (Sample flow)
        # We need to parse testdetails to check for "Received", "Collected", "Outsource"
        sample_docs = list(sample_col.find(
             {"barcode": {"$in": list(barcode_map.values())}},
             {"barcode": 1, "testdetails": 1, "_id": 0}
        ))
        
        sample_status_map = {} # barcode -> "Received" or "Collected" or "Outsource"
        for doc in sample_docs:
            bc = doc.get("barcode")
            if not bc: continue
            
            raw_details = doc.get("testdetails", "[]")
            current_status = None
            
            try:
                if isinstance(raw_details, str):
                    details = json.loads(raw_details)
                else:
                    details = raw_details
                    
                if isinstance(details, list):
                    # Priority in sample status: Received > Outsource > Collected (if mixed)
                    # Or check what's present.
                    statuses = {t.get("samplestatus") for t in details if t.get("samplestatus")}
                    
                    if "Received" in statuses:
                        current_status = "Received"
                    elif "Outsource" in statuses:
                        current_status = "Outsource" 
                    elif "Collected" in statuses: 
                        current_status = "Collected"
                    elif "Captured" in statuses: # Handling potential synonyms
                        current_status = "Collected"
            except Exception as e:
                logger.error(f"Failed to parse samplestatus testdetails for barcode {bc}: {e}")
                pass

            if current_status:
                sample_status_map[bc] = current_status

        # -------------------------
        # Final response
        # -------------------------
        result = []

        for record in billing_records:
            bill_no = record.get("bill_no")
            patient_id = record.get("patient_id")
            patient = patient_map.get(patient_id, {})

            barcode = barcode_map.get(bill_no, "N/A")
            tests = test_map.get(bill_no, [])

            # Determine Status (Precedence: Approved > Tested > Received > Outsource > Collected > Registered)
            status = record.get("status", "Registered") # Default from billing
            
            if barcode in test_status_map:
                status = test_status_map[barcode] # "Approved" or "Tested"
            elif barcode in sample_status_map:
                status = sample_status_map[barcode] # "Received" or "Outsource" or "Collected"
            
            # Unified search
            if search:
                s = search.lower()
                if not (
                    s in patient_id.lower() or
                    s in patient.get("patientname", "").lower() or
                    s in str(barcode).lower() or
                    s in status.lower() # Allow searching by status too
                ):
                    continue

            result.append({
                "date": record["date"].strftime("%Y-%m-%d"),
                "bill_no": bill_no,
                "barcode": barcode,
                "patient_id": patient_id,
                "patient_name": patient.get("patientname", "N/A"),
                "age": patient.get("age", "N/A"),
                "gender": patient.get("gender", "N/A"),
                "phone": patient.get("phone", ""),
                "tests": tests,
                "no_of_tests": len(tests),
                "total_amount": record.get("totalAmount", 0),
                "discount": record.get("discount", 0),
                "credit_amount": record.get("credit_amount", 0),
                "payment_method": record.get("payment_method", "N/A"),
                "sample_collector": get_employee_name(record.get("sample_collector", "")) or "N/A",
                "status": status,
                "refby": record.get("refby", "N/A"),
                "branch": record.get("branch", "N/A"),
                "B2B": record.get("B2B", "N/A"),
            })

        return JsonResponse(result, safe=False)

    except Exception as e:
        logger.error(f"ERROR in preetham_hospital_report: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_preethampatient_test_details(request):
    """
    Get detailed test information for a patient by barcode
    Used for generating PDF reports
    """
    try:
        client = get_client()
        db = client.Diagnostics

        barcode = request.GET.get("barcode")

        if not barcode:
            return JsonResponse(
                {"error": "barcode parameter is required"},
                status=400
            )

        testvalue_col = db.core_testvalue
        patient_col = db.core_patient
        billing_col = db.core_billing
        barcode_col = db.core_barcodetestdetails

        # 1. Fetch ALL Test Result Data (core_testvalue) for this barcode
        # (There might be multiple documents if tests were added separately)
        cursor = testvalue_col.find(
            {"barcode": barcode},
            {"_id": 0}
        )
        
        test_details = []
        patient_id = None
        
        # Iterate through all matching documents
        for doc in cursor:
            # Capture patient_id from any doc that has it
            if "patient_id" in doc and not patient_id:
                patient_id = doc.get("patient_id")
                
            # Parse testdetails (could be JSON string or list)
            raw_details = doc.get("testdetails", "[]")
            try:
                if isinstance(raw_details, str):
                    parsed = json.loads(raw_details)
                    if isinstance(parsed, list):
                        test_details.extend(parsed)
                elif isinstance(raw_details, list):
                    test_details.extend(raw_details)
            except Exception as e:
                logger.error(f"JSON extract error for barcode {barcode}: {e}")

        # 2. If no test details found in testvalue, return 404?
        # Or should we check core_billing/barcode_col for pending tests?
        # For a report, we typically only show results.
        if not test_details:
             logger.debug(f"No test details found in core_testvalue for {barcode}")
             # Optional: Check if we have pending tests in barcode_col
             
        # 3. Resolve Patient ID and Bill No
        bill_no = None
        
        # Check core_barcodetestdetails for linkage
        barcode_doc = barcode_col.find_one({"barcode": barcode}, {"_id": 0, "patient_id": 1, "bill_no": 1})
        if barcode_doc:
            if not patient_id:
                patient_id = barcode_doc.get("patient_id")
            bill_no = barcode_doc.get("bill_no")
            
        # If still missing patient_id, try billing
        if not patient_id and bill_no:
             bill_doc = billing_col.find_one({"bill_no": bill_no}, {"_id": 0, "patient_id": 1})
             if bill_doc:
                 patient_id = bill_doc.get("patient_id")

        # 4. Fetch Patient Demographics
        patient_data = {}
        if patient_id:
            patient_data = patient_col.find_one(
                {"patient_id": patient_id},
                {"_id": 0}
            ) or {}

        # 5. Fetch Billing Info (for RefBy, Branch, B2B)
        billing_data = {}
        if bill_no:
            billing_data = billing_col.find_one({"bill_no": bill_no}, {"_id": 0}) or {}
        elif patient_id:
             # Fallback: try to find billing by barcode if stored directly (rare but possible in legacy)
             billing_data = billing_col.find_one({"barcode": barcode}, {"_id": 0}) or {}

        # 6. Format Response
        result = {
            "patient_id": patient_id or "N/A",
            "patientname": patient_data.get("patientname", "N/A"),
            "age": patient_data.get("age", "N/A"),
            "age_type": patient_data.get("age_type", "Years"),
            "gender": patient_data.get("gender", "N/A"),
            "phone": patient_data.get("phone", ""),
            "email": patient_data.get("email", ""),
            "address": patient_data.get("address", ""),
            
            "refby": billing_data.get("refby", "N/A"),
            "branch": billing_data.get("branch", "N/A"),
            "B2B": billing_data.get("B2B", "N/A"),
            "bill_no": billing_data.get("bill_no", "N/A"),
            "bill_date": billing_data.get("date", ""),
            "sample_collector": get_employee_name(billing_data.get("sample_collector", "")) or "N/A",
            
            "barcodes": [barcode],
            "testdetails": test_details,
        }

        return JsonResponse(result, safe=False)

    except Exception as e:
        logger.error(f"ERROR in get_preethampatient_test_details: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def preetham_billing_dashboard(request):
    """
    Dashboard API for Preetham Hospital Billing
    - Total billing amount and counts
    - Payment status overview
    - Patient statistics
    - Test count based on test_id inside testdetails
    """

    try:
        client = get_client()
        db = client.Diagnostics
        billing_collection = db["core_billing"]

        # -------------------- Date Range --------------------
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")

        if not from_date or not to_date:
            return JsonResponse(
                {"error": "Both 'from_date' and 'to_date' are required"},
                status=400
            )

        try:
            from_date_parsed = datetime.strptime(from_date, "%Y-%m-%d")
            to_date_parsed = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            return JsonResponse(
                {"error": "Invalid date format. Use YYYY-MM-DD"},
                status=400
            )

        # -------------------- Query --------------------
        query = {
            "date": {"$gte": from_date_parsed, "$lt": to_date_parsed},
            "B2B": "PREETHAM HOSPITAL"
        }

        records = list(billing_collection.find(query))

        if not records:
            return JsonResponse({
                "total_count": 0,
                "total_amount": 0,
                "total_credit": 0,
                "total_discount": 0,
                "payment_status": {},
                "unique_patients": 0,
                "tests_count": 0,
                "status_breakdown": {}
            }, safe=False)

        # -------------------- Calculations --------------------
        total_count = len(records)
        total_amount = 0
        total_credit = 0
        total_discount = 0
        payment_status_map = {}
        unique_patients = set()
        tests_count = 0
        status_breakdown = {}

        for record in records:

            # ---------- Amounts ----------
            try:
                total_amount += float(record.get("totalAmount", 0) or 0)
            except Exception as e:
                logger.error(f"Failed to parse totalAmount: {e}")
                pass

            try:
                total_credit += float(record.get("credit_amount", 0) or 0)
            except Exception as e:
                logger.error(f"Failed to parse credit_amount: {e}")
                pass

            try:
                total_discount += float(record.get("discount", 0) or 0)
            except Exception as e:
                logger.error(f"Failed to parse discount: {e}")
                pass

            # ---------- Payment Method ----------
            payment_method = "N/A"
            try:
                payment_data = record.get("payment_method", {})
                if isinstance(payment_data, str):
                    payment_data = json.loads(payment_data.strip('"'))
                if isinstance(payment_data, dict):
                    payment_method = payment_data.get("paymentmethod", "N/A")
            except Exception as e:
                logger.error(f"Failed to parse payment_method: {e}")
                pass

            payment_status_map[payment_method] = payment_status_map.get(payment_method, 0) + 1

            # ---------- Unique Patients ----------
            if record.get("patient_id"):
                unique_patients.add(record.get("patient_id"))

            # ---------- Test Count (FROM testdetails) ----------
            try:
                testdetails = record.get("testdetails", [])

                if isinstance(testdetails, str):
                    testdetails = json.loads(testdetails)

                if isinstance(testdetails, list):
                    # count based on test_id
                    tests_count += len([t for t in testdetails if t.get("test_id")])
            except Exception as e:
                logger.error(f"Test parsing error: {e}")

            # ---------- Status Breakdown ----------
            status = record.get("status", "Unknown")
            status_breakdown[status] = status_breakdown.get(status, 0) + 1

        # -------------------- Response --------------------
        return JsonResponse({
            "total_count": total_count,
            "total_amount": round(total_amount, 2),
            "total_credit": round(total_credit, 2),
            "total_discount": round(total_discount, 2),
            "payment_status": payment_status_map,
            "unique_patients": len(unique_patients),
            "tests_count": tests_count,
            "status_breakdown": status_breakdown
        }, safe=False)

    except Exception as e:
        logger.error(f"Error in preetham_billing_dashboard: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)
    

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def preetham_hospital_ledger(request):
    try:
        from_date = request.GET.get('from_date')
        to_date = request.GET.get('to_date')

        logger.debug(f"preetham_hospital_ledger: from={from_date}, to={to_date}")

        query = {
            'B2B': 'PREETHAM HOSPITAL'
        }

        if from_date and to_date:
            try:
                start_date = datetime.strptime(from_date, "%Y-%m-%d")
                end_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
                query['date__range'] = [start_date, end_date]
            except ValueError:
                return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        billings = Billing.objects.filter(**query).order_by('date')

        data = []
        for bill in billings:
            try:
                total    = float(bill.totalAmount) if bill.totalAmount else 0
                net      = float(bill.netAmount)   if bill.netAmount   else 0
                discount = float(bill.discount)    if bill.discount    else 0
            except ValueError:
                total, net, discount = 0, 0, 0

            data.append({
                "id":           getattr(bill, 'id', str(bill.pk)),
                "date":         bill.date.strftime("%Y-%m-%d") if bill.date else "N/A",
                "bill_no":      bill.bill_no,
                "patient_name": bill.patientname,
                "b2b_name":     bill.B2B,
                "total_amount": total,
                "net_amount":   net,
                "discount":     discount,
            })

        return JsonResponse({"success": True, "data": data})

    except Exception as e:
        logger.error(f"Error in preetham_hospital_ledger: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse(
            {"success": False, "error": f"{str(e)} | {traceback.format_exc()}"},
            status=500
        )
