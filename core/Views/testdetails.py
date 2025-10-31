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

            # Normalize output for frontend: parameters as arrays, device_id as list
            for doc in docs:
                doc['parameters'] = normalize_parameters(doc.get('parameters'))
                doc['device_id'] = normalize_device_ids(doc.get('device_id'))

            if docs:
                return JsonResponse({
                    'success': True,
                    'data': docs,
                    'count': len(docs)
                }, status=200)
            else:
                return JsonResponse({
                    'success': False,
                    'message': 'No test details found for the given criteria',
                    'data': []
                }, status=404)

        elif request.method == 'POST':
            try:
                data = json.loads(request.body.decode('utf-8'))

                # Normalize device_id to array
                data['device_id'] = normalize_device_ids(data.get('device_id'))

                # Shape parameters based on device count instead of forcing a list/empty
                if 'parameters' in data and data.get('parameters') not in ("", None):
                    data['parameters'] = shape_parameters_for_storage(data.get('parameters'), data['device_id'])
                else:
                    data.pop('parameters', None)

                # Generate incremental test_id = max(test_id) + 1
                max_doc = collection.find_one(
                    {"test_id": {"$exists": True}},
                    sort=[("test_id", -1)],
                    projection={"test_id": 1, "_id": 0}
                )
                next_id = (max_doc.get('test_id', 0) if max_doc else 0) + 1
                data['test_id'] = next_id

                # Defaults
                if 'is_active' not in data:
                    data['is_active'] = True
                if 'status' not in data:
                    data['status'] = 'Pending'

                # Insert
                collection.insert_one(data)
                return JsonResponse({'success': True, 'message': 'Test details added successfully', 'test_id': next_id}, status=201)

            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)
            except Exception as e:
                print("Error:", e)
                return JsonResponse({'success': False, 'error': 'An error occurred while saving data'}, status=500)

        elif request.method == 'PATCH':
            # Update parameters with correct shape (single device -> array, multiple -> dict)
            try:
                data = json.loads(request.body.decode('utf-8'))
                test_id = data.get('test_id')
                test_name = data.get('test_name')
                updated_parameters = data.get('parameters')

                if updated_parameters is None:
                    return JsonResponse({'success': False, 'error': 'parameters are required'}, status=400)

                # Build query
                if test_id is not None:
                    query = {'test_id': test_id}
                elif test_name:
                    query = {'test_name': test_name}
                else:
                    return JsonResponse({'success': False, 'error': 'Provide test_id or test_name'}, status=400)

                # Determine device_ids to use for shaping
                existing = collection.find_one(query, {'device_id': 1, '_id': 0})
                existing_devices = normalize_device_ids(existing.get('device_id') if existing else [])
                devices_from_payload = normalize_device_ids(data.get('device_id')) if 'device_id' in data else []
                device_ids = devices_from_payload or existing_devices

                shaped = shape_parameters_for_storage(updated_parameters, device_ids)

                update_fields = {'parameters': shaped}
                if 'device_id' in data:
                    update_fields['device_id'] = device_ids

                result = collection.update_one(query, {'$set': update_fields})
                if result.matched_count > 0:
                    return JsonResponse({'success': True, 'message': 'Parameters updated successfully'}, status=200)
                else:
                    return JsonResponse({'success': False, 'error': 'Test not found'}, status=404)

            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        print("Error:", e)
        return JsonResponse({'success': False, 'error': 'An error occurred'}, status=500)

