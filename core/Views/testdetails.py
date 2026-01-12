from rest_framework.response import Response
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from rest_framework import status
from urllib.parse import quote_plus
from pymongo import MongoClient
from django.views.decorators.csrf import csrf_exempt
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from django.utils import timezone
import re
from django.core.mail import EmailMessage
from django.conf import settings
from django.utils.timezone import make_aware
import pytz
from rest_framework.views import APIView
import traceback
import json
import certifi
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission

import os
from dotenv import load_dotenv
load_dotenv()

def normalize_parameters(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # ensure each value is a list (array of parameter objects)
        fixed = {}
        for k, v in value.items():
            if isinstance(v, list):
                fixed[str(k)] = v
            elif isinstance(v, str):
                try:
                    parsed = json.loads(v)
                    fixed[str(k)] = parsed if isinstance(parsed, list) else []
                except Exception:
                    fixed[str(k)] = []
            else:
                fixed[str(k)] = []
        return fixed
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (list, dict)):
                return parsed
        except Exception:
            return []
    return []

def normalize_device_ids(value):
    """
    Ensure device_id is always a list of strings.
    Accepts: list, comma-separated string, single string, None.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [d.strip() for d in value.split(",") if d.strip()]
    return []

def shape_parameters_for_storage(parameters, device_ids):
    """
    parameters: list or dict
    device_ids: list[str]
    Returns:
      - list when len(device_ids) == 1
      - dict mapping each device_id -> same parameters array when len(device_ids) > 1
    """
    devs = normalize_device_ids(device_ids)
    if not devs:
        # No device context; just return normalized parameters as-is
        return normalize_parameters(parameters)

    norm = normalize_parameters(parameters)

    # If client sent a list
    if isinstance(norm, list):
        if len(devs) == 1:
            return norm
        # multiple devices: duplicate the array for each device
        return {d: norm for d in devs}

    # If client sent a dict
    if isinstance(norm, dict):
        if len(devs) == 1:
            # prefer the single device's own array if present; otherwise use the first available array
            arr = norm.get(devs[0])
            if isinstance(arr, list):
                return arr
            first = next((v for v in norm.values() if isinstance(v, list)), [])
            return first
        # multiple devices: ensure each device has an array; if missing, duplicate the first array
        first_arr = next((v for v in norm.values() if isinstance(v, list)), [])
        shaped = {}
        for d in devs:
            shaped[d] = norm.get(d)
            if not isinstance(shaped[d], list):
                shaped[d] = first_arr
        return shaped

    # Fallback
    return []

# ---------------------------
# Devices - required endpoint
# ---------------------------

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def get_devices(request):
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_devicedata
        devices = list(collection.find({}, {"_id": 0, "device_id": 1, "department": 1}))
        return Response(devices, status=200)
    except Exception as e:
        return Response({"error": str(e)}, status=500)

# ------------------------------------------------
# Test Details (read/create/update parameters only)
# ------------------------------------------------


@api_view(['GET', 'POST', 'PATCH'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def get_test_details(request):
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_testdetails

        # -------------------- GET --------------------
        if request.method == 'GET':
            test_id = request.GET.get('test_id')
            status_val = request.GET.get('status', 'Approved')

            query_filter = {
                "is_active": True,
                "status": status_val
            }

            if test_id:
                try:
                    query_filter["test_id"] = int(test_id)
                except ValueError:
                    query_filter["test_id"] = test_id

            docs = list(collection.find(query_filter, {'_id': 0}))

            for doc in docs:
                doc['parameters'] = normalize_parameters(doc.get('parameters'))
                doc['device_id'] = normalize_device_ids(doc.get('device_id'))

            return JsonResponse({
                'success': bool(docs),
                'data': docs,
                'count': len(docs),
                'message': 'Success' if docs else 'No test details found'
            }, status=200)

        # -------------------- POST (CREATE) --------------------
        elif request.method == 'POST':
            try:
                data = request.data

                auth_user_id = request.headers.get('auth-user-id')
                auth_user_name = request.headers.get('auth-user-name')

                # Normalize device_id
                data['device_id'] = normalize_device_ids(data.get('device_id'))

                if 'parameters' in data and data.get('parameters') not in ("", None):
                    data['parameters'] = shape_parameters_for_storage(
                        data.get('parameters'), data['device_id']
                    )
                else:
                    data.pop('parameters', None)

                max_doc = collection.find_one(
                    {"test_id": {"$exists": True}},
                    sort=[("test_id", -1)],
                    projection={"test_id": 1, "_id": 0}
                )
                next_id = (max_doc.get('test_id', 0) if max_doc else 0) + 1
                data['test_id'] = next_id

                data.setdefault('is_active', True)
                data.setdefault('NABL', False)
                data.setdefault('status', 'Pending')

                # ✅ AUDIT FIELDS (CREATE)
                data['created_at'] = datetime.utcnow()
                data['created_by'] = auth_user_id
                data['created_by_name'] = auth_user_name

                data['last_modified_at'] = datetime.utcnow()
                data['last_modified_by'] = auth_user_id
                data['last_modified_by_name'] = auth_user_name

                collection.insert_one(data)

                return JsonResponse({
                    'success': True,
                    'message': 'Test details added successfully',
                    'test_id': next_id
                }, status=201)

            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
            except Exception as e:
                print("POST Error:", e)
                return JsonResponse({'success': False, 'error': 'Error while saving data'}, status=500)

        # -------------------- PATCH (UPDATE) --------------------
        elif request.method == 'PATCH':
            try:
                data = request.data

                auth_user_id = data.get('auth-user-id')
                auth_user_name = data.get('auth-user-name')

                test_id = data.get('test_id')
                test_name = data.get('test_name')
                updated_parameters = data.get('parameters')

                if updated_parameters is None:
                    return JsonResponse({'success': False, 'error': 'parameters are required'}, status=400)

                if test_id is not None:
                    query = {'test_id': test_id}
                elif test_name:
                    query = {'test_name': test_name}
                else:
                    return JsonResponse({'success': False, 'error': 'Provide test_id or test_name'}, status=400)

                existing = collection.find_one(query, {'device_id': 1, '_id': 0})

                existing_devices = normalize_device_ids(
                    existing.get('device_id') if existing else []
                )

                devices_from_payload = normalize_device_ids(data.get('device_id')) if 'device_id' in data else []
                device_ids = devices_from_payload or existing_devices

                shaped = shape_parameters_for_storage(updated_parameters, device_ids)

                update_fields = {
                    'parameters': shaped,

                    # ✅ AUDIT FIELDS (UPDATE ONLY)
                    'last_modified_at': datetime.utcnow(),
                    'last_modified_by': auth_user_id,
                    'last_modified_by_name': auth_user_name
                }

                if 'device_id' in data:
                    update_fields['device_id'] = device_ids

                result = collection.update_one(query, {'$set': update_fields})

                if result.matched_count > 0:
                    return JsonResponse({'success': True, 'message': 'Updated successfully'}, status=200)
                else:
                    return JsonResponse({'success': False, 'error': 'Test not found'}, status=404)

            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
            except Exception as e:
                print("PATCH Error:", e)
                return JsonResponse({'success': False, 'error': 'Update failed'}, status=500)

    except Exception as e:
        print("Main Error:", e)
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)

# --------------------------
# Approval Email
# --------------------------
# Fixed send_approval_email view
@csrf_exempt
@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def send_approval_email(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)

    try:
        # ✅ Handle both JSON body and form data
        if request.content_type == 'application/json':
            data = json.loads(request.body.decode('utf-8'))
        else:
            data = request.data
        
        test_id = data.get('test_id')
        recipient_email = data.get('recipient_email')

        # ✅ Validate test_id
        if not test_id:
            return JsonResponse({'success': False, 'error': 'test_id is required'}, status=400)

        # ✅ Convert test_id to integer
        try:
            test_id = int(test_id)
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'test_id must be a valid integer'}, status=400)

        # MongoDB connection
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_testdetails

        # ✅ Find test by integer test_id
        test = collection.find_one({'test_id': test_id})
        if not test:
            return JsonResponse({'success': False, 'error': f'Test not found with test_id: {test_id}'}, status=404)

        test_name = test.get('test_name', 'Unknown Test')

        # ✅ Build proper base URL
        base_url = request.build_absolute_uri('/').rstrip('/')

        approval_url = (
            f"{base_url}/_b_a_c_k_e_n_d/LIS/approve_test/?test_id={test_id}"
        )


        # Build test details (exclude _id and parameters for cleaner email)
        test_details_list = []
        for key, value in test.items():
            if key not in ['_id', 'parameters']:
                label = key.replace('_', ' ').title()
                test_details_list.append(f"{label}: {value}")
        
        test_details_str = "\n".join(test_details_list)

        subject = f"Approval Request: Test {test_name} (ID: {test_id})"

        html_message = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background: #f9f9f9; padding: 20px; border-radius: 8px; }}
                .header {{ background: linear-gradient(135deg, #667eea, #764ba2); color: white; padding: 20px; border-radius: 8px; text-align: center; }}
                .content {{ background: white; padding: 20px; margin-top: 20px; border-radius: 8px; }}
                .details {{ background: #f5f5f5; padding: 15px; border-radius: 4px; font-family: monospace; white-space: pre-wrap; }}
                .button {{ display: inline-block; padding: 12px 24px; background: #22c55e; color: white; text-decoration: none; border-radius: 6px; margin-top: 20px; font-weight: bold; }}
                .button:hover {{ background: #16a34a; }}
                .footer {{ text-align: center; margin-top: 20px; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>🔬 Diagnostics Test Approval Request</h2>
                </div>
                <div class="content">
                    <h3>Test Details:</h3>
                    <div class="details">{test_details_str}</div>
                    <p style="margin-top: 20px;">Please review and approve this test to make it active in the system.</p>
                    <div style="text-align: center;">
                        <a href="{approval_url}" class="button">✓ Approve Test</a>
                    </div>
                </div>
                <div class="footer">
                    <p>This is an automated email from the Diagnostics LIS system.</p>
                </div>
            </div>
        </body>
        </html>
        """

        plain_message = f"""
Diagnostics Test Approval Request

Test ID: {test_id}
Test Name: {test_name}

Test Details:
{test_details_str}

Please approve this test by clicking the link below:
{approval_url}

---
This is an automated email from the Diagnostics LIS system.
        """

        # Build recipient list
        recipient_list = []
        if recipient_email:
            recipient_list.append(recipient_email)
        
        # Add default admin email
        recipient_list.append('drprabusankar@smrft.org')
        
        # Remove duplicates
        recipient_list = list(set(recipient_list))

        # Send email using SMTP
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        import smtplib

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = settings.EMAIL_HOST_USER
        msg['To'] = ", ".join(recipient_list)

        msg.attach(MIMEText(plain_message, 'plain'))
        msg.attach(MIMEText(html_message, 'html'))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
        server.sendmail(settings.EMAIL_HOST_USER, recipient_list, msg.as_string())
        server.quit()

        return JsonResponse({
            'success': True, 
            'message': 'Approval email sent successfully',
            'test_id': test_id,
            'recipients': recipient_list
        }, status=200)

    except Exception as e:
        print(f"❌ Error in send_approval_email: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ✅ Fixed approve_test view
@csrf_exempt
@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def approve_test(request):
    try:
        test_id = request.GET.get('test_id')

        if not test_id:
            return HttpResponse("❌ Error: test_id is required", status=400)

        # Convert to integer
        try:
            test_id = int(test_id)
        except (ValueError, TypeError):
            return HttpResponse("❌ Error: test_id must be a valid integer", status=400)

        # MongoDB connection
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_testdetails

        # Update test status
        result = collection.update_one(
            {'test_id': test_id},
            {'$set': {'status': 'Approved'}}
        )

        if result.matched_count == 0:
            return HttpResponse("""
            <html>
                <body style="text-align:center;font-family:Arial;padding:50px">
                    <h2 style="color:#dc2626">❌ Test Not Found</h2>
                    <p>Test ID: {}</p>
                    <p>The test could not be found in the database.</p>
                </body>
            </html>
            """.format(test_id), content_type="text/html", status=404)

        if result.modified_count == 0:
            return HttpResponse("""
            <html>
                <body style="text-align:center;font-family:Arial;padding:50px">
                    <div style="max-width:600px;margin:0 auto">
                        <h2 style="color:#f59e0b">⚠️ Already Approved</h2>
                        <p>Test ID: {}</p>
                        <p>This test has already been approved.</p>
                        <button onclick="window.close()" 
                                style="margin-top:20px;padding:10px 20px;background:#667eea;color:white;border:none;border-radius:6px;cursor:pointer">
                            Close Window
                        </button>
                    </div>
                </body>
            </html>
            """.format(test_id), content_type="text/html")

        # Success response
        return HttpResponse("""
        <html>
            <head>
                <style>
                    body {{
                        text-align: center;
                        font-family: Arial, sans-serif;
                        padding: 50px;
                        background: linear-gradient(135deg, #667eea, #764ba2);
                    }}
                    .container {{
                        background: white;
                        max-width: 600px;
                        margin: 0 auto;
                        padding: 40px;
                        border-radius: 12px;
                        box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                    }}
                    .success-icon {{
                        font-size: 64px;
                        color: #22c55e;
                        margin-bottom: 20px;
                    }}
                    h2 {{
                        color: #22c55e;
                        margin-bottom: 10px;
                    }}
                    .test-id {{
                        color: #666;
                        font-size: 14px;
                        margin-bottom: 20px;
                    }}
                    .button {{
                        margin-top: 20px;
                        padding: 12px 24px;
                        background: #667eea;
                        color: white;
                        border: none;
                        border-radius: 6px;
                        cursor: pointer;
                        font-size: 16px;
                    }}
                    .button:hover {{
                        background: #5568d3;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="success-icon">✓</div>
                    <h2>Test Approved Successfully!</h2>
                    <p class="test-id">Test ID: {}</p>
                    <p>The test has been approved and is now active in the system.</p>
                    <button class="button" onclick="window.close()">Close Window</button>
                </div>
            </body>
        </html>
        """.format(test_id), content_type="text/html")

    except Exception as e:
        print(f"❌ Error in approve_test: {str(e)}")
        import traceback
        traceback.print_exc()
        return HttpResponse(f"❌ Error: {str(e)}", status=500)

# ---------------------------------------------
# Generic field update (add normalization)
# ---------------------------------------------

@api_view(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def handle_patch_request(request):
    print("DEBUG: Entered handle_patch_request")
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_testdetails

        data = {}
        try:
            print("DEBUG: Accessing request.data")
            data = request.data
            print("DEBUG: Accessed request.data success")
        except Exception as e:
            print(f"DEBUG: Failed to access request.data: {e}")
            # Fallback to body if possible?
            try:
                print("DEBUG: Trying fallback to json.loads(request.body)")
                data = json.loads(request.body.decode('utf-8'))
            except Exception as e2:
                print(f"DEBUG: Fallback failed: {e2}")
                return JsonResponse({'success': False, 'error': f'Body read error: {e}'}, status=500)

        test_id = data.get('test_id')
        test_name = data.get('test_name')

        print(f"DEBUG: test_id={test_id}, test_name={test_name}")

        # Build filter by test_id or test_name
        query = {}
        if test_id is not None:
            query = {'test_id': test_id}
        elif test_name:
            query = {'test_name': test_name}
        else:
            return JsonResponse({'error': 'Provide test_id or test_name'}, status=400)

        # Build update fields (exclude identifiers and protected fields)
        excluded_fields = [
            'test_name', 'test_id', '_id', 
            'auth-user-id', 'auth-user-name', 'auth-branch-code',
            'created_at', 'created_by', 'created_by_name',
            'last_modified_at', 'last_modified_by', 'last_modified_by_name'
        ]
        
        update_fields = {k: v for k, v in data.items() if k not in excluded_fields}

        # Handle audit fields
        auth_user_id = data.get('auth-user-id')
        auth_user_name = data.get('auth-user-name')
        
        update_fields['last_modified_at'] = datetime.utcnow()
        if auth_user_id:
            update_fields['last_modified_by'] = auth_user_id
        if auth_user_name:
            update_fields['last_modified_by_name'] = auth_user_name

        # Normalize device_id if provided
        if 'device_id' in update_fields:
            update_fields['device_id'] = normalize_device_ids(update_fields['device_id'])

        # Normalize parameters if provided
        if 'parameters' in update_fields:
            # fetch existing devices if not provided
            existing = collection.find_one(query, {'device_id': 1, '_id': 0})
            existing_devices = normalize_device_ids(existing.get('device_id') if existing else [])
            device_ids = update_fields.get('device_id', existing_devices)
            update_fields['parameters'] = shape_parameters_for_storage(update_fields['parameters'], device_ids)

        if update_fields:
            result = collection.update_one(query, {'$set': update_fields})
            if result.matched_count > 0:
                return JsonResponse({'success': True, 'message': 'Test details updated successfully'}, status=200)
            else:
                return JsonResponse({'success': False, 'error': 'Test not found'}, status=404)
        return JsonResponse({'success': False, 'message': 'No updates provided'}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        print("Error:", e)
        return JsonResponse({'success': False, 'error': 'An error occurred while updating data'}, status=500)

# -------------------------
# Get parameters by name
# -------------------------

@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_test_parameters(request, test_name):
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_testdetails

        test = collection.find_one({"test_name": test_name}, {"_id": 0, "parameters": 1})
        if test:
            return JsonResponse({"parameters": normalize_parameters(test.get("parameters", []))}, status=200)
        else:
            return JsonResponse({"error": "Test not found"}, status=404)
    except Exception as e:
        print("Error fetching parameters:", e)
        return JsonResponse({"error": "Failed to fetch parameters"}, status=500)














