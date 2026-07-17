"""
B2B ledger + home-collection billing report views.

Moved out of the original monolithic core/Views/report.py during the
report.py -> report/ package split. Contains:
  - b2b_ledger_report
  - get_home_collection_report
"""
import logging
from rest_framework.response import Response
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework import status
from urllib.parse import quote_plus
from core.mongo_client import get_client
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta
import re
from django.core.mail import EmailMessage
from django.conf import settings
from django.utils.timezone import make_aware
from core.utils import get_employee_name
from pyauth.auth import HasRoleAndDataPermission
from core.models import Patient, SampleStatus, Billing, TestValue, MBTestValue, BarcodeTestDetails
import os, json, traceback
import pytz
from django.utils.dateparse import parse_datetime
import gridfs
from core.pagination import paginate_queryset
from dotenv import load_dotenv

load_dotenv()

# Define IST timezone
TIME_ZONE = 'Asia/Kolkata'
IST = pytz.timezone(TIME_ZONE)

logger = logging.getLogger(__name__)


@api_view(['GET'])
@csrf_exempt
def b2b_ledger_report(request):
    try:
        from_date = request.GET.get('from_date')
        to_date = request.GET.get('to_date')
        b2b_name = request.GET.get('b2b_name')
        query = {}
        if from_date and to_date:
            try:
                start_date = datetime.strptime(from_date, "%Y-%m-%d")
                end_date = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
                query['date__range'] = [start_date, end_date]
            except ValueError:
                return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        if b2b_name and b2b_name != "All":
            query['B2B'] = b2b_name

        # Construct QuerySet
        billings_qs = Billing.objects.filter(**query)

        # Exclude empty B2B if showing "All" (implied by not having b2b_name or b2b_name == "All" if we handled it above)
        # Note: If b2b_name is None, we didn't add it to query.
        if not b2b_name or b2b_name == "All":
             # Use exclude instead of regex for better compatibility
            billings_qs = billings_qs.exclude(B2B__isnull=True).exclude(B2B__exact='')

        billings = billings_qs.order_by('date')

        data = []
        for bill in billings:
            try:
                total = float(bill.totalAmount) if bill.totalAmount else 0
                net = float(bill.netAmount) if bill.netAmount else 0
                discount = float(bill.discount) if bill.discount else 0
            except ValueError:
                total, net, discount = 0, 0, 0

            # Safely get ID
            bill_id = getattr(bill, 'id', str(bill.pk))

            data.append({
                "id": bill_id,
                "date": bill.date.strftime("%Y-%m-%d") if bill.date else "N/A",
                "bill_no": bill.bill_no,
                "patient_name": bill.patientname,
                "b2b_name": bill.B2B,
                "total_amount": total,
                "net_amount": net,
                "discount": discount
            })

        total_count = len(data)
        page_obj, page_meta = paginate_queryset(data, request)
        return JsonResponse({"success": True, "data": list(page_obj), "total_count": total_count, **page_meta})

    except Exception as e:
        logger.error(f"Error in b2b_ledger_report: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({"success": False, "error": f"{str(e)} | {traceback.format_exc()}"}, status=500)


@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_home_collection_report(request):
    """
    Get report specifically for Home Collection samples.
    Filters by Date and Segment = 'Home Collection'.
    Params are expected in the POST body.
    """
    try:
        data = request.data
        from_date = data.get('from_date')
        to_date = data.get('to_date')

        if not from_date or not to_date:
             return JsonResponse({"error": "Date range (from_date, to_date) is required"}, status=400)

        try:
            from_dt = datetime.strptime(from_date, '%Y-%m-%d')
            to_dt = datetime.strptime(to_date, '%Y-%m-%d') + timedelta(days=1) # Include full end date
        except ValueError:
             return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        # MongoDB setup (using same logic as overall_report)
        client = get_client()
        db = client.Diagnostics
        billing_collection = db["core_billing"]

        # Query for Home Collection in given date range
        query = {
            "date": {"$gte": from_dt, "$lt": to_dt},
            "segment": "Home Collection"
        }

        records = list(billing_collection.find(query))

        # If no records found with explicit segment, check for 'Walk-in' with a sample_collector assigned,
        # as sometimes logic might differ, but user explicitly asked for "Home Collection Details".
        # We will stick to segment="Home Collection" primarily.

        # Fetch Patient model details using patient_id from billing records
        patient_ids = [r.get('patient_id') for r in records if r.get('patient_id')]
        patient_map = {}
        if patient_ids:
            try:
                patient_records = Patient.objects.filter(patient_id__in=patient_ids).values(
                    'patient_id', 'patientname', 'age', 'age_type', 'gender', 'phone', 'address'
                )
                patient_map = {p['patient_id']: p for p in patient_records}
            except Exception as e:
                logger.error(f"Error fetching Patient model details: {e}")

        report_data = []
        for record in records:
            bill_no = record.get("bill_no", "N/A")
            pid = record.get("patient_id")

            p_details = patient_map.get(pid, {})

            # Helper to join address parts - Prioritize Patient Model -> Billing Record
            address_source = p_details.get("address") or record.get("address")
            formatted_address = "N/A"

            if address_source:
                if isinstance(address_source, str):
                    try:
                        addr_json = json.loads(address_source)
                        if isinstance(addr_json, dict):
                             parts = [str(v) for k, v in addr_json.items() if v]
                             formatted_address = ", ".join(parts)
                        else:
                            formatted_address = address_source
                    except Exception as e:
                        logger.exception(f"Error parsing address_source JSON in get_home_collection_report: {e}")
                        formatted_address = address_source
                elif isinstance(address_source, dict):
                    parts = [str(v) for k, v in address_source.items() if v]
                    formatted_address = ", ".join(parts)

            # Patient Name priority: Patient Model > Billing
            patient_name = p_details.get("patientname") or record.get("patientname") or "N/A"

            # Phone priority: Patient Model > Billing
            phone = p_details.get("phone") or record.get("phone") or "N/A"

            # Parse Test Names
            test_names = record.get("test_names", "")
            if not test_names:
                # Fallback to testdetails parsing similar to overall_report (simplified)
                td = record.get("testdetails")
                if isinstance(td, str):
                    try:
                        parsed = json.loads(td)
                        test_names = ", ".join([t.get("testname", "") for t in parsed if isinstance(t, dict)])
                    except Exception as e:
                        logger.exception(f"Error parsing testdetails JSON in get_home_collection_report: {e}")
                        pass
                elif isinstance(td, list):
                    test_names = ", ".join([t.get("testname", "") for t in td if isinstance(t, dict)])


            item = {
                "date": record.get("date").strftime("%Y-%m-%d") if record.get("date") else "N/A",
                "bill_no": bill_no,
                "patient_id": pid or "N/A",
                "patient_name": patient_name,
                "phone": phone,
                "address": formatted_address,
                "sample_collector": get_employee_name(record.get("sample_collector", "")) or "N/A",
                "test_names": test_names,
                "total_amount": record.get("totalAmount", 0),
                "paid_amount": record.get("paid_amount", 0),
                "balance_amount": record.get("balance_amount", 0),
                "payment_status": record.get("payment_status", "N/A"),
                "status": record.get("status", "N/A"),
                "remarks": record.get("remarks", "")
            }
            report_data.append(item)

        return JsonResponse({"data": report_data, "count": len(report_data)}, safe=False)

    except Exception as e:
        logger.error(f"Error in get_home_collection_report: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)
