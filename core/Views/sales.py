from django.http import JsonResponse, HttpResponse
from bson.objectid import ObjectId
import gridfs
from rest_framework.response import Response
from rest_framework import status
from ..models import SalesVisitLog,Billing,Patient,ClinicalName,SalesPlan
from ..serializers import SalesVisitLogSerializer, HospitalLabSerializer,PatientSerializer,BillingSerializer,ClinicalNameSerializer,SalesPlanSerializer
from django.db.models import Max
import re
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
import os
from datetime import date as date_cls

@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def hospitallabform(request):
    if request.method == 'POST':
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
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_sales_executives(request):
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Global"]  

    collection = db["backend_diagnostics_profile"]

    # Query: Match either primaryRole == "SD-R-SP" or "SD-R-SP" in additionalRoles array
    query = {
        "$or": [
            {"primaryRole": "SD-R-SE"},
            {"additionalRoles": "SD-R-SE"},
             {"designation": "SD-R-SE"}
        ]
    }

    employees = list(collection.find(query, {"employeeName": 1, "employeeId": 1, "_id": 0}))

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
 
        queryset = SalesPlan.objects.filter(**filters)
        serializer = SalesPlanSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
 
    # ---------------- POST (bulk save whole grid for a category) ----------------
    if request.method == 'POST':
        data = request.data.copy()
        employee_id = data.get('auth-user-id')
 
        plans = data.get('plans', [])
        if not isinstance(plans, list):
            return Response(
                {"error": "plans must be a list"},
                status=status.HTTP_400_BAD_REQUEST
            )
 
        results = []
        now = timezone.now()
 
        for plan_item in plans:
            plan_employee_id = plan_item.get('employee_id')
            plan_category = plan_item.get('category')
            plan_month = int(plan_item.get('month'))
            plan_year = int(plan_item.get('year'))
            plan_entries = plan_item.get('entries', [])
 
            existing = SalesPlan.objects.filter(
                employee_id=plan_employee_id,
                category=plan_category,
                month=plan_month,
                year=plan_year
            ).first()
 
            if existing:
                existing_entries = existing.entries or []
                entry_map = {e['date']: e for e in existing_entries}
                for entry in plan_entries:
                    entry_map[entry['date']] = entry
                merged_entries = list(entry_map.values())
 
                SalesPlan.objects.filter(sales_plan_id=existing.sales_plan_id).update(
                    entries=merged_entries,
                    lastmodified_by=employee_id,
                    lastmodified_date=now
                )
                updated = SalesPlan.objects.get(sales_plan_id=existing.sales_plan_id)
                results.append(SalesPlanSerializer(updated).data)
            else:
                serializer = SalesPlanSerializer(data={
                    'employee_id': plan_employee_id,
                    'category': plan_category,
                    'month': plan_month,
                    'year': plan_year,
                    'date': date_cls(plan_year, plan_month, 1),
                    'entries': plan_entries,
                    'created_by': employee_id,
                    'lastmodified_by': employee_id,
                })
                if serializer.is_valid():
                    serializer.save()
                    results.append(serializer.data)
                else:
                    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
 
        return Response(results, status=status.HTTP_201_CREATED)
 
    # ---------------- PATCH (single cell edit, upsert) ----------------
    if request.method == 'PATCH':
        data = request.data.copy()
        employee_id = data.get('auth-user-id')
 
        sales_plan_id = data.get('sales_plan_id')
        emp_id = data.get('employee_id')
        category = data.get('category')
        month = int(data.get('month'))
        year = int(data.get('year'))
        day = int(data.get('day'))
        amount = data.get('amount')
 
        if sales_plan_id:
            # Known record — go straight to it, skip the compound lookup.
            plan = SalesPlan.objects.filter(sales_plan_id=int(sales_plan_id)).first()
        else:
            plan = SalesPlan.objects.filter(
                employee_id=emp_id,
                category=category,
                month=month,
                year=year
            ).first()
 
        if not plan:
            serializer = SalesPlanSerializer(data={
                'employee_id': emp_id,
                'category': category,
                'month': month,
                'year': year,
                'date': date_cls(year, month, 1),
                'entries': [{'date': day, 'amount': amount}],
                'created_by': employee_id,
                'lastmodified_by': employee_id,
            })
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
 
        entries = plan.entries or []
        found = False
        for entry in entries:
            if entry.get('date') == day:
                entry['amount'] = amount
                found = True
                break
        if not found:
            entries.append({'date': day, 'amount': amount})
 
        SalesPlan.objects.filter(sales_plan_id=plan.sales_plan_id).update(
            entries=entries,
            lastmodified_by=employee_id,
            lastmodified_date=timezone.now()
        )
 
        updated = SalesPlan.objects.get(sales_plan_id=plan.sales_plan_id)
        return Response(SalesPlanSerializer(updated).data, status=status.HTTP_200_OK)
 