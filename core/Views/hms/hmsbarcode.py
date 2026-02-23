from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from ...models import Hmsbarcode
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
            barcode = data.get('barcode') or data.get('barcode')

            # Check if barcode already exists
            if Hmsbarcode.objects.filter(barcode=barcode).exists():
                return JsonResponse({'error': 'Barcode already exists!'}, status=400)

            # Extract employee ID from request
            employee_id = data.get('auth-user-id')

            # Get all fields from the payload
            patient_id = data.get('patient_id', '')
            ipnumber = data.get('ipnumber', '')
            patientname = data.get('patientname', '')
            phone = data.get('phone', '')
            age = data.get('age', 0)
            age_type = data.get('age_type', '')
            billnumber = data.get('billnumber', '')
            gender = data.get('gender', '')
            barcode = data.get('barcode', '')
            IPOPType = data.get('IPOPType', '')
            ref_doctor = data.get('ref_doctor', '')



            date = data.get('date')
            testdetails = data.get('testdetails', [])



            # Convert string to datetime object if needed
            if date:
                try:
                    # Try parsing with time first
                    date = datetime.strptime(date, "%d/%m/%Y %H:%M")
                except ValueError:
                    try:
                        date = datetime.strptime(date, "%d/%m/%Y")
                    except ValueError:
                        # If it's already in ISO format from frontend
                        date = datetime.fromisoformat(date.replace('Z', '+00:00'))

            # Save all patient details
            Hmsbarcode.objects.create(
                patient_id=patient_id,
                ipnumber=ipnumber,
                patientname=patientname,
                phone=phone,
                age=age,
                age_type=age_type,
                billnumber=billnumber,
                gender=gender,
                barcode=barcode,
                IPOPType=IPOPType,
                ref_doctor=ref_doctor,
                date=date,
                testdetails=testdetails,
                created_by=employee_id,
            )

            return JsonResponse({'message': 'Barcodes saved successfully!'}, status=201)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)  
        

from datetime import datetime, time
import json
import os
import pytz

from pymongo import MongoClient
from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission


from rest_framework.decorators import api_view, permission_classes
from django.http import JsonResponse
from datetime import datetime, time
from pymongo import MongoClient
import pytz
import json
import os

