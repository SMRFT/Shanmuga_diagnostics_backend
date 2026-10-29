import json
import os
import ast
from datetime import datetime, timedelta
from pymongo import MongoClient
from django.db.models import Sum
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from pyauth.auth import HasRoleAndDataPermission
from ..models import Billing, Patient


def _get_chc_test_summary_data(search="", from_date_str=None, to_date_str=None):
    summary = {}
    from_date = parse_date(from_date_str) if from_date_str else None
    to_date = parse_date(to_date_str) if to_date_str else None
    if from_date:
        from_date = datetime.combine(from_date, datetime.min.time())
    if to_date:
        to_date = datetime.combine(to_date, datetime.max.time())

    try:
        mongo_client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
        chc_db = mongo_client.Corporatehealthcheckup
        diag_db = mongo_client.Diagnostics
        billing_col = chc_db.core_billing
        reg_col = chc_db.core_chcregistration

        # Build test price & canonical name map
        valid_tests = {}
        test_prices = {}
        try:
            for t in diag_db.core_testdetails.find({}, {"test_id": 1, "test_name": 1, "MRP": 1, "amount": 1, "is_servicecharge": 1}):
                if "is_servicecharge" in t:
                    continue
                tid = t.get("test_id")
                tname = t.get("test_name")
                price = float(t.get("MRP") or t.get("amount") or 0)
                if tid:
                    valid_tests[str(tid)] = tname
                    test_prices[str(tid)] = price
                if tname:
                    test_prices[tname.strip().lower()] = price

            for ct in chc_db.core_chctest.find({}, {"test_name": 1, "test_price": 1, "test_id": 1}):
                cname = ct.get("test_name")
                ctid = ct.get("test_id")
                cprice = float(ct.get("test_price") or 0)
                if ctid:
                    valid_tests[str(ctid)] = cname
                    test_prices[str(ctid)] = cprice
                if cname:
                    test_prices[cname.strip().lower()] = cprice
        except Exception as e:
            print(f"Error fetching test details: {e}")

        # Build query on core_billing
        query = {}
        if from_date and to_date:
            query["date"] = {"$gte": from_date, "$lte": to_date}
        elif from_date:
            query["date"] = {"$gte": from_date}
        elif to_date:
            query["date"] = {"$lte": to_date}

        bills = list(billing_col.find(query, {"employee_id": 1, "testdetails": 1, "date": 1}))

        # Employee gender map
        employee_ids = list({b.get("employee_id") for b in bills if b.get("employee_id")})
        gender_map = {}
        if employee_ids:
            try:
                for r in reg_col.find({"employee_id": {"$in": employee_ids}}, {"employee_id": 1, "gender": 1}):
                    gender_map[str(r.get("employee_id"))] = str(r.get("gender") or "").lower()
            except Exception as e:
                print(f"Error fetching employee genders: {e}")

        for bill in bills:
            raw_data = bill.get("testdetails")
            if not raw_data:
                continue

            eid = str(bill.get("employee_id") or "")
            gender = gender_map.get(eid, "unknown")

            if isinstance(raw_data, list):
                test_list = raw_data
            elif isinstance(raw_data, str):
                try:
                    test_list = json.loads(raw_data)
                except Exception:
                    try:
                        test_list = ast.literal_eval(raw_data)
                    except Exception:
                        continue
            else:
                continue

            for test in test_list:
                if isinstance(test, str):
                    name = test
                    test_id_str = None
                elif isinstance(test, dict):
                    if "is_servicecharge" in test:
                        continue
                    name = test.get("testname") or test.get("test_name")
                    test_id = test.get("test_id") or test.get("testid")
                    test_id_str = str(test_id) if test_id else None
                else:
                    continue

                if test_id_str and test_id_str in valid_tests:
                    name = valid_tests[test_id_str]

                if not name:
                    continue

                if search and search not in name.lower():
                    continue

                price = 0.0
                if isinstance(test, dict):
                    price = float(test.get("MRP") or test.get("amount") or test.get("price") or 0)
                if price == 0.0:
                    if test_id_str and test_id_str in test_prices:
                        price = test_prices[test_id_str]
                    elif name.strip().lower() in test_prices:
                        price = test_prices[name.strip().lower()]

                if name not in summary:
                    summary[name] = {"count": 0, "total_amount": 0.0, "male_count": 0, "female_count": 0}

                summary[name]["count"] += 1
                summary[name]["total_amount"] += price
                if gender in ["male", "m"]:
                    summary[name]["male_count"] += 1
                elif gender in ["female", "f"]:
                    summary[name]["female_count"] += 1

    except Exception as e:
        print(f"Error in chc_test_summary: {e}")
        return []

    result = [
        {
            "test_name": k,
            "count": v["count"],
            "total_amount": round(v["total_amount"], 2),
            "male_count": v["male_count"],
            "female_count": v["female_count"]
        }
        for k, v in summary.items()
    ]
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


