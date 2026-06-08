from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, time
import json
import os
import pytz
from pymongo import MongoClient
from pyauth.auth import HasRoleAndDataPermission
from ...models import Hmsbarcode

@api_view(["GET"])
@permission_classes([HasRoleAndDataPermission])
def get_sh_hms_barcode_by_date(request):
    from_date = request.GET.get("from_date")
    to_date = request.GET.get("to_date")
    single_date = request.GET.get("date")

    # Single date support
    if single_date and not (from_date and to_date):
        from_date = single_date
        to_date = single_date

    if not from_date or not to_date:
        return JsonResponse(
            {"error": "from_date and to_date parameters are required"},
            status=400
        )

    try:
        # IST → UTC conversion for querying HMS database
        ist = pytz.timezone("Asia/Kolkata")
        utc = pytz.UTC

        start_ist = ist.localize(datetime.strptime(from_date, "%Y-%m-%d"))
        end_ist = ist.localize(datetime.combine(datetime.strptime(to_date, "%Y-%m-%d"), time(23, 59, 59)))

        start_utc = start_ist.astimezone(utc)
        end_utc = end_ist.astimezone(utc)

        # MongoDB Connection
        client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
        db_hms = client.HMS
        db_diag = client.Diagnostics

        invest_col = db_hms.hospital_investbilling
        patient_col = db_hms.hospital_patient
        test_col = db_diag.core_testdetails
        diag_patient_col = db_diag.core_patient

        # Query bills in the given range with lab items
        query = {
            "investBillDate": {
                "$gte": start_utc,
                "$lte": end_utc
            },
            "item.billTypeNo": "LAB01"
        }

        bills = list(invest_col.find(query).sort("investBillDate", -1))

        # Gather all unique uhids and test_ids
        uhids = set()
        test_ids = set()
        bill_numbers = []

        for b in bills:
            uhid = b.get("uhid")
            if uhid:
                uhids.add(uhid)
            bill_no = b.get("investBillNo")
            if bill_no:
                bill_numbers.append(bill_no)
            for item in b.get("item", []):
                if item.get("billTypeNo") == "LAB01" and item.get("test_id") is not None:
                    test_ids.add(item.get("test_id"))

        # Batch lookup patients
        patients = list(patient_col.find({"uhid": {"$in": list(uhids)}}))
        patient_map = {p["uhid"]: p for p in patients if p.get("uhid")}

        # Batch lookup test details
        tests_data = list(test_col.find({"test_id": {"$in": list(test_ids)}}))
        test_map = {t["test_id"]: t for t in tests_data if t.get("test_id") is not None}

        # Check existing barcodes in Diagnostics database
        existing_barcodes = set(
            Hmsbarcode.objects.filter(
                billnumber__in=bill_numbers
            ).values_list("billnumber", flat=True)
        )

        patient_data = []
        for b in bills:
            uhid = b.get("uhid")
            bill_no = b.get("investBillNo")
            if not bill_no:
                continue

            # Get patient demographics
            patient_name = "Unknown"
            gender = "Male"
            phone = ""

            p = patient_map.get(uhid)
            if p:
                patient_name = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
                gender = p.get('gender', 'Male')
                phone = p.get('mobilePhone', '')
            else:
                # Fallback to core_patient in Diagnostics
                p_diag = diag_patient_col.find_one({"patient_id": uhid})
                if p_diag:
                    patient_name = p_diag.get("patientname", "")
                    gender = p_diag.get("gender", "Male")
                    phone = p_diag.get("phone", "")

            # If ipNumber is present and not empty, save IPOPType as "IP", otherwise "OP"
            ip_number = b.get("ipNumber", "")
            ip_op_type = "IP" if ip_number else "OP"

            # Parse age
            age_raw = b.get("age", 0)
            try:
                age_val = int(float(age_raw))
            except (ValueError, TypeError):
                age_val = 0

            # Filter items with billTypeNo: "LAB01"
            bill_testdetails = []
            for item in b.get("item", []):
                if item.get("billTypeNo") == "LAB01":
                    tid = item.get("test_id")
                    t_master = test_map.get(tid, {})
                    bill_testdetails.append({
                        "test_id": tid,
                        "hms_testcode": t_master.get("hms_testcode", ""),
                        "testname": t_master.get("test_name", item.get("itemName", "Unknown Test")),
                        "collection_container": t_master.get("collection_container", ""),
                        "shortcut": t_master.get("shortcut", ""),
                        "suffix": t_master.get("suffix", ""),
                    })

            # Check if barcode is already generated
            status = "Generated" if bill_no in existing_barcodes else "Pending"

            patient_data.append({
                "patient_id": uhid,
                "patientname": patient_name,
                "age": age_val,
                "age_type": b.get("age_type", "Y") or "Y",
                "gender": gender,
                "IPOPType": ip_op_type,
                "phone": phone,
                "bill_no": bill_no,
                "BillType": b.get("bill_type"),
                "ipnumber": ip_number,
                "date": b.get("investBillDate"),
                "ref_doctor": b.get("doctor", "SELF"),
                "testdetails": bill_testdetails,
                "barcode_status": status,
                "is_emergency": b.get("is_emergency", False),
                "patient_history": "",
                "sample_collector": ""
            })

        return JsonResponse({
            "data": patient_data,
            "summary": {
                "total_patients": len(patient_data),
                "date_range": {
                    "from": from_date,
                    "to": to_date
                }
            }
        }, safe=False)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_sh_hms_existing_barcode(request):
    patient_id = request.GET.get('patient_id')
    date = request.GET.get('date')
    bill_no = request.GET.get('bill_no')

    if not patient_id and not bill_no:
        return JsonResponse({'error': 'Either Patient ID or Bill No is required.'}, status=400)

    try:
        query_filter = {}
        if patient_id:
            query_filter['patient_id'] = patient_id
        if bill_no:
            query_filter['billnumber'] = bill_no
        if date:
            try:
                parsed_date = datetime.strptime(date, '%Y-%m-%d').date()
                query_filter['date'] = parsed_date
            except ValueError:
                pass

        barcode_record = Hmsbarcode.objects.filter(**query_filter).first()

        if barcode_record:
            testdetails = barcode_record.testdetails
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails)
                except json.JSONDecodeError:
                    pass

            return JsonResponse({
                'patient_id': barcode_record.patient_id,
                'patientname': barcode_record.patientname,
                'age': barcode_record.age,
                'gender': barcode_record.gender,
                'date': barcode_record.date.strftime('%Y-%m-%d') if barcode_record.date else '',
                'bill_no': barcode_record.billnumber,
                'testdetails': testdetails,
                'barcode': barcode_record.barcode
            }, status=200)

        return JsonResponse({'message': 'No barcode found for the given details.'}, status=404)

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

