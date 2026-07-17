from django.http import JsonResponse, HttpResponse
from bson.objectid import ObjectId
import gridfs
import logging
from rest_framework.response import Response
from rest_framework import status
from ..models import SalesVisitLog,Billing,Patient,ClinicalName
from ..serializers import SalesVisitLogSerializer, HospitalLabSerializer,PatientSerializer,BillingSerializer,ClinicalNameSerializer
from django.db.models import Max
import re
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
import os
from core.pagination import paginate_queryset

logger = logging.getLogger(__name__)

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
                except Exception as e:
                    logger.debug(f"Falling back to default font: {e}")
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
from core.mongo_client import get_client
# Connect to MongoDB
client = get_client()
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
        page_obj, page_meta = paginate_queryset(combined, request)
        return Response(
            {"success": True, "data": list(page_obj), **page_meta},
            status=status.HTTP_200_OK
        )
    except Exception as e:
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


from django.http import JsonResponse
import os
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_sales_executives(request):
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = get_client()
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

    # NOTE: response shape changed from a bare JSON array to a paginated
    # object ({"data": [...], total_count, total_pages, current_page}) to
    # bound the payload as sales executives accumulate; update any frontend
    # caller that expected a raw array here.
    page_obj, page_meta = paginate_queryset(employees, request)
    return JsonResponse({"data": list(page_obj), **page_meta}, safe=False)


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

        page_obj, page_meta = paginate_queryset(sales_logs, request)
        serializer = SalesVisitLogSerializer(page_obj, many=True)
        # NOTE: response shape changed from a bare JSON array to a paginated
        # object ({"data": [...], total_count, total_pages, current_page}) to
        # bound the payload as sales visit logs accumulate; update any
        # frontend caller that expected a raw array here.
        return JsonResponse({"data": serializer.data, **page_meta}, safe=False)



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
    page_obj, page_meta = paginate_queryset(logs, request)
    serializer = SalesVisitLogSerializer(page_obj, many=True)
    # NOTE: response shape changed from a bare JSON array to a paginated
    # object ({"data": [...], total_count, total_pages, current_page}) to
    # bound the payload as sales visit logs accumulate; update any frontend
    # caller that expected a raw array here.
    return Response({"data": serializer.data, **page_meta}, status=status.HTTP_200_OK)



from datetime import datetime, timedelta
from django.http import JsonResponse
from django.utils.timezone import make_aware, is_naive

import json
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def salesdashboard(request):
    sales_mapping = request.GET.get("salesMapping")
    date_str = request.GET.get("date")
    month_str = request.GET.get("month")

    if not sales_mapping:
        return JsonResponse({"error": "Missing salesMapping parameter"}, status=400)

    try:
        if date_str:  # Daily filter
            start_date = datetime.strptime(date_str, "%Y-%m-%d")
            if timezone.is_naive(start_date):
                start_date = make_aware(start_date)
            end_date = start_date + timedelta(days=1)

        elif month_str:  # Monthly filter
            start_date = datetime.strptime(month_str, "%Y-%m")
            if timezone.is_naive(start_date):
                start_date = make_aware(start_date)
            # Calculate first day of next month
            next_month = (start_date.replace(day=1) + timedelta(days=32)).replace(day=1)
            if timezone.is_naive(next_month):
                next_month = make_aware(next_month)
            end_date = next_month

        else:
            return JsonResponse({"error": "Missing date or month parameter"}, status=400)

        # ✅ Filter billing data
        patients = Billing.objects.filter(
            salesMapping=sales_mapping,
            date__gte=start_date,
            date__lt=end_date
        )

        total_patients = patients.count()
        total_amount = sum(float(patient.totalAmount or 0) for patient in patients)

        test_counts = {}
        total_tests = 0

        for patient in patients:
            test_data = getattr(patient, "testdetails", "[]")
            if isinstance(test_data, str):
                try:
                    test_data = json.loads(test_data)
                except json.JSONDecodeError:
                    test_data = []
            if isinstance(test_data, list):
                for test in test_data:
                    test_name = test.get("testname", "Unknown")
                    test_counts[test_name] = test_counts.get(test_name, 0) + 1
                    total_tests += 1

        return JsonResponse({
            "totalPatients": total_patients,
            "totalAmount": total_amount,
            "totalTests": total_tests,
            "testCounts": test_counts
        })

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    





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
# @permission_classes([HasRoleAndDataPermission])
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