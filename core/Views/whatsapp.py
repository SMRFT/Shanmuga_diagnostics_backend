import gridfs
from django.http import JsonResponse
from django.core.files.storage import default_storage
from pymongo import MongoClient
from bson.objectid import ObjectId
from django.views.decorators.csrf import csrf_exempt
import json
import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
#auth
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from pyauth.auth import HasRoleAndDataPermission

from ..models import CommunicationLog
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.core.mail import EmailMessage
from django.conf import settings
import os
from dotenv import load_dotenv
load_dotenv()
# MongoDB Connection
client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
db = client["Diagnostics"]
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
        file_url = f"https://test.shinova.in/_b_a_c_k_e_n_d/LIS/get-file/{str(file_id)}"

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



from core.models import CommunicationLog



@api_view(["POST"])
def send_whatsapp(request):
    try:
        patient_name = request.data.get("patient_name", "Valued Patient")
        phone = str(request.data.get("phone", "")).strip()
        collection_time = request.data.get("collection_time", "N/A")
        collected_date = request.data.get("collected_date", "N/A")
        file_url = request.data.get("file_url")
        pdf_name = request.data.get("pdf_name", "Report.pdf")
        patient_id = request.data.get("patient_id", "")  # Get patient_id

        if not phone or not file_url:
            return Response({"success": False, "error": "Missing phone or file URL"}, status=400)

        if not phone.startswith("91"):
            phone = f"91{phone}"

        # Prepare params list for the template
        # Order: 1=Name, 2=Time, 3=Date, 4=Link
        template_params_list = [
            patient_name,
            collection_time,
            collected_date,
            file_url
        ]
        # Join with commas. Note: ensure no commas in values or handle them? 
        # API likely expects simple CSV.
        template_params = ",".join([str(p) for p in template_params_list])

        template_name = request.data.get("template_name", "diagnostics_report_main")

        params = {
            "apikey": "ccbb8c923474d5b9d605b391f545a5688fbd54e0cad69d17",
            "contact": phone,
            "template": template_name,
            "params": template_params,
        }

        # print("Sending params:", params)

        botify_url = "https://dashboard.botify.in/api/v1/external/sendtemplatemessage"
        r = requests.get(botify_url, params=params, timeout=20)

        try:
            response_json = r.json()
            is_success = r.status_code == 200 and response_json.get("success") is True
        except ValueError:
            response_json = {}
            is_success = False

        status = "Success" if is_success else "Failed"
        
        # Log communication
        CommunicationLog.objects.create(
            patient_id=patient_id,
            patient_name=patient_name,
            type="WhatsApp",
            recipient=phone,
            status=status,
            details=r.text
        )

        if status == "Success":
            return Response({"success": True, "data": response_json})
        return Response({"success": False, "error": r.text}, status=400)

    except Exception as e:
        status = "Failed"
        details = str(e)
        # Log failure
        CommunicationLog.objects.create(
            patient_id=request.data.get("patient_id", ""),
            patient_name=request.data.get("patient_name", ""),
            type="WhatsApp",
            recipient=str(request.data.get("phone", "")),
            status=status,
            details=details
        )
        return Response({"success": False, "error": str(e)}, status=500)