@api_view(["POST"])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def save_sh_hms_barcodes(request):
    if request.method == "POST":
        try:
            data = request.data
            bill_no = data.get('bill_no')

            # Check if bill number already exists in Hmsbarcode
            if Hmsbarcode.objects.filter(billnumber=bill_no).exists():
                return JsonResponse({'error': 'Bill number already exists!'}, status=400)

            employee_id = data.get('auth-user-id')

            patient_id = data.get('patient_id')
            patientname = data.get('patientname')
            age = data.get('age')
            age_type = data.get('age_type', 'Y')
            gender = data.get('gender')
            phone = data.get('phone', '')
            barcode = data.get('barcode')
            date = data.get('date')
            testdetails = data.get('testdetails')
            ref_doctor = data.get('ref_doctor', '')
            ipnumber = data.get('ipnumber', '')
            IPOPType = data.get('IPOPType', '')

            # Convert string to date object
            if date:
                try:
                    date_obj = datetime.strptime(date, "%d/%m/%Y %H:%M").date()
                except ValueError:
                    try:
                        date_obj = datetime.strptime(date, "%d/%m/%Y").date()
                    except ValueError:
                        try:
                            date_obj = datetime.strptime(date, "%Y-%m-%d").date()
                        except ValueError:
                            date_obj = datetime.fromisoformat(date.replace('Z', '+00:00')).date()
            else:
                date_obj = datetime.now().date()

            try:
                age_val = int(float(age))
            except (ValueError, TypeError):
                age_val = 0

            # Save patient details to Hmsbarcode
            Hmsbarcode.objects.create(
                patient_id=patient_id,
                ipnumber=ipnumber,
                patientname=patientname,
                phone=phone,
                age=age_val,
                age_type=age_type,
                billnumber=bill_no,
                gender=gender,
                barcode=barcode,
                IPOPType=IPOPType,
                ref_doctor=ref_doctor,
                date=date_obj,
                testdetails=testdetails,
                created_by=employee_id,
                location_id="hms"
            )
            return JsonResponse({'message': 'Barcodes saved successfully!'}, status=201)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
