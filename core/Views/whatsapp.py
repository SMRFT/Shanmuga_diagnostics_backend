import gridfs
from django.http import JsonResponse
from django.core.files.storage import default_storage
from pymongo import MongoClient
from bson.objectid import ObjectId
from django.views.decorators.csrf import csrf_exempt
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
#auth
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission

from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.core.mail import EmailMessage
from django.conf import settings
import os
from dotenv import load_dotenv
load_dotenv()
# MongoDB Connection
client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
db = client["Daignostics"]
fs = gridfs.GridFS(db)

@api_view(['POST'])
@csrf_exempt
# @permission_classes([HasRoleAndDataPermission])
def upload_pdf_to_gridfs(request):
    if request.method == "POST" and request.FILES.get("file"):
        file = request.FILES["file"]

        # 1. Validate type
        if file.content_type != "application/pdf":
            return JsonResponse({"error": "Only PDF files are allowed."}, status=400)

        # 2. Limit size (5MB)
        if file.size > 5 * 1024 * 1024:
            return JsonResponse({"error": "File too large (max 5 MB)."}, status=400)

        # 3. Sanitize filename
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_\.\-]', '_', file.name)

        # 4. Upload to GridFS
        file_id = fs.put(file, filename=safe_name)

        # 5. Generate access URL
        file_url = f"https://shinova.in/_b_a_c_k_e_n_d/LIS/get-file/{str(file_id)}"

        return JsonResponse({"file_id": str(file_id), "file_url": file_url})

    return JsonResponse({"error": "No file uploaded"}, status=400)

from django.http import HttpResponse
from bson import ObjectId

@api_view(['GET'])
@csrf_exempt
# @permission_classes([ HasRoleAndDataPermission])
def get_pdf_from_gridfs(request, file_id):
    try:
        file = fs.get(ObjectId(file_id))
        response = HttpResponse(file.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{file.filename}"'  # ← forces download
        return response
    except:
        return JsonResponse({"error": "File not found"}, status=404)






# views.py
import requests
from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(["POST"])
def send_whatsapp(request):
    try:
        patient_name = request.data.get("patient_name", "Valued Patient")
        phone = str(request.data.get("phone", "")).strip()
        collection_time = request.data.get("collection_time", "N/A")
        collected_date = request.data.get("collected_date", "N/A")
        file_url = request.data.get("file_url")
        pdf_name = request.data.get("pdf_name", "Report.pdf")

        if not phone or not file_url:
            return Response({"success": False, "error": "Missing phone or file URL"}, status=400)

        if not phone.startswith("91"):
            phone = f"91{phone}"

        params = {
            "LicenseNumber": "23638212604",
            "APIKey": "gENpYneQRuS7Vzq3dHoaB40lk",
            "Contact": phone,
            "Template": "diagnostics_report",
            "Param": f"{patient_name},{collection_time},{collected_date},{file_url}",
            "Fileurl": file_url,
            "PDFName": pdf_name,
        }

        botify_url = "https://admin.botify.in/api/sendtemplate.php"
        r = requests.get(botify_url, params=params, timeout=20)

        if r.status_code == 200 and "Success" in r.text:
            return Response({"success": True, "data": r.json() if "json" in r.headers.get("content-type", "") else r.text})
        return Response({"success": False, "error": r.text}, status=400)

    except Exception as e:
        return Response({"success": False, "error": str(e)}, status=500)



@csrf_exempt
def send_email(request):
    try:
        subject = request.POST.get('subject', 'No Subject')
        message = request.POST.get('message', 'No Message')
        recipient_list = request.POST.getlist('recipients') or ['shanmugainnovations@gmail.com']
        from_email = request.POST.get('from_email', settings.DEFAULT_FROM_EMAIL)
        signature = (
            "Contact Us,\nShanmuga Hospital,\n24, Saradha College Road,\n"
            "Salem-636007 Tamil Nadu,\n\n6369131631, 0427 270 6666,\n"
            "info@shanmugahospital.com,\nhttps://shanmugahospital.com/"
        )
        files = request.FILES.getlist('attachments')
        if not recipient_list:
            return JsonResponse({'status': 'error', 'message': 'At least one recipient is required to send the email.'}, status=400)
        email = EmailMessage(
            subject=subject,
            body=message + "\n\n" + signature,
            from_email=from_email,
            to=recipient_list,
        )
        for file in files:
            email.attach(file.name, file.read(), file.content_type)
        email.send()
        return JsonResponse({'status': 'success', 'message': 'Email sent successfully!'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