@csrf_exempt
def send_email(request):
    recipient_list = request.POST.getlist('recipients') or ['shanmugainnovations@gmail.com']
    try:
        subject = request.POST.get('subject', 'No Subject')
        message = request.POST.get('message', 'No Message')
        from_email = request.POST.get('from_email', settings.DEFAULT_FROM_EMAIL)
        patient_id = request.POST.get('patient_id', '') # Get patient_id
        patient_name = request.POST.get('patient_name', '') # Get patient_name

        # Create HTML Content
        html_message = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #334155;
                    margin: 0;
                    padding: 0;
                    background-color: #f1f5f9;
                }}
                .email-container {{
                    max-width: 600px;
                    margin: 20px auto;
                    background-color: #ffffff;
                    border-radius: 12px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
                }}
                .header {{
                    background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
                    padding: 30px 20px;
                    text-align: center;
                }}
                .header h1 {{
                    color: white;
                    margin: 0;
                    font-size: 24px;
                    font-weight: 700;
                    letter-spacing: 0.5px;
                }}
                .content {{
                    padding: 40px 30px;
                }}
                .greeting {{
                    font-size: 18px;
                    font-weight: 600;
                    color: #1e293b;
                    margin-bottom: 20px;
                }}
                .message-body {{
                    font-size: 16px;
                    color: #475569;
                    margin-bottom: 30px;
                }}
                .cta-box {{
                    background-color: #f8fafc;
                    border-left: 4px solid #4f46e5;
                    padding: 20px;
                    margin: 20px 0;
                    border-radius: 4px;
                }}
                .footer {{
                    background-color: #f8fafc;
                    padding: 30px;
                    text-align: center;
                    border-top: 1px solid #e2e8f0;
                }}
                .footer-text {{
                    font-size: 12px;
                    color: #94a3b8;
                    margin: 5px 0;
                }}
                .contact-info {{
                    margin-top: 15px;
                    font-size: 13px;
                    color: #64748b;
                    font-weight: 500;
                }}
                a {{
                    color: #4f46e5;
                    text-decoration: none;
                }}
            </style>
        </head>
        <body>
            <div class="email-container">
                <div class="header">
                    <h1>Shanmuga Diagnostics</h1>
                </div>
                <div class="content">
                    <div class="greeting">Hello,</div>
                    <div class="message-body">
                        {message.replace(chr(10), '<br>')}
                    </div>
                </div>
                <div class="footer">
                    <div class="contact-info">
                        Shanmuga Hospital<br>
                        24, Saradha College Road, Salem-636007, Tamil Nadu
                    </div>
                    <div class="contact-info" style="margin-top: 10px;">
                        <a href="tel:6369131631">6369131631</a> | <a href="tel:04272706666">0427 270 6666</a><br>
                        <a href="mailto:info@shanmugahospital.com">info@shanmugahospital.com</a>
                    </div>
                    <div class="contact-info" style="margin-top: 10px;">
                         <a href="https://shanmugahospital.com/">www.shanmugahospital.com</a>
                    </div>
                    <div class="footer-text" style="margin-top: 20px;">
                        &copy; {datetime.datetime.now().year} Shanmuga Diagnostics. All rights reserved.
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

        if not recipient_list:
            return JsonResponse({'status': 'error', 'message': 'At least one recipient is required to send the email.'}, status=400)
            
        files = request.FILES.getlist('attachments') # Restore files definition
        
        email = EmailMessage(
            subject=subject,
            body=html_message,
            from_email=from_email,
            to=recipient_list,
        )
        email.content_subtype = "html"  # Main content is now text/html
        for file in files:
            email.attach(file.name, file.read(), file.content_type)
        email.send()
        
        # Log success
        CommunicationLog.objects.create(
            patient_id=patient_id,
            patient_name=patient_name,
            type="Email",
            recipient=", ".join(recipient_list),
            status="Success",
            details="Email sent successfully"
        )
        
        return JsonResponse({'status': 'success', 'message': 'Email sent successfully!'})
    except Exception as e:
        # Log failure
        CommunicationLog.objects.create(
            patient_id=request.POST.get('patient_id', ''),
            patient_name=request.POST.get('patient_name', ''),
            type="Email",
            recipient=", ".join(recipient_list),
            status="Failed",
            details=str(e)
        )

from django.utils.dateparse import parse_date
import datetime

@api_view(['GET', 'POST'])
@csrf_exempt
# @permission_classes([HasRoleAndDataPermission])
def get_communication_logs(request):
    print(f"DEBUG: get_communication_logs called with method {request.method}")
    try:
        if request.method == 'POST':
            from_date_str = request.data.get('from_date')
            to_date_str = request.data.get('to_date')
        else:
            from_date_str = request.GET.get('from_date')
            to_date_str = request.GET.get('to_date')
        
        filter_kwargs = {}
        if from_date_str:
            d = parse_date(from_date_str)
            if d:
                filter_kwargs['created_date__gte'] = datetime.datetime.combine(d, datetime.time.min)
        if to_date_str:
            d = parse_date(to_date_str)
            if d:
                filter_kwargs['created_date__lte'] = datetime.datetime.combine(d, datetime.time.max)
            
        logs = CommunicationLog.objects.filter(**filter_kwargs).order_by('-created_date')
            
        data = [
            {
                "id": log.id,
                "date": log.created_date,
                "patientId": log.patient_id,
                "patientName": log.patient_name,
                "type": log.type,
                "recipient": log.recipient,
                "status": log.status,
                "details": log.details
            } for log in logs
        ]
            
        return JsonResponse({"success": True, "data": data})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)
