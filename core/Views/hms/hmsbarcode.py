from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from ...models import Hmsbarcode, HmspatientBilling
import json
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission


@api_view(["POST"])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def save_hms_barcodes(request):
    if request.method == "POST":
        try:
            data = request.data
            billnumber = data.get('billnumber')

            # Check if bill_no already exists
            if Hmsbarcode.objects.filter(billnumber=billnumber).exists():
                return JsonResponse({'error': 'Bill number already exists!'}, status=400)

            # Extract employee ID from request (same as your other function)
            employee_id = data.get('auth-user-id')

                    
            barcode = data.get('barcode')
            date = data.get('date')  # Date as a string
            testdetails = data.get('testdetails')

            # Convert string to date object if needed
            if date:
                try:
                    date = datetime.strptime(date, "%d/%m/%Y %H:%M").date()
                except ValueError:
                    date = datetime.strptime(date, "%d/%m/%Y").date()

            # Save patient details with created_by field
            Hmsbarcode.objects.create(
                
                date=date,                
                barcode=barcode,
                billnumber=billnumber,
                testdetails=testdetails,
                created_by=employee_id,  # Add the created_by field
            )
            return JsonResponse({'message': 'Barcodes saved successfully!'}, status=201)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
        
from rest_framework.decorators import api_view, permission_classes
from django.http import JsonResponse
from datetime import datetime
from pymongo import MongoClient
import os
@api_view(["GET"])
@permission_classes([HasRoleAndDataPermission])
def get_hms_barcode_by_date(request):
    """
    Fetch HMS barcode data from MongoDB using:
    - core_hmspatientbilling (PRIMARY)
    - machine_hmsmi (SECONDARY)
    - Multiple tests per BillNumber
    - Billing tests resolved using test_id
    - Machine tests resolved using hms_testcode
    """
    from_date = request.GET.get("from_date")
    to_date = request.GET.get("to_date")
    single_date = request.GET.get("date")
    if single_date and not (from_date and to_date):
        from_date = single_date
        to_date = single_date
    if not from_date or not to_date:
        return JsonResponse(
            {"error": "from_date and to_date parameters are required."},
            status=400
        )
    try:
        datetime.strptime(from_date, "%Y-%m-%d")
        datetime.strptime(to_date, "%Y-%m-%d")
        client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
        db = client.Diagnostics
        billing_collection = db.core_hmspatientbilling
        machine_collection = db.machine_hmsmi
        test_collection = db.core_testdetails
        # ------------------------------------------------
        # Date Filters (IST Safe)
        # ------------------------------------------------
        billing_date_expr = {
            "$expr": {
                "$and": [
                    {
                        "$gte": [
                            {"$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$date",
                                "timezone": "Asia/Kolkata"
                            }},
                            from_date
                        ]
                    },
                    {
                        "$lte": [
                            {"$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$date",
                                "timezone": "Asia/Kolkata"
                            }},
                            to_date
                        ]
                    }
                ]
            }
        }
        machine_date_expr = {
            "$expr": {
                "$and": [
                    {
                        "$gte": [
                            {"$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$BillDate",
                                "timezone": "Asia/Kolkata"
                            }},
                            from_date
                        ]
                    },
                    {
                        "$lte": [
                            {"$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$BillDate",
                                "timezone": "Asia/Kolkata"
                            }},
                            to_date
                        ]
                    }
                ]
            }
        }
        bill_map = {}
        billing_test_ids = set()
        machine_test_codes = set()
        # ------------------------------------------------
        # STEP 1: PRIMARY — Billing Collection
        # ------------------------------------------------
        for rec in billing_collection.find(billing_date_expr):
            bill_no = rec.get("billnumber")
            if not bill_no:
                continue
            tests = rec.get("testdetails", [])
            if isinstance(tests, str):
                try:
                    tests = json.loads(tests)
                except Exception:
                    tests = []
            if not tests:
                continue
            test_ids = []
            for t in tests:
                if t.get("test_id"):
                    test_ids.append(t["test_id"])
                    billing_test_ids.add(t["test_id"])
            if not test_ids:
                continue
            bill_map[bill_no] = {
                "source": "core_hmspatientbilling",
                "patient_id": rec.get("patient_id", ""),
                "patientname": rec.get("patientname", ""),
                "age": rec.get("age", ""),
                "gender": rec.get("gender", ""),
                "phone": rec.get("phone", ""),
                "date": rec.get("date"),
                "ref_doctor": rec.get("ref_doctor", ""),
                "test_ids": test_ids
            }
        # ------------------------------------------------
        # STEP 2: SECONDARY — Machine Collection
        # ------------------------------------------------
        for rec in machine_collection.find(machine_date_expr):
            bill_no = rec.get("BillNumber")
            test_code = rec.get("TestCode")
            if not bill_no or bill_no in bill_map or not test_code:
                continue
            machine_test_codes.add(test_code)
            bill_map[bill_no] = {
                "source": "machine_hmsmi",
                "patient_id": rec.get("IPOPNumber", ""),
                "patientname": rec.get("PatientName", ""),
                "age": rec.get("PatientAge", ""),
                "gender": rec.get("Gender", ""),
                "phone": rec.get("mobilenumber", ""),
                "date": rec.get("BillDate"),
                "ref_doctor": rec.get("RefDoctor", ""),
                "test_codes": [test_code]
            }
        # ------------------------------------------------
        # STEP 3: Resolve Test Master
        # ------------------------------------------------
        test_lookup_by_id = {}
        test_lookup_by_code = {}
        if billing_test_ids or machine_test_codes:
            for t in test_collection.find({
                "$or": [
                    {"test_id": {"$in": list(billing_test_ids)}},
                    {"hms_testcode": {"$in": list(machine_test_codes)}}
                ]
            }):
                if t.get("test_id"):
                    test_lookup_by_id[t["test_id"]] = t
                if t.get("hms_testcode"):
                    test_lookup_by_code[t["hms_testcode"]] = t
        # ------------------------------------------------
        # STEP 4: Final Response
        # ------------------------------------------------
        patient_data = []
        for bill_no, rec in bill_map.items():
            testdetails = []
            if rec["source"] == "core_hmspatientbilling":
                for tid in rec["test_ids"]:
                    t = test_lookup_by_id.get(tid, {})
                    testdetails.append({
                        "test_code": tid,
                        "testname": t.get("test_name", "Unknown Test"),
                        "collection_container": t.get("collection_container", ""),
                        "shortcut": t.get("shortcut", ""),
                        "status": "Pending",
                        "price": 0
                    })
            else:
                for code in rec["test_codes"]:
                    t = test_lookup_by_code.get(code, {})
                    testdetails.append({
                        "test_code": code,
                        "testname": t.get("test_name", "Unknown Test"),
                        "collection_container": t.get("collection_container", ""),
                        "shortcut": t.get("shortcut", ""),
                        "status": "Pending",
                        "price": 0
                    })
            patient_data.append({
                "patient_id": rec["patient_id"],
                "patientname": rec["patientname"],
                "age": rec["age"],
                "age_type": "Y",
                "gender": rec["gender"],
                "phone": rec["phone"],
                "bill_no": bill_no,
                "date": rec["date"],
                "ref_doctor": rec["ref_doctor"],
                "testdetails": testdetails,
                "source": rec["source"]
            })
        return JsonResponse({
            "data": patient_data,
            "summary": {
                "total_patients": len(patient_data),
                "date_range": {"from": from_date, "to": to_date}
            }
        }, safe=False)
    except Exception as e:
        return JsonResponse(
            {"error": f"Unexpected error occurred: {str(e)}"},
            status=500
        )
