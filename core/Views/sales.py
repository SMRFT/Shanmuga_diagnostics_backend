from django.http import JsonResponse, HttpResponse
from bson.objectid import ObjectId
import gridfs
from rest_framework.response import Response
from rest_framework import status
from ..models import SalesVisitLog,Billing,Patient,ClinicalName,SalesPlan,TOTAL_ROW_EMPLOYEE_ID
from ..serializers import SalesVisitLogSerializer, HospitalLabSerializer,PatientSerializer,BillingSerializer,ClinicalNameSerializer,SalesPlanSerializer
from django.db.models import Max
import re
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
import calendar

from django.utils.timezone import now
import os
from datetime import date as date_cls

from ..models import HospitalLab

@api_view(['POST', 'GET'])
@permission_classes([HasRoleAndDataPermission])
def hospitallabform(request):
    if request.method == 'GET':
        labs = HospitalLab.objects.all()
        serializer = HospitalLabSerializer(labs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    elif request.method == 'POST':
        serializer = HospitalLabSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {"success": True, "message": "Hospital/Lab details saved successfully."},
                status=status.HTTP_201_CREATED
            )
        return Response(
            {"success": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )

@csrf_exempt
@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def salesvisitlog(request):
    if request.method == 'POST':
        try:
            data = request.data.copy()  
            employee_id = data.get('auth-user-id')
            
            # Handle Image Upload
            visit_image = request.FILES.get('visit_image')
            if visit_image:
                import gridfs
                from PIL import Image, ImageDraw, ImageFont, ImageOps
                from io import BytesIO

                # Open image
                img = Image.open(visit_image)
                
                # Correct orientation if needed
                img = ImageOps.exif_transpose(img)

                # Convert to RGB if necessary
                if img.mode != 'RGB':
                    img = img.convert('RGB')

                # Prepare text to draw
                draw = ImageDraw.Draw(img)
                width, height = img.size
                
                # Timestamp
                timestamp_text = f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                
                # Location
                latitude = data.get('latitude')
                longitude = data.get('longitude') 
                location_text = ""
                if latitude and longitude:
                    location_text = f"Loc: {latitude}, {longitude}"

                # Text Settings (aim for some scaling based on image size)
                font_size = int(height * 0.03) 
                if font_size < 15: font_size = 15
                
                # Try to load a font, fallback to default
                try:
                    font = ImageFont.truetype("arial.ttf", font_size)
                except:
                    font = ImageFont.load_default()

                # Calculate text position (bottom right or bottom left)
                # Let's put it at bottom left with some padding
                x = 20
                y = height - (font_size * 3) - 20
                
                # Draw text with shadow/outline for visibility
                text_color = (255, 255, 255)
                outline_color = (0, 0, 0)
                
                def draw_text_with_outline(position, text, font):
                    x, y = position
                    # Outline
                    for adj in [-2, 0, 2]:
                        for adj2 in [-2, 0, 2]:
                            draw.text((x+adj, y+adj2), text, font=font, fill=outline_color)
                    # Main text
                    draw.text(position, text, font=font, fill=text_color)

                draw_text_with_outline((x, y), timestamp_text, font)
                if location_text:
                    draw_text_with_outline((x, y + font_size + 5), location_text, font)

                # Save modified image to bytes
                output_buffer = BytesIO()
                img.save(output_buffer, format='JPEG', quality=85)
                output_buffer.seek(0)
                
                fs = gridfs.GridFS(db)
                file_id = fs.put(
                    output_buffer,
                    filename=f"tagged_{visit_image.name}",
                    content_type="image/jpeg",
                    uploaded_by=employee_id,
                    uploaded_date=datetime.now()
                )
                data['visit_image_id'] = str(file_id)

            # Add audit fields
            data['created_by'] = employee_id
            data['created_date'] = datetime.now()

            serializer = SalesVisitLogSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from pymongo import MongoClient
# Connect to MongoDB
client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
db = client["Diagnostics"]
@api_view(['GET'])
# @permission_classes([HasRoleAndDataPermission])
def get_all_clinicalnames(request):
    try:
        # --- Fetch from core_clinicalname ---
        clinical_cursor = db.core_clinicalname.find({}, {
            "_id": 0,
            "clinicalname": 1,
            "type": 1,
            "salesMapping": 1,
            "contactPerson": 1,
            "phone": 1,
            "email": 1,
            "address":1,
            "referrerCode": 1,
        })
        clinical_list = list(clinical_cursor)
        for item in clinical_list:
            item["hospitalName"] = item.get("clinicalname", "")
            item["contactNumber"] = item.get("phone", "")
            item["emailId"] = item.get("email", ""),
            item["referrerCode"] = item.get("referrerCode", "")
            item["address"] = item.get("address", "")
        # --- Fetch from core_hospitallab ---
        hospital_cursor = db.core_hospitallab.find({}, {
            "_id": 0,
            "clinicalname": 1,  # :white_check_mark: FIXED: fetch clinicalname instead of hospitalName
            "type": 1,
            "salesMapping": 1,
            "contactPerson": 1,
            "contactNumber": 1,
            "emailId": 1,
            "address":1,
            "referrerCode": 1,
        })
        hospital_list = list(hospital_cursor)
        for item in hospital_list:
            # :white_check_mark: Match structure with clinical_list
            item["hospitalName"] = item.get("clinicalname", "")
            item["referrerCode"] = item.get("referrerCode", "")
            item["address"] = item.get("address", "")
        # --- Merge both ---
        combined = clinical_list + hospital_list
        return Response(
            {"success": True, "data": combined},
            status=status.HTTP_200_OK
        )
    except Exception as e:
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


from django.http import JsonResponse
from pymongo import MongoClient
import os


def _get_sales_executive_rows():
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Global"]
    collection = db["backend_diagnostics_profile"]

    query = {
        "$or": [
            {"primaryRole": "SD-R-SE"},
            {"additionalRoles": "SD-R-SE"},
            {"designation": "SD-R-SE"}
        ]
    }

    return list(collection.find(query, {"employeeName": 1, "employeeId": 1, "_id": 0}))


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_sales_executives(request):
    employees = _get_sales_executive_rows()
    return JsonResponse(employees, safe=False)


from datetime import datetime, date
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_sales_individual_report(request):
        date_param = request.GET.get("date")  # YYYY-MM or YYYY-MM-DD
        salesMapping = request.GET.get("salesMapping")

        if not date_param or not salesMapping:
            return JsonResponse({"error": "Date and user salesmapping name are required"}, status=400)

        try:
            if len(date_param) == 7:  # Filtering by month (YYYY-MM)
                year, month = map(int, date_param.split("-"))
                start_date = date(year, month, 1)  # First day of the month
                if month == 12:
                    end_date = date(year + 1, 1, 1)  # Start of next year
                else:
                    end_date = date(year, month + 1, 1)  # Start of next month
                sales_logs = SalesVisitLog.objects.filter(
                    date__gte=start_date, date__lt=end_date, salesMapping=salesMapping
                )
            else:  # Filtering by full date (YYYY-MM-DD)
                selected_date = datetime.strptime(date_param, "%Y-%m-%d").date()  # Correct usage
                sales_logs = SalesVisitLog.objects.filter(date=selected_date, salesMapping=salesMapping)

        except ValueError:
            return JsonResponse({"error": "Invalid date format"}, status=400)

        serializer = SalesVisitLogSerializer(sales_logs, many=True)
        return JsonResponse(serializer.data, safe=False)



@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def Adminview_salesexecutive_report(request):
    from_date = request.query_params.get('fromDate')
    to_date = request.query_params.get('toDate')
    sales_executive = request.query_params.get('salesExecutive')  # key name: salesExecutive

    query = {}

    # Date range filter
    if from_date and to_date:
        try:
            from_date_parsed = datetime.strptime(from_date, "%Y-%m-%d")
            to_date_parsed = datetime.strptime(to_date, "%Y-%m-%d")
            query['date__gte'] = from_date_parsed
            query['date__lte'] = to_date_parsed
        except ValueError:
            return Response({"error": "Invalid fromDate or toDate format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

    # Sales Executive filter (case-insensitive)
    if sales_executive:
        query['salesMapping__icontains'] = sales_executive

    logs = SalesVisitLog.objects.filter(**query)
    serializer = SalesVisitLogSerializer(logs, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)



from datetime import datetime, timedelta
import json
from calendar import monthrange
from django.http import JsonResponse
from django.utils import timezone
from django.utils.timezone import make_aware
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes

def _to_float(value):
    """netAmount is declared as CharField but Mongo may hand back a raw
    number or a numeric string depending on how the doc was inserted."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
 
 
def _make_aware_if_needed(dt):
    if settings.USE_TZ and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
 
 
def _month_bounds(year, month):
    month_start = timezone.make_aware(datetime(year, month, 1, 0, 0, 0))
    if month == 12:
        month_end = timezone.make_aware(datetime(year + 1, 1, 1, 0, 0, 0))
    else:
        month_end = timezone.make_aware(datetime(year, month + 1, 1, 0, 0, 0))
    return month_start, month_end


def _summarize(bills):
    """Aggregate a list of Billing instances into the stat block the
    frontend renders. Cancelled/refunded line items are excluded from
    totalTests and testCounts since they weren't actually delivered."""
    patient_ids = set()
    total_amount = 0.0
    total_tests = 0
    test_counts = {}
 
    for bill in bills:
        if bill.patient_id:
            patient_ids.add(bill.patient_id)
 
        total_amount += _to_float(bill.netAmount)
 
        for test in (bill.testdetails or []):
            if test.get('refund') or test.get('cancellation'):
                continue
            total_tests += 1
            name = test.get('testname') or 'Unknown'
            test_counts[name] = test_counts.get(name, 0) + 1
 
    return {
        'totalPatients': len(patient_ids),
        'totalAmount': round(total_amount, 2),
        'totalTests': total_tests,
        'testCounts': test_counts,
    }
 
 
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def salesdashboard(request):
    sales_mapping = (request.GET.get('salesMapping') or '').strip()
    employee_id = (request.GET.get('employeeId') or '').strip()
    date_param = request.GET.get('date')
    month_param = request.GET.get('month')
 
    # ---- build the salesMapping/employeeId filter ----
    # salesMapping on Billing sometimes holds the executive's name and
    # sometimes their id, so match against either. Empty means "All".
    filters = Q()
    if sales_mapping or employee_id:
        mapping_filter = Q()
        if sales_mapping:
            mapping_filter |= Q(salesMapping=sales_mapping)
        if employee_id:
            mapping_filter |= Q(salesMapping=employee_id)
        filters &= mapping_filter
 
    # ---- resolve the date range ----
    trend_days = []  # list of date objects, only populated for month filter
 
    if month_param:
        try:
            year, month = (int(part) for part in month_param.split('-'))
        except (ValueError, AttributeError):
            today = timezone.localdate() if settings.USE_TZ else datetime.today().date()
            year, month = today.year, today.month
 
        range_start = datetime(year, month, 1)
        days_in_month = monthrange(year, month)[1]
        range_end = range_start + timedelta(days=days_in_month)
        trend_days = [range_start + timedelta(days=i) for i in range(days_in_month)]
    else:
        if date_param:
            try:
                day = datetime.strptime(date_param, '%Y-%m-%d')
            except ValueError:
                return Response(
                    {'error': "Invalid 'date' format, expected YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            # No date sent at all -> default to today, per requirement #1
            today = timezone.localdate() if settings.USE_TZ else datetime.today().date()
            day = datetime(today.year, today.month, today.day)
 
        range_start = day
        range_end = day + timedelta(days=1)
 
    range_start = _make_aware_if_needed(range_start)
    range_end = _make_aware_if_needed(range_end)
 
    filters &= Q(date__gte=range_start, date__lt=range_end)
 
    bills = list(Billing.objects.filter(filters))
 
    summary = _summarize(bills)
 
    trend_data = []
    if trend_days:
        bills_by_day = {}
        for bill in bills:
            if not bill.date:
                continue
            bill_local_date = timezone.localtime(bill.date).date() if settings.USE_TZ else bill.date.date()
            bills_by_day.setdefault(bill_local_date, []).append(bill)
 
        for day in trend_days:
            day_key = day.date()
            day_summary = _summarize(bills_by_day.get(day_key, []))
            trend_data.append({
                'date': day_key.strftime('%Y-%m-%d'),
                'totalPatients': day_summary['totalPatients'],
                'totalAmount': day_summary['totalAmount'],
                'totalTests': day_summary['totalTests'],
            })
 
    response_data = {
        **summary,
        'monthlyData': [],
        'trendData': trend_data,
    }
    return Response(response_data)
 


@api_view(['PUT'])
@permission_classes([HasRoleAndDataPermission])
def update_clinicalname(request):
    referrer_code = request.data.get('referrerCode')

    if not referrer_code:
        return Response({"error": "referrerCode is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        instance = ClinicalName.objects.filter(referrerCode=referrer_code).first()  # ✅ FIXED
        if not instance:
            return Response({"error": "Clinical record not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ClinicalNameSerializer(instance, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def serve_sales_image(request, file_id):
    try:
        # Use the global db connection defined earlier
        fs = gridfs.GridFS(db)
        if not ObjectId.is_valid(file_id):
             return HttpResponse(status=404)
        
        if not fs.exists(ObjectId(file_id)):
             return HttpResponse(status=404)

        grid_out = fs.get(ObjectId(file_id))
        
        response = HttpResponse(grid_out.read(), content_type=grid_out.content_type)
        response['Content-Disposition'] = f'inline; filename="{grid_out.filename}"'
        return response
    except Exception as e:
        return HttpResponse(status=500)
    




from ..models import (SalesPlan,recompute_entries_and_rollups,recompute_total_row,)

@api_view(['GET', 'POST', 'PATCH'])
@permission_classes([HasRoleAndDataPermission])
def salesplan(request):

    # ---------------- GET ----------------
    if request.method == 'GET':
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        category = request.query_params.get('category')

        filters = {}
        if month is not None:
            filters['month'] = int(month)
        if year is not None:
            filters['year'] = int(year)
        if category:
            filters['category'] = category

        queryset = SalesPlan.objects.filter(**filters).exclude(employee_id=TOTAL_ROW_EMPLOYEE_ID)
        serializer = SalesPlanSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ---------------- POST (bulk save whole grid for a category) ----------------
    if request.method == 'POST':
        data = request.data.copy()
        employee_id = data.get('auth-user-id')

        plans = data.get('plans', [])
        if not isinstance(plans, list):
            return Response({"error": "plans must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        results = []
        touched_combos = set()
        now = timezone.now()

        for plan_item in plans:
            plan_employee_id = plan_item.get('employee_id')
            plan_category = plan_item.get('category')
            plan_month = int(plan_item.get('month'))
            plan_year = int(plan_item.get('year'))
            plan_entries = plan_item.get('entries', [])
            plan_working_days = plan_item.get('working_days')
            plan_avg_revenue = plan_item.get('avg_revenue_per_prescription')

            existing = SalesPlan.objects.filter(
                employee_id=plan_employee_id,
                category=plan_category,
                month=plan_month,
                year=plan_year
            ).first()

            effective_avg_revenue = (
                float(plan_avg_revenue) if plan_avg_revenue is not None
                else float(existing.avg_revenue_per_prescription or 0) if existing else 0.0
            )

            if existing:
                existing_entries = existing.entries or []
                entry_map = {e['date']: e for e in existing_entries}
                for entry in plan_entries:
                    entry_map[entry['date']] = {'date': entry['date'], 'volume': entry.get('volume', 0)}
                merged_entries = list(entry_map.values())
            else:
                merged_entries = [{'date': e['date'], 'volume': e.get('volume', 0)} for e in plan_entries]

            normalized_entries, weekly_totals, total_revenue = recompute_entries_and_rollups(
                merged_entries, effective_avg_revenue, plan_year, plan_month
            )

            if existing:
                update_fields = {
                    'entries': normalized_entries,
                    'weekly_totals': weekly_totals,
                    'total_revenue': total_revenue,
                    'lastmodified_by': employee_id,
                    'lastmodified_date': now
                }
                if plan_working_days is not None:
                    update_fields['working_days'] = int(plan_working_days)
                if plan_avg_revenue is not None:
                    update_fields['avg_revenue_per_prescription'] = effective_avg_revenue

                SalesPlan.objects.filter(sales_plan_id=existing.sales_plan_id).update(**update_fields)
                updated = SalesPlan.objects.get(sales_plan_id=existing.sales_plan_id)
                results.append(SalesPlanSerializer(updated).data)
            else:
                serializer = SalesPlanSerializer(data={
                    'employee_id': plan_employee_id,
                    'category': plan_category,
                    'month': plan_month,
                    'year': plan_year,
                    'date': date_cls(plan_year, plan_month, 1),
                    'entries': normalized_entries,
                    'weekly_totals': weekly_totals,
                    'total_revenue': total_revenue,
                    'working_days': int(plan_working_days) if plan_working_days is not None else 0,
                    'avg_revenue_per_prescription': effective_avg_revenue,
                    'created_by': employee_id,
                    'lastmodified_by': employee_id,
                })
                if serializer.is_valid():
                    serializer.save()
                    results.append(serializer.data)
                else:
                    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            touched_combos.add((plan_category, plan_month, plan_year))

        for cat, mon, yr in touched_combos:
            recompute_total_row(cat, mon, yr, employee_id)

        return Response(results, status=status.HTTP_201_CREATED)

    # ---------------- PATCH (single cell edit, working-days edit, or avg-revenue edit, upsert) ----------------
    if request.method == 'PATCH':
        data = request.data.copy()
        employee_id = data.get('auth-user-id')

        sales_plan_id = data.get('sales_plan_id')
        emp_id = data.get('employee_id')
        category = data.get('category')
        month = int(data.get('month'))
        year = int(data.get('year'))

        day = data.get('day')
        working_days = data.get('working_days')
        avg_revenue = data.get('avg_revenue_per_prescription')

        if sales_plan_id:
            plan = SalesPlan.objects.filter(sales_plan_id=int(sales_plan_id)).first()
        else:
            plan = SalesPlan.objects.filter(
                employee_id=emp_id, category=category, month=month, year=year
            ).first()

        if not plan:
            raw_entries = []
            if day is not None:
                raw_entries = [{'date': int(day), 'volume': float(data.get('volume') or 0)}]

            effective_avg_revenue = float(avg_revenue) if avg_revenue is not None else 0.0
            normalized_entries, weekly_totals, total_revenue = recompute_entries_and_rollups(
                raw_entries, effective_avg_revenue, year, month
            )

            serializer = SalesPlanSerializer(data={
                'employee_id': emp_id,
                'category': category,
                'month': month,
                'year': year,
                'date': date_cls(year, month, 1),
                'entries': normalized_entries,
                'weekly_totals': weekly_totals,
                'total_revenue': total_revenue,
                'working_days': int(working_days) if working_days is not None else 0,
                'avg_revenue_per_prescription': effective_avg_revenue,
                'created_by': employee_id,
                'lastmodified_by': employee_id,
            })
            if serializer.is_valid():
                serializer.save()
                recompute_total_row(category, month, year, employee_id)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        update_fields = {
            'lastmodified_by': employee_id,
            'lastmodified_date': timezone.now()
        }

        entries = plan.entries or []
        if day is not None:
            day = int(day)
            volume = float(data.get('volume') or 0)
            found = False
            for entry in entries:
                if entry.get('date') == day:
                    entry['volume'] = volume
                    found = True
                    break
            if not found:
                entries.append({'date': day, 'volume': volume})

        effective_avg_revenue = (
            float(avg_revenue) if avg_revenue is not None
            else float(plan.avg_revenue_per_prescription or 0)
        )
        if avg_revenue is not None:
            update_fields['avg_revenue_per_prescription'] = effective_avg_revenue

        if working_days is not None:
            update_fields['working_days'] = int(working_days)

        if day is not None or avg_revenue is not None:
            normalized_entries, weekly_totals, total_revenue = recompute_entries_and_rollups(
                entries, effective_avg_revenue, year, month
            )
            update_fields['entries'] = normalized_entries
            update_fields['weekly_totals'] = weekly_totals
            update_fields['total_revenue'] = total_revenue

        SalesPlan.objects.filter(sales_plan_id=plan.sales_plan_id).update(**update_fields)
        recompute_total_row(category, month, year, employee_id)

        updated = SalesPlan.objects.get(sales_plan_id=plan.sales_plan_id)
        return Response(SalesPlanSerializer(updated).data, status=status.HTTP_200_OK)




# ─────────────────────────────────────────────
# Adjusted-days helper
# ─────────────────────────────────────────────
def get_adjusted_days(year, month, upto_day):
    """
    total_days         -> total calendar days in the month
    total_sundays      -> total Sundays in the month
    adjusted_days      -> elapsed days up to upto_day, with each Sunday counted as half a working day
    month_adjusted_days-> full month working days, with each Sunday counted as half a working day
    """
    total_days = calendar.monthrange(year, month)[1]
    cal = calendar.monthcalendar(year, month)

    total_sundays = 0
    upto_sundays = 0
    for week in cal:
        sunday = week[calendar.SUNDAY]
        if sunday != 0:
            total_sundays += 1
            if sunday <= upto_day:
                upto_sundays += 1

    adjusted_days = max(0.5, upto_day - (upto_sundays / 2.0))
    month_adjusted_days = total_days - (total_sundays / 2.0)

    return total_days, total_sundays, adjusted_days, month_adjusted_days
 
 
# ─────────────────────────────────────────────
# Calling your existing get_sales_executives/ view directly
# instead of duplicating its query.
#
# That view is wrapped in @api_view, which internally asserts
# its `request` arg is a raw django.http.HttpRequest — not the
# rest_framework.request.Request that salesplan_summary already
# has (that's what threw the earlier AssertionError). A DRF
# Request keeps the original Django request on `._request`, so
# passing THAT through satisfies the assertion and still carries
# the authenticated user/session along with it.
#
# The view itself returns a JsonResponse, not a Python list, so
# its .content needs to be parsed back into JSON here.
# ─────────────────────────────────────────────
def fetch_sales_executives(request):
    employees = _get_sales_executive_rows()
    if isinstance(employees, str):
        try:
            employees = json.loads(employees)
        except (TypeError, ValueError):
            employees = []
    if isinstance(employees, dict):
        employees = employees.get("data") or employees.get("employees") or []
    if not isinstance(employees, list):
        employees = []
    return employees
 
 
@api_view(['GET'])
def salesplan_summary(request):
    """
    Returns a category-wise summary table with:
      - Today  : the day BEFORE the selected date (yesterday relative to view date)
      - WTD    : Monday of that week up to (selected_date - 1), inclusive
      - MTD    : 1st of month up to (selected_date - 1), inclusive

    Query params:
      date  (YYYY-MM-DD) - the "as-of" date the user is viewing from.
                           Defaults to today (local). Data shown is always up to date-1.

    Response shape:
      {
        "as_of_date": "2026-08-03",
        "report_upto": "2026-08-02",   # date - 1
        "today_label": "2-Aug",
        "wtd_label": "Mon 28-Jul .. 2-Aug",
        "mtd_label": "1-Aug .. 2-Aug",
        "rows": [
          {
            "category": "B2B",
            "today_plan": 6000, "today_actual": 890, "today_pct": 14.8,
            "wtd_plan": 8000, "wtd_actual": 2000, "wtd_pct": 25.0,
            "mtd_plan": 8000, "mtd_actual": 2000, "mtd_pct": 25.0,
            "trending": 12345, "projection": 56789,
          },
          ...
          { "category": "Total", ... }
        ]
      }
    """
    date_str = request.GET.get("date")

    if date_str:
        try:
            as_of_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)
    else:
        as_of_date = timezone.localdate()

    # All "report" data is up to the day BEFORE the as-of date (i.e. yesterday relative to view)
    report_upto = as_of_date - timedelta(days=1)

    year = report_upto.year
    month = report_upto.month
    report_day = report_upto.day  # last day to include (inclusive)

    # ── Date range: MTD ────────────────────────────────────────────────────────
    mtd_start_dt = timezone.make_aware(datetime(year, month, 1, 0, 0, 0))
    mtd_end_dt   = timezone.make_aware(datetime(year, month, report_day, 23, 59, 59))

    # ── Date range: Today (yesterday) ─────────────────────────────────────────
    today_start_dt = timezone.make_aware(datetime(year, month, report_day, 0, 0, 0))
    today_end_dt   = mtd_end_dt  # same end

    # ── Date range: WTD ────────────────────────────────────────────────────────
    # Monday of the week containing report_upto, clipped to the 1st of the month
    monday = report_upto - timedelta(days=report_upto.weekday())  # weekday() Mon=0
    wtd_start_date = max(monday, report_upto.replace(day=1))
    wtd_start_dt = timezone.make_aware(datetime(wtd_start_date.year, wtd_start_date.month, wtd_start_date.day, 0, 0, 0))
    wtd_end_dt   = mtd_end_dt

    # ── Month projection helpers ────────────────────────────────────────────────
    total_days, sundays, adjusted_elapsed, month_adjusted_days = get_adjusted_days(
        year, month, report_day
    )

    # ── Pull all sales executives ──────────────────────────────────────────────
    employees = fetch_sales_executives(request)

    # ── Categories we care about ───────────────────────────────────────────────
    all_categories = list(
        SalesPlan.objects.exclude(category__isnull=True).exclude(category="")
        .values_list('category', flat=True).distinct()
    )
    if not all_categories:
        all_categories = ['B2B', 'Corporate Health Checkup', 'Home Collection', 'Franchise']

    def sum_plan_for_days(emp_id, category, day_numbers):
        """Sum SalesPlan entries[].revenue for the given calendar day numbers."""
        plans = SalesPlan.objects.filter(
            employee_id=emp_id, month=month, year=year, category=category
        )
        total = 0.0
        for plan in plans:
            for entry in (plan.entries or []):
                try:
                    d = int(entry.get('date'))
                    r = _to_float(entry.get('revenue'))
                except (TypeError, ValueError):
                    continue
                if d in day_numbers:
                    total += r
        return total

    def sum_actual_for_range(emp_name, category, start_dt, end_dt):
        """Sum Billing.netAmount for the given date range and category (segment)."""
        qs = Billing.objects.filter(
            salesMapping=emp_name,
            bill_date__gte=start_dt,
            bill_date__lte=end_dt,
            segment=category,
        )
        total = 0.0
        for bill in qs.only('netAmount'):
            total += _to_float(bill.netAmount)
        return total

    def sum_plan_total_revenue(emp_id, category):
        """Full month plan total revenue for trending/projection."""
        plans = SalesPlan.objects.filter(
            employee_id=emp_id, month=month, year=year, category=category
        )
        return sum(_to_float(p.total_revenue) for p in plans)

    # Day sets for plan lookups
    today_days = {report_day}
    wtd_days   = set(range(wtd_start_date.day, report_day + 1))
    mtd_days   = set(range(1, report_day + 1))

    # ── Accumulate per category ───────────────────────────────────────────────
    cat_data = {}  # { category: { today_plan, today_actual, wtd_*, mtd_*, full_plan, mtd_actual } }

    for cat in all_categories:
        cat_data[cat] = {
            'today_plan': 0.0, 'today_actual': 0.0,
            'wtd_plan':   0.0, 'wtd_actual':   0.0,
            'mtd_plan':   0.0, 'mtd_actual':   0.0,
            'full_plan':  0.0,  # total_revenue for whole month (for trending/projection)
        }

    for emp in employees:
        if not isinstance(emp, dict):
            continue
        emp_id   = emp.get("employeeId") or emp.get("employee_id")
        emp_name = emp.get("employeeName") or emp.get("employee_name")
        if not emp_id or not emp_name:
            continue

        for cat in all_categories:
            d = cat_data[cat]
            d['today_plan']  += sum_plan_for_days(emp_id, cat, today_days)
            d['wtd_plan']    += sum_plan_for_days(emp_id, cat, wtd_days)
            d['mtd_plan']    += sum_plan_for_days(emp_id, cat, mtd_days)
            d['full_plan']   += sum_plan_total_revenue(emp_id, cat)
            d['today_actual'] += sum_actual_for_range(emp_name, cat, today_start_dt, today_end_dt)
            d['wtd_actual']   += sum_actual_for_range(emp_name, cat, wtd_start_dt,   wtd_end_dt)
            d['mtd_actual']   += sum_actual_for_range(emp_name, cat, mtd_start_dt,   mtd_end_dt)

    # ── Build result rows ─────────────────────────────────────────────────────
    def pct(actual, plan):
        return round((actual / plan * 100), 1) if plan > 0 else 0.0

    def trending(mtd_actual, adjusted_elapsed, month_adjusted_days):
        """(actual so far / elapsed adjusted days) * total adjusted days in month."""
        if adjusted_elapsed > 0:
            return round((mtd_actual / adjusted_elapsed) * month_adjusted_days, 2)
        return 0.0

    def projection(trend, full_plan):
        return round((trend / full_plan * 100), 1) if full_plan > 0 else 0.0

    rows = []
    grand = {k: 0.0 for k in ['today_plan','today_actual','wtd_plan','wtd_actual',
                                'mtd_plan','mtd_actual','full_plan']}

    for cat in all_categories:
        d = cat_data[cat]
        trend = trending(d['mtd_actual'], adjusted_elapsed, month_adjusted_days)
        proj  = projection(trend, d['full_plan'])
        rows.append({
            'category':      cat,
            'today_plan':    round(d['today_plan'],   2),
            'today_actual':  round(d['today_actual'], 2),
            'today_pct':     pct(d['today_actual'], d['today_plan']),
            'wtd_plan':      round(d['wtd_plan'],   2),
            'wtd_actual':    round(d['wtd_actual'], 2),
            'wtd_pct':       pct(d['wtd_actual'], d['wtd_plan']),
            'mtd_plan':      round(d['mtd_plan'],   2),
            'mtd_actual':    round(d['mtd_actual'], 2),
            'mtd_pct':       pct(d['mtd_actual'], d['mtd_plan']),
            'trending':      trend,
            'projection':    proj,
        })
        for k in grand:
            grand[k] += d[k]

    # Grand Total row
    grand_trend = trending(grand['mtd_actual'], adjusted_elapsed, month_adjusted_days)
    grand_proj  = projection(grand_trend, grand['full_plan'])
    rows.append({
        'category':      'Total',
        'today_plan':    round(grand['today_plan'],   2),
        'today_actual':  round(grand['today_actual'], 2),
        'today_pct':     pct(grand['today_actual'], grand['today_plan']),
        'wtd_plan':      round(grand['wtd_plan'],   2),
        'wtd_actual':    round(grand['wtd_actual'], 2),
        'wtd_pct':       pct(grand['wtd_actual'], grand['wtd_plan']),
        'mtd_plan':      round(grand['mtd_plan'],   2),
        'mtd_actual':    round(grand['mtd_actual'], 2),
        'mtd_pct':       pct(grand['mtd_actual'], grand['mtd_plan']),
        'trending':      grand_trend,
        'projection':    grand_proj,
    })

    return Response({
        'as_of_date':   as_of_date.strftime("%Y-%m-%d"),
        'report_upto':  report_upto.strftime("%Y-%m-%d"),
        'today_label':  report_upto.strftime("%d-%b").lstrip("0"),
        'wtd_label':    f"{wtd_start_date.strftime('%d-%b').lstrip('0')} to {report_upto.strftime('%d-%b').lstrip('0')}",
        'mtd_label':    f"1-{report_upto.strftime('%b')} to {report_upto.strftime('%d-%b').lstrip('0')}",
        'rows': rows,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Overall Summary — per-category, per-salesperson (Volume/Plan/Actual/Diff)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
def overall_summary(request):
    """
    GET /overall_summary/?month=8&year=2026

    Returns per-category breakdown with each sales executive's:
      volume  – total planned volume (SalesPlan.entries[].volume)
      plan    – total planned revenue (SalesPlan.entries[].revenue)
      actual  – total billed (Billing.netAmount for that segment/month)
      diff    – plan - actual
      pct     – (actual / plan) * 100

    Response:
    {
      "month": 8, "year": 2026,
      "categories": [
        {
          "category": "B2B",
          "subtotal": { volume, plan, actual, diff, pct },
          "persons": [
            { employee_id, employee_name, volume, plan, actual, diff, pct },
            ...
          ]
        }, ...
      ],
      "grand_total": { volume, plan, actual, diff, pct }
    }
    """
    try:
        month = int(request.GET.get('month') or timezone.localdate().month)
        year  = int(request.GET.get('year')  or timezone.localdate().year)
    except (TypeError, ValueError):
        return Response({'error': 'month and year must be integers'}, status=400)

    # Full-month billing window
    month_start = timezone.make_aware(datetime(year, month, 1, 0, 0, 0))
    if month == 12:
        month_end = timezone.make_aware(datetime(year + 1, 1, 1, 0, 0, 0))
    else:
        month_end = timezone.make_aware(datetime(year, month + 1, 1, 0, 0, 0))

    # All categories with plan data this month
    all_categories = list(
        SalesPlan.objects.filter(month=month, year=year)
        .exclude(category__isnull=True).exclude(category='')
        .values_list('category', flat=True).distinct()
    )
    if not all_categories:
        all_categories = ['B2B', 'Corporate Health Checkup', 'Home Collection', 'Franchise']

    employees = fetch_sales_executives(request)

    grand_volume = 0
    grand_plan   = 0.0
    grand_actual = 0.0
    categories_data = []

    for cat in all_categories:
        cat_volume = 0
        cat_plan   = 0.0
        cat_actual = 0.0
        persons    = []

        for emp in employees:
            if not isinstance(emp, dict):
                continue
            emp_id   = emp.get('employeeId') or emp.get('employee_id')
            emp_name = emp.get('employeeName') or emp.get('employee_name')
            if not emp_id or not emp_name:
                continue

            # Plan from SalesPlan entries
            plans = SalesPlan.objects.filter(
                employee_id=emp_id, month=month, year=year, category=cat
            )
            volume = 0
            plan   = 0.0
            for p in plans:
                for entry in (p.entries or []):
                    try:
                        volume += int(entry.get('volume') or 0)
                        plan   += _to_float(entry.get('revenue'))
                    except (TypeError, ValueError):
                        continue

            # Actual from Billing
            actual = 0.0
            for bill in Billing.objects.filter(
                salesMapping=emp_name,
                bill_date__gte=month_start,
                bill_date__lt=month_end,
                segment=cat,
            ).only('netAmount'):
                actual += _to_float(bill.netAmount)

            if volume == 0 and plan == 0.0 and actual == 0.0:
                continue

            diff = plan - actual
            pct_val = round((actual / plan * 100), 1) if plan > 0 else 0.0

            persons.append({
                'employee_id':   emp_id,
                'employee_name': emp_name,
                'volume':        volume,
                'plan':          round(plan, 2),
                'actual':        round(actual, 2),
                'diff':          round(diff, 2),
                'pct':           pct_val,
            })
            cat_volume += volume
            cat_plan   += plan
            cat_actual += actual

        cat_diff = cat_plan - cat_actual
        cat_pct  = round((cat_actual / cat_plan * 100), 1) if cat_plan > 0 else 0.0

        categories_data.append({
            'category': cat,
            'subtotal': {
                'volume': cat_volume,
                'plan':   round(cat_plan, 2),
                'actual': round(cat_actual, 2),
                'diff':   round(cat_diff, 2),
                'pct':    cat_pct,
            },
            'persons': persons,
        })
        grand_volume += cat_volume
        grand_plan   += cat_plan
        grand_actual += cat_actual

    grand_diff = grand_plan - grand_actual
    grand_pct  = round((grand_actual / grand_plan * 100), 1) if grand_plan > 0 else 0.0

    return Response({
        'month': month,
        'year':  year,
        'categories': categories_data,
        'grand_total': {
            'volume': grand_volume,
            'plan':   round(grand_plan, 2),
            'actual': round(grand_actual, 2),
            'diff':   round(grand_diff, 2),
            'pct':    grand_pct,
        },
    })


@api_view(['GET', 'POST'])
@csrf_exempt
# @permission_classes([HasRoleAndDataPermission])
def salesplanreport(request):
    """
    Actual vs Plan report, one row per sales executive, for a given
    month/year (+ optional category filter).
 
    POST /salesplanreport/
        body: {
          "month": 6,
          "year": 2026,
          "category": "all" | "B2B" | ...,
          "employees": [ { "employeeId": "50886", "employeeName": "Chandra" }, ... ],
          "day": 15   // optional — see "day" below
        }
    GET  /salesplanreport/?month=6&year=2026&category=all&employees=<json-encoded list>
        (same shape as POST, for convenience/testing — employees must be a
        JSON-encoded string of the same array when passed as a query param)
 
    `day` (optional, 1-31): when supplied, the frontend's "Date wise"
    picker asks for a specific calendar date (year/month/day) rather than
    "today". Unlike week/month-to-date, this works for ANY month — past,
    current, or future — since the user explicitly picked the date. When
    omitted, day-level figures fall back to the old behaviour (today's
    date, only if month/year is the current calendar month).
 
    `employees` is required and is supplied by the caller (rather than
    looked up here) because Billing has no employee_id — it only stores
    the executive's display name in `salesMapping` — so matching Billing
    rows to a sales executive has to go through employeeName. The frontend
    already has the employeeId/employeeName list from get_sales_executives/,
    so it's passed straight through instead of duplicating that lookup here.
 
    Numbers returned per executive (unchanged from before):
      plan_day / actual_day / diff_day   — Day to Date (today only; null
                                            if the requested month/year
                                            isn't the current calendar
                                            month)
      plan_wtd / actual_wtd / diff_wtd   — Week to Date (same
                                            current-month-only restriction)
      plan_mtd / actual_mtd / diff_mtd   — Month To Date
 
    diff = plan - actual (positive = behind target / shortfall,
    negative = ahead of target / surplus).
 
    NOTE (fix): plan_* figures now sum entries[].volume, not
    entries[].amount — the SalesPlan documents never actually had an
    "amount" key (only "date", "volume", "revenue"), so this was
    previously always summing to 0.
 
    NEW — week-grid data, for the two stacked "Plan" / "Actual" tables
    (Week NN header groups, one column per calendar day of the month):
      `days`: [{ "day": 1, "week": 31, "weekday": "Sat", "label": "01-Aug" }, ...]
      per-employee `plan_by_day`:   { "1": 20.0, "2": 10.0, ... }  — sum of volume per day
      per-employee `actual_by_day`: { "1": 3, "2": 5, ... }        — COUNT of Billing rows per day
                                     (a headcount, matching Plan's volume unit — NOT a sum of netAmount)
 
    Also returns `categories`: the distinct SalesPlan.category values
    currently in use, for populating a filter dropdown.
    """
    try:
        params = request.data if request.method == 'POST' else request.query_params
 
        month = int(params.get('month'))
        year = int(params.get('year'))
    except (TypeError, ValueError):
        return Response(
            {'error': 'month and year are required and must be integers'},
            status=status.HTTP_400_BAD_REQUEST
        )
 
    category = params.get('category') or 'all'
 
    day_param = params.get('day')
    if day_param not in (None, ""):
        try:
            day_param = int(day_param)
        except (TypeError, ValueError):
            return Response(
                {'error': 'day must be an integer'},
                status=status.HTTP_400_BAD_REQUEST
            )
    else:
        day_param = None
 
    employees = params.get('employees')
    if isinstance(employees, str):
        try:
            employees = json.loads(employees)
        except (TypeError, ValueError):
            return Response(
                {'error': 'employees must be valid JSON when passed as a string'},
                status=status.HTTP_400_BAD_REQUEST
            )
    if not employees:
        return Response(
            {'error': 'employees is required — pass the list from get_sales_executives/'},
            status=status.HTTP_400_BAD_REQUEST
        )
 
    try:
        today = timezone.localdate()
        is_current_month = (year == today.year and month == today.month)
        days_in_month = calendar.monthrange(year, month)[1]
 
        # MTD day range: 1..today if this is the current month in progress,
        # otherwise 1..end-of-month (nothing left to compare "to date").
        mtd_end_day = today.day if is_current_month else days_in_month
 
        # WTD day range: only meaningful for the current month. Monday of
        # the current week, clipped to day 1 if that Monday actually falls
        # in the previous calendar month.
        wtd_start_day = None
        wtd_end_day = None
        if is_current_month:
            monday = today - timezone.timedelta(days=today.weekday())
            wtd_start_day = monday.day if monday.month == today.month else 1
            wtd_end_day = today.day
 
        # Day-wise target. If the caller passed an explicit "day" (the
        # frontend's Date wise picker always does), use it directly and
        # allow it for any month/year the picker resolved to. Otherwise
        # fall back to the old behaviour: today's day-of-month, but only
        # if the requested month/year is the current calendar month.
        if day_param is not None:
            if not (1 <= day_param <= days_in_month):
                return Response(
                    {'error': f'day must be between 1 and {days_in_month} for {month}/{year}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            day_target = day_param
        else:
            day_target = today.day if is_current_month else None
 
        month_start_dt, month_end_dt = _month_bounds(year, month)
 
        # ── Week-grid day metadata (Week NN header groups) ─────────────
        days = []
        for d in range(1, days_in_month + 1):
            dt = timezone.datetime(year, month, d).date()
            _, iso_week, _ = dt.isocalendar()
            days.append({
                'day': d,
                'week': iso_week,
                'weekday': dt.strftime('%a'),
                'label': dt.strftime('%d-%b'),
            })
 
        # Distinct categories currently used in SalesPlan, for the filter
        # dropdown on the frontend.
        categories = list(
            SalesPlan.objects.exclude(category__isnull=True)
            .exclude(category="")
            .values_list('category', flat=True)
            .distinct()
        )
 
        results = []
 
        for emp in employees:
            employee_id = emp.get('employeeId')
            employee_name = emp.get('employeeName')
            if not employee_id or not employee_name:
                continue
 
            # ── Plan side (SalesPlan.entries + weekly_totals) ──────────────────────────
            plan_qs = SalesPlan.objects.filter(
                employee_id=employee_id, month=month, year=year
            )
            if category != 'all':
                plan_qs = plan_qs.filter(category=category)

            plan_mtd = 0.0
            plan_wtd = 0.0
            plan_day = 0.0
            plan_by_day = {d['day']: 0.0 for d in days}
            # Accumulate plan weekly totals from SalesPlan.weekly_totals
            plan_weekly_totals = {}  # { iso_week_number: revenue }
            for plan in plan_qs:
                # Per-day revenue from entries
                for entry in (plan.entries or []):
                    try:
                        entry_day = int(entry.get('date'))
                        # Use revenue amount (not volume/headcount)
                        entry_revenue = _to_float(entry.get('revenue'))
                        entry_volume = _to_float(entry.get('volume'))
                    except (TypeError, ValueError, AttributeError):
                        continue
                    if entry_day <= mtd_end_day:
                        plan_mtd += entry_volume
                    if (
                        wtd_start_day is not None
                        and wtd_start_day <= entry_day <= wtd_end_day
                    ):
                        plan_wtd += entry_volume
                    if day_target is not None and entry_day == day_target:
                        plan_day += entry_volume
                    if entry_day in plan_by_day:
                        # Store revenue amount in the daily grid
                        plan_by_day[entry_day] += entry_revenue
                # Accumulate weekly totals from the pre-computed weekly_totals array
                for wt in (plan.weekly_totals or []):
                    try:
                        wk = int(wt.get('week'))
                        wk_total = _to_float(wt.get('total'))
                    except (TypeError, ValueError, AttributeError):
                        continue
                    plan_weekly_totals[wk] = plan_weekly_totals.get(wk, 0.0) + wk_total

            # ── Actual side (Billing — sum netAmount per day and per ISO week) ──────
            billing_qs = Billing.objects.filter(
                salesMapping=employee_name,
                bill_date__gte=month_start_dt,
                bill_date__lt=month_end_dt,
            )
            if category != 'all':
                billing_qs = billing_qs.filter(segment=category)

            actual_mtd = 0.0
            actual_wtd = 0.0
            actual_day = 0.0
            actual_by_day = {d['day']: 0.0 for d in days}
            actual_weekly_totals = {}  # { iso_week_number: revenue }
            for bill in billing_qs.only('bill_date', 'netAmount'):
                if not bill.bill_date:
                    continue
                bill_local_dt = timezone.localtime(bill.bill_date)
                bill_day = bill_local_dt.day
                amount = _to_float(bill.netAmount)
                if bill_day <= mtd_end_day:
                    actual_mtd += amount
                if (
                    wtd_start_day is not None
                    and wtd_start_day <= bill_day <= wtd_end_day
                ):
                    actual_wtd += amount
                if day_target is not None and bill_day == day_target:
                    actual_day += amount
                if bill_day in actual_by_day:
                    # Sum netAmount (revenue) per day instead of just counting bills
                    actual_by_day[bill_day] += amount
                # Aggregate into ISO week bucket
                _, bill_iso_week, _ = bill_local_dt.date().isocalendar()
                actual_weekly_totals[bill_iso_week] = (
                    actual_weekly_totals.get(bill_iso_week, 0.0) + amount
                )

            results.append({
                'employee_id': employee_id,
                'employee_name': employee_name,
                'plan_mtd': round(plan_mtd, 2),
                'actual_mtd': round(actual_mtd, 2),
                'diff_mtd': round(plan_mtd - actual_mtd, 2),
                'plan_wtd': round(plan_wtd, 2) if wtd_start_day is not None else None,
                'actual_wtd': round(actual_wtd, 2) if wtd_start_day is not None else None,
                'diff_wtd': (
                    round(plan_wtd - actual_wtd, 2) if wtd_start_day is not None else None
                ),
                'plan_day': round(plan_day, 2) if day_target is not None else None,
                'actual_day': round(actual_day, 2) if day_target is not None else None,
                'diff_day': (
                    round(plan_day - actual_day, 2) if day_target is not None else None
                ),
                # Daily grids: plan uses revenue (₹), actual uses sum of netAmount (₹)
                'plan_by_day': {str(k): round(v, 2) for k, v in plan_by_day.items()},
                'actual_by_day': {str(k): round(v, 2) for k, v in actual_by_day.items()},
                # Weekly totals: plan from SalesPlan.weekly_totals, actual computed from billing
                'plan_weekly_totals': {str(k): round(v, 2) for k, v in plan_weekly_totals.items()},
                'actual_weekly_totals': {str(k): round(v, 2) for k, v in actual_weekly_totals.items()},
            })
 
        return Response({
            'month': month,
            'year': year,
            'category': category,
            'is_current_month': is_current_month,
            'week_to_date_applicable': is_current_month,
            'day_to_date_applicable': day_target is not None,
            'categories': categories,
            'days': days,
            'results': results,
        }, status=status.HTTP_200_OK)
 
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
 