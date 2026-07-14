import json
import os
from pymongo import MongoClient
from django.db.models import Sum
from rest_framework.decorators import api_view
from rest_framework.response import Response
from ..models import Billing
import ast


import ast
import json
from rest_framework.decorators import api_view
from rest_framework.response import Response


#auth
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission
from django.views.decorators.csrf import csrf_exempt

from rest_framework.response import Response
from django.utils.dateparse import parse_date
import json, ast
from datetime import datetime, timedelta

@api_view(["POST", "GET"])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def test_summary(request):
    summary = {}

    # --- Filters ---
    if request.method == "POST":
        data = request.data
        search = data.get("search", "").strip().lower()
        from_date_str = data.get("from_date")
        to_date_str = data.get("to_date")
    else:
        search = request.GET.get("search", "").strip().lower()
        from_date_str = request.GET.get("from_date")
        to_date_str = request.GET.get("to_date")

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
    from ..models import Patient
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