# --------------------------
# Approval Email
# --------------------------
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def send_approval_email(request):
    if request.method == 'POST':
        try:
            print("Received approval email request")
            try:
                data = json.loads(request.body.decode('utf-8'))
                test_name = data.get('test_name')
                recipient_email = data.get('recipient_email')
                if not test_name:
                    return JsonResponse({'error': 'Test name is required'}, status=400)
            except json.JSONDecodeError as e:
                return JsonResponse({'error': 'Invalid JSON'}, status=400)

            try:
                client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
                db = client.Diagnostics
                collection = db.core_testdetails
                test = collection.find_one({'test_name': test_name})
                if not test:
                    return JsonResponse({'error': 'Test not found'}, status=404)
                if '_id' in test:
                    test['_id'] = str(test['_id'])
            except Exception as mongo_err:
                return JsonResponse({'error': f'Database error: {str(mongo_err)}'}, status=500)

            base_url = 'http://127.0.0.1:1071/'
            approval_url = f"{base_url}_b_a_c_k_e_n_d/LIS/approve_test/?test_name={test_name}"

            test_details_str = ""
            for key, value in test.items():
                if key != '_id' and key != 'parameters':
                    test_details_str += f"{key.replace('_', ' ').title()}: {value}\n"

            if 'parameters' in test:
                try:
                    params = normalize_parameters(test['parameters'])
                    if isinstance(params, list) and params:
                        test_details_str += "\nParameters:\n"
                        for i, p in enumerate(params, 1):
                            test_details_str += f"  Parameter {i}:\n"
                            for kk, vv in p.items():
                                if kk == 'value_option' and isinstance(vv, list):
                                    vv = ", ".join(vv)
                                test_details_str += f"    {kk.replace('_', ' ').title()}: {vv}\n"
                    elif isinstance(params, dict) and params:
                        test_details_str += "\nParameters (by device):\n"
                        for dev, arr in params.items():
                            test_details_str += f"  Device {dev}:\n"
                            if isinstance(arr, list):
                                for i, p in enumerate(arr, 1):
                                    test_details_str += f"    Parameter {i}:\n"
                                    for kk, vv in p.items():
                                        if kk == 'value_option' and isinstance(vv, list):
                                            vv = ", ".join(vv)
                                        test_details_str += f"      {kk.replace('_', ' ').title()}: {vv}\n"
                except Exception:
                    test_details_str += f"\nParameters: Not available\n"

            subject = f'Approval Request: Test {test_name}'
            html_message = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Test Approval Request</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; color: #333333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                    .header {{ background-color: #F5F5F5; padding: 10px; border-radius: 5px; margin-bottom: 20px; }}
                    .test-details {{ white-space: pre-line; margin-bottom: 20px; }}
                    .button {{ display: inline-block; padding: 10px 20px; background-color: #4CAF50; color: white;
                               text-decoration: none; border-radius: 5px; font-weight: bold; }}
                    .footer {{ font-size: 12px; color: #666; margin-top: 30px; border-top: 1px solid #ddd; padding-top: 10px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h2>Diagnostics Test Approval Request</h2>
                    </div>
                    <p>Hello,</p>
                    <p>A new Diagnostics test has been submitted and requires your approval. Here are the details:</p>
                    <div class="test-details">
                        {test_details_str}
                    </div>
                    <p>To approve this test, please click the button below:</p>
                    <p><a href="{approval_url}" class="button">Approve Test</a></p>
                    <div class="footer">
                        <p>This is an automated message from Shanmuga Diagnostics Laboratory System. If you did not request this approval, please ignore this email.</p>
                        <p>© 2025 Shanmuga Diagnostics. All rights reserved.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            plain_message = f"""
            Diagnostics Test Approval Request
            Hello,
            A new Diagnostics test has been submitted and requires your approval. Here are the details:
            {test_details_str}
            To approve this test, please click on the following link:
            {approval_url}
            This is an automated message from Shanmuga Diagnostics System. If you did not request this approval, please ignore this email.
            © 2025 Shanmuga Diagnostics. All rights reserved.
            """

            recipient_list = []
            if 'recipient_email' in locals() and recipient_email:
                recipient_list.append(recipient_email)

            default_emails = ['sivasundarismrft@gmail.com', 'sivasundari1024@gmail.com']
            for email in default_emails:
                if email not in recipient_list:
                    recipient_list.append(email)

            try:
                import smtplib
                from email.mime.multipart import MIMEMultipart
                from email.mime.text import MIMEText
                from email.utils import formatdate, make_msgid

                smtp_server = "smtp.gmail.com"
                smtp_port = 587
                smtp_username = settings.EMAIL_HOST_USER
                smtp_password = settings.EMAIL_HOST_PASSWORD

                msg = MIMEMultipart('alternative')
                msg['Subject'] = subject
                msg['From'] = f"Shanmuga Diagnostics<{smtp_username}>"
                msg['To'] = ", ".join(recipient_list)
                msg['Date'] = formatdate(localtime=True)
                msg['Message-ID'] = make_msgid(domain='shinovadatabase.in')

                part1 = MIMEText(plain_message, 'plain')
                part2 = MIMEText(html_message, 'html')
                msg.attach(part1)
                msg.attach(part2)

                server = smtplib.SMTP(smtp_server, smtp_port)
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_username, smtp_password)
                server.sendmail(smtp_username, recipient_list, msg.as_string())
                server.quit()
                return JsonResponse({'message': 'Approval email sent successfully'}, status=200)
            except Exception as email_err:
                return JsonResponse({'error': f'Email sending failed: {str(email_err)}'}, status=500)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Invalid request method'}, status=405)

# -----------------------
# Approval endpoint kept
# -----------------------

@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def approve_test(request):
    if request.method == 'PATCH':
        try:
            data = json.loads(request.body.decode('utf-8'))
            test_name = data.get('test_name')
            if not test_name:
                return JsonResponse({'error': 'Test name is required'}, status=400)

            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            collection = db.core_testdetails

            result = collection.update_one(
                {'test_name': test_name},
                {'$set': {'status': 'Approved'}}
            )
            if result.modified_count > 0:
                return JsonResponse({'message': 'Test approved successfully'}, status=200)
            else:
                return JsonResponse({'error': 'Test not found or already approved'}, status=404)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    elif request.method == 'GET':
        try:
            test_name = request.GET.get('test_name')
            if not test_name:
                return JsonResponse({'error': 'Test name is required'}, status=400)

            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            collection = db.core_testdetails

            result = collection.update_one(
                {'test_name': test_name},
                {'$set': {'status': 'Approved'}}
            )
            if result.modified_count > 0:
                html_response = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Test Approval Confirmation</title>
                    <style>
                        body { font-family: Arial, sans-serif; margin: 40px; text-align: center; background-color: #f5f5f5; }
                        .container { max-width: 600px; margin: 0 auto; padding: 30px; background-color: white;
                                      border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
                        .success { color: #4caf50; font-size: 28px; margin-bottom: 20px; }
                        .icon { font-size: 50px; color: #4caf50; margin-bottom: 20px; }
                        .button { display: inline-block; padding: 10px 20px; background-color: #4CAF50; color: white;
                                  text-decoration: none; border-radius: 5px; font-weight: bold; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="icon">✓</div>
                        <div class="success">Test Approved Successfully</div>
                        <p>The test has been approved and is now active in the system.</p>
                        <p>You can close this window.</p>
                    </div>
                </body>
                </html>
                """
                return HttpResponse(html_response, content_type='text/html')
            else:
                html_error = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Test Approval Error</title>
                    <style>
                        body { font-family: Arial, sans-serif; margin: 40px; text-align: center; background-color: #f5f5f5; }
                        .container { max-width: 600px; margin: 0 auto; padding: 30px; background-color: white;
                                      border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
                        .error { color: #f44336; font-size: 28px; margin-bottom: 20px; }
                        .icon { font-size: 50px; color: #f44336; margin-bottom: 20px; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="icon">✗</div>
                        <div class="error">Approval Failed</div>
                        <p>The test was not found or has already been approved.</p>
                        <p>Please contact the administrator for assistance.</p>
                    </div>
                </body>
                </html>
                """
                return HttpResponse(html_error, content_type='text/html', status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)

# ---------------------------------------------
# Generic field update (add normalization)
# ---------------------------------------------

@api_view(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def handle_patch_request(request):
    try:
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        collection = db.core_testdetails

        data = {}
        if request.body:
            data = json.loads(request.body.decode('utf-8'))

        test_id = data.get('test_id')
        test_name = data.get('test_name')

        # Build filter by test_id or test_name
        query = {}
        if test_id is not None:
            query = {'test_id': test_id}
        elif test_name:
            query = {'test_name': test_name}
        else:
            return JsonResponse({'error': 'Provide test_id or test_name'}, status=400)

        # Build update fields (exclude identifiers)
        update_fields = {k: v for k, v in data.items() if k not in ('test_name', 'test_id')}

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