@api_view(["GET"])
@permission_classes([HasRoleAndDataPermission])
def get_hms_barcode_by_date(request):

    from_date = request.GET.get("from_date")
    to_date = request.GET.get("to_date")
    single_date = request.GET.get("date")

    if single_date and not (from_date and to_date):
        from_date = single_date
        to_date = single_date

    if not from_date or not to_date:
        return JsonResponse(
            {"error": "from_date and to_date parameters are required"},
            status=400
        )

    try:
        # ------------------------------------------------
        # IST → UTC CONVERSION
        # ------------------------------------------------
        ist = pytz.timezone("Asia/Kolkata")
        utc = pytz.UTC

        start_ist = ist.localize(datetime.strptime(from_date, "%Y-%m-%d"))
        end_ist = ist.localize(
            datetime.combine(
                datetime.strptime(to_date, "%Y-%m-%d"),
                time(23, 59, 59)
            )
        )

        start_utc = start_ist.astimezone(utc)
        end_utc = end_ist.astimezone(utc)

        # ------------------------------------------------
        # MongoDB Connection
        # ------------------------------------------------
        client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
        db = client.Diagnostics

        billing_collection = db.core_hmspatientbilling
        machine_collection = db.machine_hmsmi
        test_collection = db.core_testdetails

        # ------------------------------------------------
        # DATE FILTERS
        # ------------------------------------------------
        billing_filter = {
            "date": {"$gte": start_utc, "$lte": end_utc}
        }

        machine_filter = {
            "BillDate": {"$gte": start_utc, "$lte": end_utc}
        }

        bill_map = {}
        billing_test_ids = set()
        machine_test_codes = set()

        # ------------------------------------------------
        # STEP 1: BILLING DATA
        # ------------------------------------------------
        for rec in billing_collection.find(billing_filter):
            bill_no = rec.get("billnumber")
            bill_type = rec.get("BillType")

            if not bill_no:
                continue

            bill_key = f"{bill_no}__{bill_type}"

            tests = rec.get("testdetails", [])
            if isinstance(tests, str):
                tests = json.loads(tests)

            test_ids = [t.get("test_id") for t in tests if t.get("test_id")]
            if not test_ids:
                continue

            billing_test_ids.update(test_ids)

            bill_map.setdefault(bill_key, {
                "source": "core_hmspatientbilling",
                "patient_id": rec.get("patient_id", ""),
                "patientname": rec.get("patientname", ""),
                "age": rec.get("age", ""),
                "gender": rec.get("gender", ""),
                "IPOPType": rec.get("IPOPType", ""),
                "BillType": bill_type,
                "phone": rec.get("phone", ""),
                "date": rec.get("date"),
                "ref_doctor": rec.get("ref_doctor", ""),
                "bill_no": bill_no,
                "test_ids": []
            })

            bill_map[bill_key]["test_ids"].extend(test_ids)

        # ------------------------------------------------
        # STEP 2: MACHINE DATA (FIXED: BILLNO + BILLTYPE)
        # ------------------------------------------------
        for rec in machine_collection.find(machine_filter):
            bill_no = rec.get("BillNumber")
            bill_type = rec.get("BillType")
            test_code = rec.get("TestCode")

            if not bill_no or not bill_type or not test_code:
                continue

            bill_key = f"{bill_no}__{bill_type}"

            machine_test_codes.add(test_code)

            if bill_key in bill_map:
                bill_map[bill_key].setdefault("test_codes", []).append(test_code)
                continue

            bill_map[bill_key] = {
                "source": "machine_hmsmi",
                "patient_id": rec.get("IPOPNumber", ""),
                "patientname": rec.get("PatientName", ""),
                "age": rec.get("PatientAge", ""),
                "gender": rec.get("Gender", ""),
                "IPOPType": rec.get("IPOPType", ""),
                "BillType": bill_type,
                "phone": rec.get("mobilenumber", ""),
                "date": rec.get("BillDate"),
                "ref_doctor": rec.get("RefDoctor", ""),
                "bill_no": bill_no,
                "test_codes": [test_code]
            }

        # ------------------------------------------------
        # STEP 3: TEST MASTER LOOKUP
        # ------------------------------------------------
        test_lookup_by_id = {}
        test_lookup_by_code = {}

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
        # STEP 4: RESPONSE (DEDUPLICATION SAFE)
        # ------------------------------------------------
        patient_data = []

        for rec in bill_map.values():
            testdetails = []
            seen_tests = set()

            # BILLING SOURCE
            if rec["source"] == "core_hmspatientbilling":
                for tid in rec.get("test_ids", []):
                    if tid in seen_tests:
                        continue

                    seen_tests.add(tid)
                    t = test_lookup_by_id.get(tid, {})

                    testdetails.append({
                        "test_id": tid,
                        "testname": t.get("test_name", "Unknown Test"),
                        "collection_container": t.get("collection_container", ""),
                        "shortcut": t.get("shortcut", ""),
                    })

            # MACHINE SOURCE
            else:
                for code in rec.get("test_codes", []):
                    t = test_lookup_by_code.get(code, {})
                    test_key = t.get("test_id") or code

                    if test_key in seen_tests:
                        continue

                    seen_tests.add(test_key)

                    testdetails.append({
                        "test_id": t.get("test_id"),
                        "hms_testcode": code,
                        "testname": t.get("test_name", "Unknown Test"),
                        "collection_container": t.get("collection_container", ""),
                        "shortcut": t.get("shortcut", ""),
                    })

            patient_data.append({
                "patient_id": rec["patient_id"],
                "patientname": rec["patientname"],
                "age": rec["age"],
                "age_type": "Y",
                "gender": rec["gender"],
                "IPOPType": rec["IPOPType"],
                "phone": rec["phone"],
                "bill_no": rec["bill_no"],
                "BillType": rec["BillType"],
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
        return JsonResponse({"error": str(e)}, status=500)  

