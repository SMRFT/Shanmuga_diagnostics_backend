import json
from django.db.models import Sum
from rest_framework.decorators import api_view
from rest_framework.response import Response
from ..models import Billing
import ast


import ast
import json
from rest_framework.decorators import api_view
from rest_framework.response import Response



from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils.dateparse import parse_date
import json, ast
from datetime import datetime, timedelta

@api_view(["GET"])
def test_summary(request):
    summary = {}

    # --- Filters from query params ---
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

    # --- Process each bill ---
    for bill in bills:
        raw_data = bill.testdetails
        if not raw_data:
            continue

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
            name = test.get("test_name") or test.get("testname")
            amount = float(test.get("MRP", test.get("amount", 0)))
            if not name:
                continue

            # Apply search filter
            if search and search not in name.lower():
                continue

            if name not in summary:
                summary[name] = {"count": 0, "total_amount": 0}
            summary[name]["count"] += 1
            summary[name]["total_amount"] += amount

    # Format response
    result = [
        {"test_name": k, "count": v["count"], "total_amount": v["total_amount"]}
        for k, v in summary.items()
    ]
    return Response(result)