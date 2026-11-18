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
    bill_no_param = request.GET.get("bill_no")
    patient_id_param = request.GET.get("patient_id")

    # Single date support
    if single_date and not (from_date and to_date):
        from_date = single_date
        to_date = single_date

    if not from_date and not to_date and not bill_no_param and not patient_id_param:
        return JsonResponse(
            {"error": "from_date and to_date or bill_no parameters are required"},
            status=400
        )

    try:
        # IST → UTC conversion for querying HMS database
        ist = pytz.timezone("Asia/Kolkata")
        utc = pytz.UTC

        start_utc = None
        end_utc = None
        if from_date and to_date:
            try:
                def parse_input_date(d_str):
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
                        try:
                            return datetime.strptime(d_str, fmt)
                        except ValueError:
                            pass
                    return datetime.fromisoformat(d_str.replace("Z", "+00:00"))

                start_d = parse_input_date(from_date)
                end_d = parse_input_date(to_date)
                start_dt = start_d.date() if hasattr(start_d, "date") else start_d
                end_dt = end_d.date() if hasattr(end_d, "date") else end_d
                start_ist = ist.localize(datetime.combine(start_dt, time(0, 0, 0)))
                end_ist = ist.localize(datetime.combine(end_dt, time(23, 59, 59)))
                start_utc = start_ist.astimezone(utc)
                end_utc = end_ist.astimezone(utc)
                if start_utc > end_utc:
                    start_utc, end_utc = end_utc, start_utc
            except Exception:
                pass

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
            "item.billTypeNo": {"$in": ["LAB01", "LAB","LAB02","LAB03"]}
        }

        if bill_no_param:
            query["investBillNo"] = bill_no_param
        elif patient_id_param:
            query["$or"] = [
                {"uhid": patient_id_param},
                {"investBillNo": patient_id_param}
            ]
            if start_utc and end_utc:
                query["investBillDate"] = {"$gte": start_utc, "$lte": end_utc}
        elif start_utc and end_utc:
            query["investBillDate"] = {"$gte": start_utc, "$lte": end_utc}

        bills = list(invest_col.find(query).sort("investBillDate", -1))

        # Gather all unique uhids and test_ids
        uhids = set()
        test_ids = set()
        bill_numbers = []

        for b in bills:
            raw_uhid = b.get("uhid")
            if raw_uhid and str(raw_uhid).strip():
                uhids.add(str(raw_uhid).strip())
            bill_no = b.get("investBillNo")
            if bill_no:
                bill_numbers.append(bill_no)
            for item in b.get("item", []):
                if item.get("billTypeNo") in ["LAB01", "LAB","LAB02","LAB03"] and item.get("test_id") is not None:
                    tid_val = item.get("test_id")
                    test_ids.add(tid_val)
                    try:
                        test_ids.add(int(tid_val))
                    except (ValueError, TypeError):
                        pass
                    test_ids.add(str(tid_val))

        # Batch lookup patients
        patients = list(patient_col.find({"uhid": {"$in": list(uhids)}}))
        patient_map = {p["uhid"]: p for p in patients if p.get("uhid")}

        # Batch lookup test details
        tests_data = list(test_col.find({"test_id": {"$in": list(test_ids)}}))
        test_map = {}
        for t in tests_data:
            tid_k = t.get("test_id")
            if tid_k is not None:
                test_map[tid_k] = t
                test_map[str(tid_k)] = t
                try:
                    test_map[int(tid_k)] = t
                except (ValueError, TypeError):
                    pass

        # Batch lookup doctors from backend_diagnostics_profile
        doctor_ids = set()
        for b in bills:
            doc_id = b.get("doctor")
            if doc_id:
                doctor_ids.add(doc_id)
                doctor_ids.add(str(doc_id).strip())
                try:
                    doctor_ids.add(int(doc_id))
                except (ValueError, TypeError):
                    pass

        profile_col = client.Global.backend_diagnostics_profile
        doc_profiles = list(profile_col.find({"employeeId": {"$in": list(doctor_ids)}})) if doctor_ids else []
        doctor_map = {}
        for dp in doc_profiles:
            eid = dp.get("employeeId")
            if eid is not None:
                doctor_map[eid] = dp
                doctor_map[str(eid).strip()] = dp
                try:
                    doctor_map[int(eid)] = dp
                except (ValueError, TypeError):
                    pass

        # Check existing barcodes in Diagnostics database
        existing_barcodes = set(
            Hmsbarcode.objects.filter(
                billnumber__in=bill_numbers
            ).values_list("billnumber", flat=True)
        )

        bills_to_set_true = []
        bills_to_set_false = []

        patient_data = []
        for b in bills:
            raw_uhid = b.get("uhid")
            uhid = str(raw_uhid).strip() if raw_uhid else ""
            bill_no = b.get("investBillNo")
            if not bill_no:
                continue

            # If uhid is empty, use investBillNo as patient_id
            patient_id = uhid if uhid else bill_no

            # Check if barcode is already generated or not saved
            is_barcode_field = b.get("is_barcode")
            if is_barcode_field is True:
                is_generated = True
            elif is_barcode_field is False:
                is_generated = bill_no in existing_barcodes
            else:
                # Field not yet set in hospital_investbilling
                is_generated = bill_no in existing_barcodes
                if is_generated:
                    bills_to_set_true.append(bill_no)
                else:
                    bills_to_set_false.append(bill_no)

            status = "Generated" if is_generated else "Pending"

            # Patient demographics: extract fallback from bill document b
            b_salutation = str(b.get("salutation", "") or "").strip()
            b_first_name = str(b.get("firstName", "") or "").strip()
            b_last_name = str(b.get("lastName", "") or "").strip()
            b_name_parts = [pt for pt in [b_salutation, b_first_name, b_last_name] if pt]
            bill_patient_name = " ".join(b_name_parts).strip()

            patient_name = ""
            gender = b.get("gender") or "Male"
            phone = b.get("mobilePhone") or b.get("phone") or ""

            if uhid:
                p = patient_map.get(uhid)
                if p:
                    p_parts = [
                        str(p.get("salutation", "") or "").strip(),
                        str(p.get("firstName", "") or "").strip(),
                        str(p.get("lastName", "") or "").strip()
                    ]
                    p_name = " ".join([pt for pt in p_parts if pt]).strip()
                    if p_name:
                        patient_name = p_name
                    gender = p.get("gender") or gender
                    phone = p.get("mobilePhone") or p.get("phone") or phone
                else:
                    # Fallback to core_patient in Diagnostics
                    p_diag = diag_patient_col.find_one({"patient_id": uhid})
                    if p_diag:
                        patient_name = p_diag.get("patientname", "") or patient_name
                        gender = p_diag.get("gender") or gender
                        phone = p_diag.get("phone") or phone

            # If patient_name is still empty or uhid wasn't present, use bill document details
            if not patient_name:
                patient_name = bill_patient_name if bill_patient_name else "Unknown"

            # If ipNumber is present and not empty, save IPOPType as "IP", otherwise "OP"
            ip_number = b.get("ipNumber", "")
            ip_op_type = "IP" if ip_number else "OP"

            # Parse age
            age_raw = b.get("age", 0)
            try:
                age_val = int(float(age_raw))
            except (ValueError, TypeError):
                age_val = 0

            # Filter items with billTypeNo: "LAB01" or "LAB"
            bill_testdetails = []
            for item in b.get("item", []):
                if item.get("billTypeNo") in ["LAB01", "LAB","LAB02","LAB03"]:
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

            # Format bill date in IST so the frontend date picker and test details match correctly
            invest_date = b.get("investBillDate")
            date_ist_str = ""
            if invest_date:
                if isinstance(invest_date, datetime):
                    if invest_date.tzinfo is None:
                        invest_date_utc = pytz.UTC.localize(invest_date)
                    else:
                        invest_date_utc = invest_date.astimezone(pytz.UTC)
                    date_ist_str = invest_date_utc.astimezone(ist).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    date_ist_str = str(invest_date)

            raw_doc = b.get("doctor", "SELF")
            matched_dp = doctor_map.get(raw_doc)
            ref_doctor_name = matched_dp.get("employeeName") if matched_dp else raw_doc
            ref_doctor_id = str(matched_dp.get("employeeId")) if matched_dp else (str(raw_doc) if raw_doc != "SELF" else "")

            patient_data.append({
                "patient_id": patient_id,
                "patientname": patient_name,
                "age": age_val,
                "age_type": b.get("age_type", "Y") or "Y",
                "gender": gender,
                "IPOPType": ip_op_type,
                "phone": phone,
                "bill_no": bill_no,
                "BillType": b.get("bill_type"),
                "ipnumber": ip_number,
                "date": date_ist_str,
                "ref_doctor": ref_doctor_name,
                "ref_doctor_id": ref_doctor_id,
                "testdetails": bill_testdetails,
                "barcode_status": status,
                "is_barcode": is_generated,
                "is_emergency": b.get("is_emergency", False),
                "patient_history": "",
                "sample_collector": "",
                "source": "SH"
            })

        # Persist is_barcode flag in hospital_investbilling for uninitialized records
        if bills_to_set_true:
            invest_col.update_many(
                {"investBillNo": {"$in": bills_to_set_true}},
                {"$set": {"is_barcode": True}}
            )
        if bills_to_set_false:
            invest_col.update_many(
                {"investBillNo": {"$in": bills_to_set_false}},
                {"$set": {"is_barcode": False}}
            )

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
                'barcode': barcode_record.barcode,
                'ref_doctor': barcode_record.ref_doctor,
                'ref_doctor_id': getattr(barcode_record, 'ref_doctor_id', '') or ''
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
            bill_no = data.get('bill_no') or data.get('billnumber')

            if not bill_no:
                return JsonResponse({'error': 'Bill number is required!'}, status=400)

            # Check if this is a request to mark barcode as not saved / pending
            is_barcode_flag = data.get('is_barcode', True)
            if is_barcode_flag is False or str(is_barcode_flag).lower() == 'false':
                client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
                client.HMS.hospital_investbilling.update_one(
                    {"investBillNo": bill_no},
                    {"$set": {"is_barcode": False}}
                )
                Hmsbarcode.objects.filter(billnumber=bill_no).delete()
                return JsonResponse({'message': 'Barcode status marked as not saved successfully!'}, status=200)

            # Check if bill number already exists in Hmsbarcode
            if Hmsbarcode.objects.filter(billnumber=bill_no).exists():
                # Ensure is_barcode is True in hospital_investbilling
                try:
                    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
                    client.HMS.hospital_investbilling.update_one(
                        {"investBillNo": bill_no},
                        {"$set": {"is_barcode": True}}
                    )
                except Exception:
                    pass
                return JsonResponse({'error': 'Bill number already exists!'}, status=400)

            employee_id = data.get('auth-user-id')

            patient_id = data.get('patient_id')
            if not patient_id:
                patient_id = bill_no
            patientname = data.get('patientname')
            age = data.get('age')
            age_type = data.get('age_type', 'Y')
            gender = data.get('gender')
            phone = data.get('phone', '')
            barcode = data.get('barcode')
            date = data.get('date')
            testdetails = data.get('testdetails')
            ref_doctor_raw = data.get('ref_doctor', '')
            ref_doctor_id_raw = data.get('ref_doctor_id', '')
            ipnumber = data.get('ipnumber', '')
            IPOPType = data.get('IPOPType', '')

            # Resolve doctor against Global.backend_diagnostics_profile
            client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
            db_hms = client.HMS
            db_global = client.Global
            profile_col = db_global.backend_diagnostics_profile

            # Fallback for patientname if missing or "Unknown"
            if not patientname or patientname == "Unknown":
                bill_doc_for_patient = db_hms.hospital_investbilling.find_one({"investBillNo": bill_no})
                if bill_doc_for_patient:
                    b_parts = [
                        str(bill_doc_for_patient.get("salutation", "") or "").strip(),
                        str(bill_doc_for_patient.get("firstName", "") or "").strip(),
                        str(bill_doc_for_patient.get("lastName", "") or "").strip()
                    ]
                    b_full = " ".join([pt for pt in b_parts if pt]).strip()
                    if b_full:
                        patientname = b_full
                    if not gender:
                        gender = bill_doc_for_patient.get("gender") or "Male"
                    if not phone:
                        phone = bill_doc_for_patient.get("mobilePhone") or bill_doc_for_patient.get("phone") or ""

            # Determine doctor identifier candidate
            doc_candidate = ref_doctor_id_raw or ref_doctor_raw
            if not doc_candidate or doc_candidate == "SELF":
                bill_doc = db_hms.hospital_investbilling.find_one({"investBillNo": bill_no})
                if bill_doc and bill_doc.get("doctor"):
                    doc_candidate = bill_doc.get("doctor")

            ref_doctor_name = ref_doctor_raw or "SELF"
            ref_doctor_id = str(doc_candidate) if doc_candidate and doc_candidate != "SELF" else ""

            if doc_candidate and doc_candidate != "SELF":
                # Compare doctor with backend_diagnostics_profile - employeeId
                matched_profile = profile_col.find_one({
                    "$or": [
                        {"employeeId": str(doc_candidate).strip()},
                        {"employeeId": doc_candidate}
                    ]
                })
                if not matched_profile:
                    try:
                        matched_profile = profile_col.find_one({"employeeId": int(doc_candidate)})
                    except (ValueError, TypeError):
                        pass

                # If doc_candidate was already doctor name, match by employeeName
                if not matched_profile:
                    matched_profile = profile_col.find_one({"employeeName": str(doc_candidate).strip()})

                if matched_profile:
                    ref_doctor_name = matched_profile.get("employeeName") or ref_doctor_raw
                    ref_doctor_id = str(matched_profile.get("employeeId", doc_candidate))

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
                ref_doctor=ref_doctor_name,
                ref_doctor_id=ref_doctor_id,
                date=date_obj,
                testdetails=testdetails,
                created_by=employee_id,
                location_id="hms"
            )

            # Update is_barcode = True in hospital_investbilling
            try:
                client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
                client.HMS.hospital_investbilling.update_one(
                    {"investBillNo": bill_no},
                    {"$set": {"is_barcode": True}}
                )
            except Exception as mongo_err:
                print("Error updating hospital_investbilling is_barcode:", mongo_err)

            return JsonResponse({'message': 'Barcodes saved successfully!'}, status=201)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