@api_view(["POST", "GET"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def test_summary(request):
    # --- Check if CHC source requested ---
    if request.method == "POST":
        data = request.data
        source = (data.get("source") or data.get("type") or "").strip().lower()
        search = data.get("search", "").strip().lower()
        from_date_str = data.get("from_date")
        to_date_str = data.get("to_date")
    else:
        source = (request.GET.get("source") or request.GET.get("type") or "").strip().lower()
        search = request.GET.get("search", "").strip().lower()
        from_date_str = request.GET.get("from_date")
        to_date_str = request.GET.get("to_date")

    if source == "chc":
        result = _get_chc_test_summary_data(search=search, from_date_str=from_date_str, to_date_str=to_date_str)
        return Response(result)

    summary = {}

    # Convert string to datetime
    from_date = parse_date(from_date_str) if from_date_str else None
    to_date = parse_date(to_date_str) if to_date_str else None
    if to_date:
        # include full day
        to_date = datetime.combine(to_date, datetime.max.time())

    # --- Queryset filtering (use bill_date instead of date) ---
    bills = Billing.objects.all()
    if from_date:
        bills = bills.filter(bill_date__gte=from_date)
    if to_date:
        bills = bills.filter(bill_date__lte=to_date)

    # Fetch patient gender mapping
    patient_ids = [bill.patient_id for bill in bills if bill.patient_id]
    patients = Patient.objects.filter(patient_id__in=patient_ids).values('patient_id', 'gender')
    patient_genders = {p['patient_id']: str(p['gender']).lower() for p in patients if p.get('gender')}

    # Fetch valid test mappings from MongoDB
    try:
        mongo_client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
        core_collection = mongo_client.Diagnostics.core_testdetails
        valid_tests = {}
        for t in core_collection.find({}, {"test_id": 1, "test_name": 1, "is_servicecharge": 1}):
            # Skip if 'is_servicecharge' field is present (regardless of true/false)
            if "is_servicecharge" in t:
                continue
            tid = t.get("test_id")
            if tid:
                valid_tests[str(tid)] = t.get("test_name")
    except Exception as e:
        print(f"Error fetching core_testdetails: {e}")
        valid_tests = {}

    # --- Process each bill ---
    for bill in bills:
        raw_data = bill.testdetails
        if not raw_data:
            continue

        gender = patient_genders.get(bill.patient_id, 'unknown')

        # Handle string / list
        if isinstance(raw_data, list):
            test_list = raw_data
        elif isinstance(raw_data, str):
            try:
                test_list = json.loads(raw_data)
            except Exception:
                try:
                    test_list = ast.literal_eval(raw_data)
                except Exception:
                    continue
        else:
            continue

        # Count tests
        for test in test_list:
            # Skip if 'is_servicecharge' field is present in billing testdetails
            if "is_servicecharge" in test:
                continue

            test_id = test.get("test_id") or test.get("testid")
            if not test_id:
                continue
                
            test_id_str = str(test_id)
            if test_id_str not in valid_tests:
                continue
                
            name = valid_tests[test_id_str]
            amount = float(test.get("MRP", test.get("amount", 0)))
            if not name:
                continue

            # Apply search filter
            if search and search not in name.lower():
                continue

            if name not in summary:
                summary[name] = {"count": 0, "total_amount": 0, "male_count": 0, "female_count": 0}
            summary[name]["count"] += 1
            summary[name]["total_amount"] += amount
            if gender in ['male', 'm']:
                summary[name]["male_count"] += 1
            elif gender in ['female', 'f']:
                summary[name]["female_count"] += 1

    # Format response
    result = [
        {
            "test_name": k, 
            "count": v["count"], 
            "total_amount": v["total_amount"],
            "male_count": v["male_count"],
            "female_count": v["female_count"]
        }
        for k, v in summary.items()
    ]
    return Response(result)


@api_view(["POST", "GET"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def chc_test_summary(request):
    if request.method == "POST":
        data = request.data
        search = data.get("search", "").strip().lower()
        from_date_str = data.get("from_date")
        to_date_str = data.get("to_date")
    else:
        search = request.GET.get("search", "").strip().lower()
        from_date_str = request.GET.get("from_date")
        to_date_str = request.GET.get("to_date")

    result = _get_chc_test_summary_data(search=search, from_date_str=from_date_str, to_date_str=to_date_str)
    return Response(result)
