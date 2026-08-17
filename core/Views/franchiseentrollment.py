# core/Views/franchiseentrollment.py

import os
import re
import uuid
from decimal import Decimal
from datetime import datetime, timedelta

from django.db import models
from djongo import models as djongo_models
from bson import ObjectId
from rest_framework import serializers

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.parsers import MultiPartParser
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.response import Response
from pyauth.auth import HasRolePermission
from rest_framework.permissions import AllowAny
from rest_framework import status
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.contrib.auth.hashers import make_password
from django.shortcuts import render, redirect
from django.contrib import messages
from django.urls import reverse
from pymongo import MongoClient
import gridfs
import json
from bson.json_util import dumps
from dotenv import load_dotenv
from django.utils.dateparse import parse_date
from django.utils import timezone



from ..models import Franchise, barcodestock, FranchiseLocation
from ..serializers import BarcodestockSerializer, FranchiseLocationSerializer
from .dbcollection import cluster_collection, location_collection




class Wallet(models.Model):
    wallet_id = models.CharField(primary_key=True, max_length=50, default=lambda: str(ObjectId()))
    franchise = models.OneToOneField(
        'Franchise',
        on_delete=models.CASCADE,
        related_name='wallet'
    )
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default='INR')
    status = models.CharField(
        max_length=20,
        choices=[('active', 'Active'), ('inactive', 'Inactive')],
        default='active'
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet for {self.franchise.franchise_name} - ₹{self.balance}"




class Payments(models.Model):
    TRANSACTION_TYPES = [
        ('initial', 'Initial Payment'),
        ('topup', 'Top Up'),
    ]
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('pending', 'Pending'),
    ]
    transaction_id = models.CharField(max_length=100, primary_key=True)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    currency_id = models.CharField(max_length=100, default='INR')
    payment_amount = models.DecimalField(max_digits=12, decimal_places=2)
    wallet_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    user_notes = models.TextField(blank=True)
    system_notes = models.TextField(blank=True)
    payment_gateway_id = models.IntegerField(default=2)
    payment_gateway_ref_id = models.CharField(max_length=200, blank=True)
    payment_gateway_txn_id = models.CharField(max_length=200, blank=True)
    payment_gateway_status = models.CharField(max_length=50, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.transaction_id} - ₹{self.payment_amount}"

    class Meta:
        ordering = ['-created']





# =============================================================================
# Serializers
# =============================================================================

class ObjectIdField(serializers.Field):
    def to_representation(self, value):
        return str(value)
    def to_internal_value(self, data):    
        return ObjectId(data)


class FranchiseSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)

    class Meta:
        model = Franchise
        fields = '__all__'

    def create(self, validated_data):
        franchise = super().create(validated_data)
        wallet = Wallet.objects.create(
            franchise_id=franchise.franchise_id,
            balance=Decimal('2500.00'),
            currency='INR'
        )
        Payments.objects.create(
            transaction_id=str(uuid.uuid4()),
            wallet=wallet,
            transaction_type='initial',
            payment_amount=Decimal('2500.00'),
            wallet_amount=Decimal('2500.00'),
            status='success',
            user_notes='Initial funding',
            system_notes='Auto-created during franchise registration'
        )
        return franchise










# =============================================================================
# Views
# =============================================================================

@csrf_exempt
@api_view(['POST'])
@permission_classes([HasRolePermission])
@parser_classes([MultiPartParser])
def register_franchise(request):
    raw_data = request.data
    data = {}
    for key, value in raw_data.items():
        # Exclude file stream objects from serializer payload
        if not hasattr(value, 'read') and not hasattr(value, 'file') and not hasattr(value, 'chunks'):
            data[key] = value

    print("Received cleaned data:", data)

    aadhaar_file = request.FILES.get('aadhaar_proof')
    pan_file = request.FILES.get('pan_proof') or request.FILES.get('pan_card_proof') or request.FILES.get('pan_file')
    payment_file = request.FILES.get('payment_proof')
    agreement_file = request.FILES.get('agreement_proof')
    franchise_photo_file = request.FILES.get('franchise_photo')
    user_id = data.get('auth-user-id')
    print("User ID from request:", user_id)

    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Diagnostics"]
    fs = gridfs.GridFS(db)

    try:
        user_id = data.get('auth-user-id')
        if aadhaar_file:
            file_id = fs.put(aadhaar_file.read(), filename=aadhaar_file.name, content_type=aadhaar_file.content_type)
            data['aadhaar_file_id'] = str(file_id)

        if pan_file:
            file_id = fs.put(pan_file.read(), filename=pan_file.name, content_type=pan_file.content_type)
            data['pan_file_id'] = str(file_id)

        if payment_file:
            file_id = fs.put(payment_file.read(), filename=payment_file.name, content_type=payment_file.content_type)
            data['payment_file_id'] = str(file_id)

        if agreement_file:
            file_id = fs.put(agreement_file.read(), filename=agreement_file.name, content_type=agreement_file.content_type)
            data['agreement_file_id'] = str(file_id)

        if franchise_photo_file:
            file_id = fs.put(franchise_photo_file.read(), filename=franchise_photo_file.name, content_type=franchise_photo_file.content_type)
            data['franchise_photo_file_id'] = str(file_id)

        # Remove non-model helper keys
        file_keys = [
            'aadhaar_proof', 'pan_proof', 'pan_card_proof', 'pan_file',
            'payment_proof', 'agreement_proof', 'franchise_photo',
            'aadhaar_file', 'pan_card', 'payment_file', 'agreement_file'
        ]
        for k in file_keys:
            data.pop(k, None)

        if 'location' in data and not data.get('location_id'):
            data['location_id'] = data.get('location')
        data.pop('location', None)

        data['created_by'] = data.get('created_by', user_id)
        data['lastmodified_by'] = data.get('lastmodified_by', user_id)

        serializer = FranchiseSerializer(data=data)
        if serializer.is_valid():
            franchise_data = serializer.save()
            franchise_id = data.get('franchise_id')

            if not franchise_id:
                return Response({
                    'error': 'Franchise ID is required for registration'
                }, status=status.HTTP_400_BAD_REQUEST)

            dummy_password = str(uuid.uuid4())[:8]
            hashed_password = make_password(dummy_password)
            reset_token = str(uuid.uuid4())

            franchise_user_collection = db["franchise_user"]
            franchise_user_data = {
                "franchise_id": franchise_id,
                "password": hashed_password,
                "reset_password": True,
                "reset_token": reset_token,
                "reset_token_expires": datetime.utcnow() + timedelta(hours=72),
                "created_date": datetime.utcnow(),
                "created_by": user_id,
                "lastmodified_by": user_id,
                "lastmodified_date": datetime.utcnow()
            }

            franchise_user_collection.insert_one(franchise_user_data)

            send_franchise_welcome_email(
                email=data.get('email'),
                franchise_id=franchise_id,
                reset_token=reset_token,
                franchise_name=data.get('franchiser_name', 'Franchise Partner')
            )

            return Response({
                'message': 'Franchise registered successfully. Password reset email sent.',
                'franchise_id': franchise_id
            }, status=status.HTTP_201_CREATED)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        return Response({
            'error': f'Registration failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        client.close()


def send_franchise_welcome_email(email, franchise_id, reset_token, franchise_name):
    try:
        reset_link = f"{os.getenv('FRONTEND_URL', 'http://127.0.0.1:8000')}/franchise/reset-password?token={reset_token}&franchise_id={franchise_id}"
        context = {
            'franchise_name': franchise_name,
            'franchise_id': franchise_id,
            'reset_link': reset_link,
            'expiry_hours': 72
        }
        html_message = render_to_string('email/franchise_welcome.html', context)
        plain_message = strip_tags(html_message)
        send_mail(
            subject='Welcome to Shanmuga Franchise - Set Your Password',
            message=plain_message,
            from_email=os.getenv('EMAIL_FROM', 'noreply@shanmugafranchise.com'),
            recipient_list=[email],
            html_message=html_message,
            fail_silently=False,
        )
        print(f"Welcome email sent successfully to {email}")
    except Exception as e:
        print(f"Failed to send email to {email}: {str(e)}")


@permission_classes([HasRolePermission])
def reset_password_form(request):
    token = request.GET.get('token')
    franchise_id = request.GET.get('franchise_id')
    
    if not token or not franchise_id:
        return render(request, 'email/error.html', {
            'error': 'Invalid reset link. Token and franchise ID are required.'
        })
    
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Diagnostics"]
    franchise_user_collection = db["franchise_user"]
    
    try:
        user = franchise_user_collection.find_one({
            "franchise_id": franchise_id,
            "reset_token": token,
            "reset_token_expires": {"$gt": datetime.utcnow()}
        })
        if not user:
            # Fallback to franchise db
            try:
                user = client["franchise"]["franchise_user"].find_one({
                    "franchise_id": franchise_id,
                    "reset_token": token,
                    "reset_token_expires": {"$gt": datetime.utcnow()}
                })
            except Exception:
                pass
        
        if not user:
            return render(request, 'email/error.html', {
                'error': 'Invalid or expired reset token. Please request a new password reset link.'
            })
        
        context = {
            'token': token,
            'franchise_id': franchise_id,
            'email': user.get('email', '')
        }
        return render(request, 'email/reset_password.html', context)
    except Exception as e:
        return render(request, 'email/error.html', {
            'error': f'An error occurred: {str(e)}'
        })
    finally:
        client.close()


@csrf_exempt
@permission_classes([HasRolePermission])
def reset_franchise_password(request):
    if request.method == 'POST':
        token = request.POST.get('token')
        franchise_id = request.POST.get('franchise_id')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        if not all([token, franchise_id, new_password, confirm_password]):
            messages.error(request, 'All fields are required')
            return redirect(f'/franchise/reset-password?token={token}&franchise_id={franchise_id}')
        
        if new_password != confirm_password:
            messages.error(request, 'Passwords do not match')
            return redirect(f'/franchise/reset-password?token={token}&franchise_id={franchise_id}')
        
        if len(new_password) < 8:
            messages.error(request, 'Password must be at least 8 characters long')
            return redirect(f'/franchise/reset-password?token={token}&franchise_id={franchise_id}')

        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        db = client["Diagnostics"]
        user_collection = db["franchise_user"]

        try:
            user = user_collection.find_one({
                "franchise_id": franchise_id,
                "reset_token": token,
                "reset_token_expires": {"$gt": datetime.utcnow()}
            })

            target_db = db
            if not user:
                try:
                    fallback_user = client["franchise"]["franchise_user"].find_one({
                        "franchise_id": franchise_id,
                        "reset_token": token,
                        "reset_token_expires": {"$gt": datetime.utcnow()}
                    })
                    if fallback_user:
                        user = fallback_user
                        target_db = client["franchise"]
                except Exception:
                    pass

            if not user:
                messages.error(request, 'Invalid or expired reset token')
                return redirect(f'/franchise/reset-password?token={token}&franchise_id={franchise_id}')

            hashed_password = make_password(new_password)

            update_doc = {
                "$set": {
                    "password": hashed_password,
                    "reset_password": False,
                    "lastmodified_date": datetime.utcnow(),
                    "lastmodified_by": franchise_id
                },
                "$unset": {
                    "reset_token": "",
                    "reset_token_expires": ""
                }
            }

            user_collection.update_one({"_id": user["_id"]}, update_doc)
            try:
                client["franchise"]["franchise_user"].update_one({"franchise_id": franchise_id}, update_doc)
            except Exception:
                pass

            # Update Django ORM Franchise model
            Franchise.objects.filter(franchise_id=franchise_id).update(
                is_active=True,
                lastmodified_by=franchise_id,
                lastmodified_date=datetime.utcnow()
            )

            # Also sync mongo collections
            for db_name in ["Diagnostics", "franchise"]:
                for col_name in ["core_franchise", "franchise_franchise"]:
                    try:
                        client[db_name][col_name].update_many(
                            {"franchise_id": franchise_id},
                            {"$set": {"is_active": True, "lastmodified_by": franchise_id, "lastmodified_date": datetime.utcnow()}}
                        )
                    except Exception:
                        pass

            return render(request, 'email/success.html', {
                'message': 'Password updated successfully. Your account is now active.',
                'franchise_id': franchise_id
            })

        except Exception as e:
            messages.error(request, f'Password reset failed: {str(e)}')
            return redirect(f'/franchise/reset-password?token={token}&franchise_id={franchise_id}')
        finally:
            client.close()

    return reset_password_form(request)


@csrf_exempt
@api_view(['GET'])
@permission_classes([HasRolePermission])
def validate_reset_token(request):
    token = request.GET.get('token')
    franchise_id = request.GET.get('franchise_id')
    
    if not token or not franchise_id:
        return Response({
            'error': 'Token and franchise ID are required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Diagnostics"]
    franchise_user_collection = db["franchise_user"]
    
    try:
        user = franchise_user_collection.find_one({
            "franchise_id": franchise_id,
            "reset_token": token,
            "reset_token_expires": {"$gt": datetime.utcnow()}
        })
        if not user:
            try:
                user = client["franchise"]["franchise_user"].find_one({
                    "franchise_id": franchise_id,
                    "reset_token": token,
                    "reset_token_expires": {"$gt": datetime.utcnow()}
                })
            except Exception:
                pass
        
        if user:
            return Response({
                'valid': True,
                'franchise_id': franchise_id
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'valid': False,
                'error': 'Invalid or expired token'
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except Exception as e:
        return Response({
            'error': f'Validation failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        client.close()


@csrf_exempt
@api_view(['GET'])
@permission_classes([HasRolePermission])
def get_registered_franchise(request):
    try:
        franchises = Franchise.objects.all()
        serializer = FranchiseSerializer(franchises, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([HasRolePermission])
def get_inactive_franchise_locations(request):
    try:
        active_query = {
            "$or": [
                {"is_active": True},
                {"is_active": "true"},
                {"is_active": "True"},
                {"is_active": {"$exists": False}}
            ]
        }

        # Primary: cluster_collection from dbcollection.py
        data = list(cluster_collection.find(active_query))
        if not data:
            data = list(cluster_collection.find())

        # Fallback 1: Check Global db franchise_location_details
        if not data:
            mongo_client = cluster_collection.database.client
            global_db = cluster_collection.database
            if "franchise_location_details" in global_db.list_collection_names():
                data = list(global_db["franchise_location_details"].find(active_query))

        # Fallback 2: Check franchise db
        if not data:
            mongo_client = cluster_collection.database.client
            franchise_db = mongo_client["franchise"]
            for col in ["franchise_location_details", "core_franchise_location_details"]:
                if col in franchise_db.list_collection_names():
                    data = list(franchise_db[col].find(active_query))
                    if not data:
                        data = list(franchise_db[col].find())
                    if data:
                        break

        # Fallback 3: Check Diagnostics db
        if not data:
            mongo_client = cluster_collection.database.client
            diag_db = mongo_client["Diagnostics"]
            for col in ["core_franchise_location_details", "franchise_location_details"]:
                if col in diag_db.list_collection_names():
                    data = list(diag_db[col].find(active_query))
                    if not data:
                        data = list(diag_db[col].find())
                    if data:
                        break

        return HttpResponse(dumps(data, indent=2), content_type="application/json")
    except Exception as e:
        return HttpResponse(
            dumps({ "error": str(e) }),
            content_type="application/json",
            status=500
        )


@api_view(['GET'])
@permission_classes([HasRolePermission])
def get_all_franchise_locations(request):
    try:
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        diag_db = client["Diagnostics"]
        
        # Primary: Diagnostics core_franchise_location_details
        collection = diag_db["core_franchise_location_details"]
        data = list(collection.find())
        if not data:
            data = list(diag_db["franchise_location_details"].find())
        if not data:
            try:
                data = list(client["franchise"]["franchise_location_details"].find())
            except Exception:
                pass

        for item in data:
            item["_id"] = str(item["_id"])
            if "created_date" in item:
                item["created_date"] = item["created_date"].isoformat()
            if "lastmodified_date" in item:
                item["lastmodified_date"] = item["lastmodified_date"].isoformat()

        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({ "error": str(e) }, status=500)


@csrf_exempt
@api_view(['GET'])
@permission_classes([AllowAny])
def get_file(request, file_id):
    try:
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        
        file_obj = None
        # Try Diagnostics, franchise, whatsapp, Global databases GridFS
        for db_name in ["Diagnostics", "franchise", "whatsapp", "Global"]:
            try:
                fs_candidate = gridfs.GridFS(client[db_name])
                file_obj = fs_candidate.get(ObjectId(file_id))
                if file_obj:
                    break
            except Exception:
                pass

        if not file_obj:
            return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)

        content_type = getattr(file_obj, 'content_type', None)
        filename = getattr(file_obj, 'filename', '') or 'document'
        if not content_type:
            lower_name = filename.lower()
            if lower_name.endswith('.pdf'):
                content_type = 'application/pdf'
            elif lower_name.endswith(('.jpg', '.jpeg')):
                content_type = 'image/jpeg'
            elif lower_name.endswith('.png'):
                content_type = 'image/png'
            else:
                content_type = 'application/octet-stream'

        response = HttpResponse(file_obj.read(), content_type=content_type)
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        response['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PATCH'])
@permission_classes([HasRolePermission])
def update_franchise_status(request, location_id):
    try:
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        diag_db = client["Diagnostics"]
        user_id = request.headers.get("auth-user-id")
        object_id = ObjectId(location_id)
        
        is_active = request.data.get("is_active")

        # Update across Diagnostics and fallback franchise
        updated = False
        for db_name in ["Diagnostics", "franchise"]:
            for col_name in ["core_franchise_location_details", "franchise_location_details"]:
                try:
                    res = client[db_name][col_name].update_one(
                        {"_id": object_id},
                        {
                            "$set": {
                                "is_active": is_active,
                                "lastmodified_by": user_id,
                                "lastmodified_date": datetime.utcnow()
                            }
                        }
                    )
                    if res.matched_count > 0:
                        updated = True
                except Exception:
                    pass

        if not updated:
            return JsonResponse({"error": "Franchise location not found."}, status=404)

        return JsonResponse({"message": "Status updated successfully."}, status=200)
    except Exception as e:
        print("Error:", str(e))
        return JsonResponse({"error": "Something went wrong."}, status=500)


@api_view(['PATCH'])
@permission_classes([HasRolePermission])
def toggle_franchise_status(request, franchise_id):
    try:
        user_id = request.headers.get("auth-user-id")
        new_status = None

        # 1. Update via Django ORM Model (primary store in Diagnostics)
        franchise = Franchise.objects.filter(franchise_id=franchise_id).first()
        if franchise:
            franchise.is_active = not bool(franchise.is_active)
            franchise.lastmodified_by = user_id
            franchise.lastmodified_date = datetime.utcnow()
            franchise.save()
            new_status = franchise.is_active

        # 2. Also sync to all MongoDB database collections
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        found_in_mongo = False

        for db_name in ["Diagnostics", "franchise", "Global"]:
            for col_name in ["core_franchise", "franchise_franchise", "franchise"]:
                try:
                    col = client[db_name][col_name]
                    doc = col.find_one({"franchise_id": franchise_id})
                    if doc:
                        if new_status is None:
                            new_status = not bool(doc.get("is_active", False))
                        col.update_one(
                            {"franchise_id": franchise_id},
                            {
                                "$set": {
                                    "is_active": new_status,
                                    "lastmodified_by": user_id,
                                    "lastmodified_date": datetime.utcnow()
                                }
                            }
                        )
                        found_in_mongo = True
                except Exception:
                    pass

        if new_status is None and not found_in_mongo:
            return Response({"error": "Franchise not found"}, status=status.HTTP_404_NOT_FOUND)

        # 3. Sync to franchise_user collection in Diagnostics and franchise
        for db_name in ["Diagnostics", "franchise"]:
            try:
                client[db_name]["franchise_user"].update_many(
                    {"franchise_id": franchise_id},
                    {"$set": {"is_active": new_status, "lastmodified_by": user_id, "lastmodified_date": datetime.utcnow()}}
                )
            except Exception:
                pass

        return Response({
            "message": "Status updated successfully",
            "is_active": new_status,
            "employee": {
                "franchise_id": franchise_id,
                "is_active": new_status
            }
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@require_http_methods(["GET"])
@permission_classes([HasRolePermission])
def get_franchise(request, franchise_id):
    try:
        franchise = get_object_or_404(Franchise, franchise_id=franchise_id)
        franchise_data = {
            'franchise_id': franchise.franchise_id,
            'franchiser_name': franchise.franchise_name,
            'location': franchise.location_id,
            'contact_no': franchise.contact_no,
            'email': franchise.email,
            'alt_number': franchise.alt_number,
            'address': franchise.address,
            'qualification': franchise.qualification,
            'age': franchise.age,
            'gender': franchise.gender,
            'pincode': franchise.pincode,
            'dob': franchise.dob.isoformat() if franchise.dob else None,
            'is_active': franchise.is_active,
            'aadhaar_file_id': franchise.aadhaar_file_id,
            'pan_file_id': getattr(franchise, 'pan_file_id', None),
            'payment_file_id': franchise.payment_file_id,
            'agreement_file_id': franchise.agreement_file_id,
            'franchise_photo_file_id': franchise.franchise_photo_file_id,
        }
        return JsonResponse(franchise_data)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


class MongoJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        return super().default(obj)


@csrf_exempt
@require_http_methods(["POST"])
@permission_classes([HasRolePermission])
def update_franchise(request, franchise_id):
    try:
        user_id = request.headers.get("auth-user-id", "system")
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        db = client["Diagnostics"]
        fs = gridfs.GridFS(db)

        franchise = Franchise.objects.filter(franchise_id=franchise_id).first()

        if franchise:
            if "franchiser_name" in request.POST or "franchise_name" in request.POST:
                franchise.franchise_name = request.POST.get("franchiser_name") or request.POST.get("franchise_name")
            if "location" in request.POST or "location_id" in request.POST:
                franchise.location_id = request.POST.get("location") or request.POST.get("location_id")
            if "contact_no" in request.POST:
                franchise.contact_no = request.POST.get("contact_no")
            if "email" in request.POST:
                franchise.email = request.POST.get("email")
            if "alt_number" in request.POST:
                franchise.alt_number = request.POST.get("alt_number")
            if "address" in request.POST:
                franchise.address = request.POST.get("address")
            if "qualification" in request.POST:
                franchise.qualification = request.POST.get("qualification")
            if "age" in request.POST:
                try:
                    franchise.age = int(request.POST.get("age"))
                except ValueError:
                    pass
            if "gender" in request.POST:
                franchise.gender = request.POST.get("gender")
            if "pincode" in request.POST:
                franchise.pincode = request.POST.get("pincode")
            if "dob" in request.POST:
                try:
                    franchise.dob = datetime.fromisoformat(request.POST.get("dob"))
                except Exception:
                    pass
            if "is_active" in request.POST:
                franchise.is_active = (request.POST.get("is_active").lower() == "true")

            file_fields_map = {
                "aadhaar_file": "aadhaar_file_id",
                "pan_file": "pan_file_id",
                "pan_proof": "pan_file_id",
                "payment_file": "payment_file_id",
                "agreement_file": "agreement_file_id",
                "franchise_photo": "franchise_photo_file_id",
            }
            for incoming_name, model_field in file_fields_map.items():
                if incoming_name in request.FILES:
                    uploaded_file = request.FILES[incoming_name]
                    file_id = fs.put(
                        uploaded_file.read(),
                        filename=uploaded_file.name,
                        content_type=uploaded_file.content_type
                    )
                    setattr(franchise, model_field, str(file_id))

            franchise.lastmodified_by = user_id
            franchise.lastmodified_date = datetime.utcnow()
            franchise.save()

        # Also sync to Mongo collections
        update_fields = {}
        text_keys = ["contact_no", "email", "alt_number", "address", "qualification", "age", "gender", "pincode"]
        for k in text_keys:
            if k in request.POST:
                update_fields[k] = request.POST.get(k)
        if "franchiser_name" in request.POST:
            update_fields["franchise_name"] = request.POST.get("franchiser_name")
            update_fields["franchiser_name"] = request.POST.get("franchiser_name")
        if "location" in request.POST:
            update_fields["location_id"] = request.POST.get("location")
            update_fields["location"] = request.POST.get("location")
        if "is_active" in request.POST:
            update_fields["is_active"] = (request.POST.get("is_active").lower() == "true")
        update_fields["lastmodified_by"] = user_id
        update_fields["lastmodified_date"] = datetime.utcnow()

        for db_name in ["Diagnostics", "franchise", "Global"]:
            for col_name in ["core_franchise", "franchise_franchise", "franchise"]:
                try:
                    client[db_name][col_name].update_many(
                        {"franchise_id": franchise_id},
                        {"$set": update_fields}
                    )
                except Exception:
                    pass

        return JsonResponse({"message": "Franchise updated successfully"}, status=200)
    except Http404 as e:
        return JsonResponse({"error": str(e)}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([HasRolePermission])
def generate_next_franchise_id(request):
    last_franchise = Franchise.objects.order_by('-franchise_id').first()
    if last_franchise:
        last_id_num = int(last_franchise.franchise_id.replace('SHF', ''))
        next_id_num = last_id_num + 1
    else:
        next_id_num = 1

    next_franchise_id = f'SHF{next_id_num:03d}'
    return JsonResponse({'franchise_id': next_franchise_id})


    

@api_view(['POST'])
@permission_classes([HasRolePermission])
def savestockbarcode(request):
    data = request.data.copy()
    user_id = (
        data.get('auth-user-id') or
        data.get('createdby') or
        (request.user.username if request.user and request.user.is_authenticated else '') or
        ''
    )
    data['createdby'] = user_id
    data['modifedby'] = ''

    serializer = BarcodestockSerializer(data=data)
    if serializer.is_valid():
        serializer.save()
        return Response({'message': 'Barcode stock saved successfully'}, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([HasRolePermission])
def get_franchises(request):
    franchises = Franchise.objects.all()
    serializer = FranchiseSerializer(franchises, many=True)
    return Response(serializer.data)


@csrf_exempt
@api_view(['POST'])
def resend_password_reset_email(request):
    franchise_id = request.data.get('franchise_id')
    
    if not franchise_id:
        return Response({
            'error': 'Franchise ID is required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Diagnostics"]
    franchise_user_collection = db["franchise_user"]
    
    try:
        user = franchise_user_collection.find_one({
            "franchise_id": franchise_id,
            "reset_password": True
        })
        if not user:
            try:
                user = client["franchise"]["franchise_user"].find_one({
                    "franchise_id": franchise_id,
                    "reset_password": True
                })
            except Exception:
                pass
        
        if not user:
            return Response({
                'error': 'Franchise not found or already activated'
            }, status=status.HTTP_404_NOT_FOUND)
        
        franchise = Franchise.objects.filter(franchise_id=franchise_id).first()
        franchise_email = franchise.email if franchise else None
        franchise_name = franchise.franchise_name if franchise else 'Franchise Partner'

        if not franchise_email:
            for db_name in ["Diagnostics", "franchise"]:
                for col_name in ["core_franchise", "franchise_franchise"]:
                    try:
                        f_doc = client[db_name][col_name].find_one({"franchise_id": franchise_id})
                        if f_doc and f_doc.get('email'):
                            franchise_email = f_doc.get('email')
                            franchise_name = f_doc.get('franchise_name', franchise_name)
                            break
                    except Exception:
                        pass
        
        new_reset_token = str(uuid.uuid4())
        new_expiry = datetime.utcnow() + timedelta(hours=72)
        
        for db_name in ["Diagnostics", "franchise"]:
            try:
                client[db_name]["franchise_user"].update_one(
                    {"franchise_id": franchise_id},
                    {
                        "$set": {
                            "reset_token": new_reset_token,
                            "reset_token_expires": new_expiry,
                        }
                    }
                )
            except Exception:
                pass
        
        send_franchise_reminder_email(
            email=franchise_email or user.get('email'),
            franchise_id=franchise_id,
            reset_token=new_reset_token,
            franchise_name=franchise_name
        )
        
        return Response({
            'message': 'Password reset email resent successfully',
            'franchise_id': franchise_id,
            'new_expiry': new_expiry.isoformat()
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'error': f'Failed to resend email: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        client.close()


def send_franchise_reminder_email(email, franchise_id, reset_token, franchise_name):
    try:
        reset_link = f"{os.getenv('FRONTEND_URL', 'http://127.0.0.1:8000')}/franchise/reset-password?token={reset_token}&franchise_id={franchise_id}"
        context = {
            'franchise_name': franchise_name,
            'franchise_id': franchise_id,
            'reset_link': reset_link,
            'expiry_hours': 72,
            'is_reminder': True
        }
        html_message = render_to_string('email/franchise_reminder.html', context)
        plain_message = strip_tags(html_message)
        send_mail(
            subject='Reminder: Complete Your Shanmuga Franchise Account Setup',
            message=plain_message,
            from_email=os.getenv('EMAIL_FROM', 'noreply@shanmugafranchise.com'),
            recipient_list=[email],
            html_message=html_message,
            fail_silently=False,
        )
        print(f"Reminder email sent successfully to {email}")
    except Exception as e:
        print(f"Failed to send reminder email to {email}: {str(e)}")


@api_view(['GET'])
def inactive_franchises(request):
    try:
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        db = client["Diagnostics"]
        user_collection = db["franchise_user"]
        
        users = list(user_collection.find({"reset_password": True}))
        if not users:
            try:
                users = list(client["franchise"]["franchise_user"].find({"reset_password": True}))
            except Exception:
                pass

        enriched_users = []
        for user in users:
            user["_id"] = str(user["_id"])
            f_obj = Franchise.objects.filter(franchise_id=user["franchise_id"]).first()
            if f_obj:
                user["franchise_name"] = f_obj.franchise_name
                user["email"] = f_obj.email
                user["phone"] = f_obj.contact_no
                user["location"] = f_obj.location_id
            else:
                user["franchise_name"] = "Unknown"
                user["email"] = "No email"
                user["phone"] = "No phone"
                user["location"] = "No location"
            
            if user.get("reset_token_expires"):
                user["is_expired"] = user["reset_token_expires"] < datetime.utcnow()
            else:
                user["is_expired"] = True
                
            enriched_users.append(user)
        
        return Response(enriched_users)
    except Exception as e:
        return Response({"error": str(e)}, status=500)
    finally:
        client.close()


@csrf_exempt
@api_view(['POST'])
def bulk_resend_password_reset_emails(request):
    franchise_ids = request.data.get('franchise_ids', [])
    
    if not franchise_ids or not isinstance(franchise_ids, list):
        return Response({
            'error': 'franchise_ids array is required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Diagnostics"]
    franchise_user_collection = db["franchise_user"]
    
    success_count = 0
    failed_franchises = []
    
    try:
        for franchise_id in franchise_ids:
            try:
                user = franchise_user_collection.find_one({
                    "franchise_id": franchise_id,
                    "reset_password": True
                })
                if not user:
                    try:
                        user = client["franchise"]["franchise_user"].find_one({
                            "franchise_id": franchise_id,
                            "reset_password": True
                        })
                    except Exception:
                        pass
                
                if not user:
                    failed_franchises.append({
                        'franchise_id': franchise_id,
                        'error': 'Franchise not found or already activated'
                    })
                    continue
                
                franchise = Franchise.objects.filter(franchise_id=franchise_id).first()
                franchise_email = franchise.email if franchise else None
                franchise_name = franchise.franchise_name if franchise else 'Franchise Partner'

                if not franchise_email:
                    failed_franchises.append({
                        'franchise_id': franchise_id,
                        'error': 'Franchise details not found'
                    })
                    continue
                
                new_reset_token = str(uuid.uuid4())
                new_expiry = datetime.utcnow() + timedelta(hours=72)
                
                for db_name in ["Diagnostics", "franchise"]:
                    try:
                        client[db_name]["franchise_user"].update_one(
                            {"franchise_id": franchise_id},
                            {
                                "$set": {
                                    "reset_token": new_reset_token,
                                    "reset_token_expires": new_expiry,
                                    "lastmodified_date": datetime.utcnow(),
                                    "lastmodified_by": 'system'
                                }
                            }
                        )
                    except Exception:
                        pass
                
                send_franchise_reminder_email(
                    email=franchise_email,
                    franchise_id=franchise_id,
                    reset_token=new_reset_token,
                    franchise_name=franchise_name
                )
                
                success_count += 1
            except Exception as e:
                failed_franchises.append({
                    'franchise_id': franchise_id,
                    'error': str(e)
                })
        
        return Response({
            'message': f'Bulk resend completed. {success_count} emails sent successfully.',
            'success_count': success_count,
            'failed_count': len(failed_franchises),
            'failed_franchises': failed_franchises
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({
            'error': f'Bulk resend failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        client.close()


@csrf_exempt
def update_test_status(request):
    if request.method != 'POST':
        return JsonResponse({"error": "Method not allowed"}, status=405)
    
    try:
        data = json.loads(request.body)
        patient_id = data.get('patient_id')
        barcode = data.get('barcode')
        test_name = data.get('test_name')
        new_status = data.get('new_status')
        
        if not all([patient_id, barcode, test_name, new_status]):
            return JsonResponse({
                "error": "Missing required fields: patient_id, barcode, test_name, new_status"
            }, status=400)
        
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        db = client["Diagnostics"]
        franchise_collection = db["franchise_billing"]
        
        document = franchise_collection.find_one({
            "patient_id": patient_id,
            "barcode": barcode
        })
        if not document:
            try:
                document = client["franchise"]["franchise_billing"].find_one({
                    "patient_id": patient_id,
                    "barcode": barcode
                })
                if document:
                    franchise_collection = client["franchise"]["franchise_billing"]
            except Exception:
                pass
        
        if not document:
            return JsonResponse({
                "error": "Document not found"
            }, status=404)
        
        if "testdetails" not in document or not document["testdetails"]:
            return JsonResponse({
                "error": "No test details found"
            }, status=404)
        
        try:
            testdetails_str = json.loads(document["testdetails"])  
            if isinstance(testdetails_str, str):
                testdetails = json.loads(testdetails_str)
            else:
                testdetails = testdetails_str
        except Exception as e:
            return JsonResponse({
                "error": f"Error parsing testdetails: {str(e)}"
            }, status=400)
        
        test_found = False
        for test in testdetails:
            if test.get("test_name") == test_name:
                test["status"] = new_status
                test_found = True
                break
        
        if not test_found:
            return JsonResponse({
                "error": "Test not found"
            }, status=404)
        
        updated_testdetails = json.dumps(json.dumps(testdetails))
        
        result = franchise_collection.update_one(
            {
                "patient_id": patient_id,
                "barcode": barcode
            },
            {
                "$set": {"testdetails": updated_testdetails}
            }
        )
        
        if result.modified_count == 1:
            return JsonResponse({
                "message": "Test status updated successfully",
                "patient_id": patient_id,
                "barcode": barcode,
                "test_name": test_name,
                "new_status": new_status
            }, status=200)
        else:
            return JsonResponse({
                "error": "Failed to update document"
            }, status=500)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({
            "error": f"Internal server error: {str(e)}"
        }, status=500)
    finally:
        if 'client' in locals():
            client.close()


@csrf_exempt
def get_cancel_requested_tests(request):
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Diagnostics"]
    franchise_collection = db["franchise_billing"]

    results = []
    docs = list(franchise_collection.find())
    if not docs:
        try:
            docs = list(client["franchise"]["franchise_billing"].find())
        except Exception:
            pass

    for doc in docs:
        if "testdetails" in doc and doc["testdetails"]:
            try:
                testdetails_str = json.loads(doc["testdetails"])  
                if isinstance(testdetails_str, str):
                    testdetails = json.loads(testdetails_str)
                else:
                    testdetails = testdetails_str

                cancel_requested = [
                    test for test in testdetails 
                    if test.get("status") in ["Cancel Requested", "Cancel Accepted", "Rejected"]
                ]
                
                if cancel_requested:
                    results.append({
                        "patient_id": doc.get("patient_id"),
                        "barcode": doc.get("barcode"),
                        "referredDoctor": doc.get("referredDoctor"),
                        "franchise_id": doc.get("franchise_id"),
                        "cancel_requested_tests": cancel_requested
                    })
            except Exception as e:
                print("Error parsing testdetails:", e, doc.get("_id"))

    client.close()
    return JsonResponse({"cancel_requested": results}, safe=False)


@csrf_exempt
def update_cancel_status(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        data = json.loads(request.body.decode("utf-8"))
        patient_id = data.get("patient_id")
        test_id = data.get("test_id")
        action = data.get("action")

        if not (patient_id and test_id and action):
            return JsonResponse(
                {"error": "patient_id, test_id and action are required"}, status=400
            )

        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        db = client["Diagnostics"]
        franchise_collection = db["franchise_billing"]
        franchise_revenue_collection = db["franchise_franchisemonthlyrevenue"]

        patient = franchise_collection.find_one({"patient_id": patient_id})
        if not patient:
            try:
                fallback_patient = client["franchise"]["franchise_billing"].find_one({"patient_id": patient_id})
                if fallback_patient:
                    patient = fallback_patient
                    franchise_collection = client["franchise"]["franchise_billing"]
                    franchise_revenue_collection = client["franchise"]["franchise_franchisemonthlyrevenue"]
            except Exception:
                pass

        if not patient:
            return JsonResponse({"error": "Patient not found"}, status=404)

        raw = patient.get("testdetails", "[]")
        testdetails = raw
        for _ in range(3):
            if isinstance(testdetails, str):
                try:
                    testdetails = json.loads(testdetails)
                except Exception:
                    break
            else:
                break

        if not isinstance(testdetails, list):
            return JsonResponse({"error": "testdetails is not a list"}, status=400)

        updated = False
        cancelled_test = None
        for test in testdetails:
            if str(test.get("test_id")) == str(test_id) and test.get("status") in ["Cancel Accepted"]:
                if action == "approve":
                    test["status"] = "Cancel Approved"
                    test["cancel_approved_date"] = datetime.utcnow().isoformat()
                    cancelled_test = test
                elif action == "reject":
                    test["status"] = "Reject"
                    test["rejected_date"] = datetime.utcnow().isoformat()
                updated = True
                break

        if not updated:
            return JsonResponse(
                {"error": "No matching test with status 'Cancel Accepted'"}, status=404
            )

        if action == "approve" and cancelled_test:
            test_mrp = float(cancelled_test.get("MRP", 0))
            discount_percentage = float(cancelled_test.get("discountPercentage", 0))

            discount_amount = (test_mrp * discount_percentage) / 100
            final_amount = test_mrp - discount_amount

            franchise_collection.update_one(
                {"patient_id": patient_id},
                [
                    {
                        "$set": {
                            "total": {
                                "$subtract": [
                                    {"$toDouble": {"$ifNull": ["$total", 0]}},
                                    final_amount
                                ]
                            },
                            "netAmount": {
                                "$subtract": [
                                    {"$toDouble": {"$ifNull": ["$netAmount", 0]}},
                                    final_amount
                                ]
                            }
                        }
                    }
                ]
            )

            franchise_id = patient.get("franchise_id")
            current_date = datetime.utcnow()
            month = current_date.month
            year = current_date.year

            if franchise_id:
                franchise_share = final_amount * 0.5
                franchiser_share = final_amount * 0.5

                franchise_revenue_collection.update_one(
                    {
                        "franchise_id": franchise_id,
                        "month": month,
                        "year": year
                    },
                    [
                        {
                            "$set": {
                                "franchise_share": {
                                    "$subtract": [
                                        {"$toDouble": {"$ifNull": ["$franchise_share", 0]}},
                                        franchise_share
                                    ]
                                },
                                "franchiser_share": {
                                    "$subtract": [
                                        {"$toDouble": {"$ifNull": ["$franchiser_share", 0]}},
                                        franchiser_share
                                    ]
                                },
                                "total_revenue": {
                                    "$subtract": [
                                        {"$toDouble": {"$ifNull": ["$total_revenue", 0]}},
                                        final_amount
                                    ]
                                }
                            }
                        }
                    ]
                )

        updated_testdetails = json.dumps(testdetails)

        franchise_collection.update_one(
            {"patient_id": patient_id},
            {
                "$set": {
                    "testdetails": updated_testdetails,
                    "lastmodified_date": datetime.utcnow()
                }
            }
        )

        return JsonResponse(
            {"success": True, "message": f"Cancel request {action}d successfully"}
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    finally:
        if "client" in locals():
            client.close()


@csrf_exempt
def month_end_calculation(request):
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    db = client["Diagnostics"]

    def safe_float(value):
        if isinstance(value, Decimal128):
            return float(value.to_decimal())
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    if request.method == "POST":
        try:
            body = json.loads(request.body) if request.body else {}
            franchise_id = body.get("franchise_id")
            month = body.get("month")
            year = body.get("year")
        except json.JSONDecodeError:
            franchise_id = None
            month = None
            year = None

        if franchise_id and month and year:
            monthly_doc = db["franchise_franchisemonthlyrevenue"].find_one({
                "franchise_id": franchise_id,
                "month": month,
                "year": year
            })
            if not monthly_doc:
                try:
                    monthly_doc = client["franchise"]["franchise_franchisemonthlyrevenue"].find_one({
                        "franchise_id": franchise_id,
                        "month": month,
                        "year": year
                    })
                except Exception:
                    pass

            wallet_doc = db["core_wallet"].find_one({"franchise_id": franchise_id}) or db["franchise_wallet"].find_one({"franchise_id": franchise_id})
            if not wallet_doc:
                try:
                    wallet_doc = client["franchise"]["franchise_wallet"].find_one({"franchise_id": franchise_id})
                except Exception:
                    pass

            if monthly_doc and wallet_doc:
                total_revenue = safe_float(monthly_doc.get("total_revenue", 0))
                franchiser_share = safe_float(monthly_doc.get("franchise_share", 0))
                wallet_balance = safe_float(wallet_doc.get("balance", 0))
                calculated_value = wallet_balance - (total_revenue - franchiser_share)

                db["franchise_wallet"].update_one(
                    {"franchise_id": franchise_id},
                    {
                        "$set": {
                            "balance": calculated_value,
                            "updated_date": datetime.utcnow()
                        }
                    }
                )

                db["franchise_franchisemonthlyrevenue"].update_one(
                    {"franchise_id": franchise_id, "month": month, "year": year},
                    {
                        "$set": {
                            "status": "closed",
                            "closed_date": datetime.utcnow()
                        }
                    }
                )
                message = f"Franchise {franchise_id} closed successfully for {month}/{year}"
            else:
                message = "Franchise or wallet not found"
        else:
            franchise_ids = db["franchise_franchisemonthlyrevenue"].distinct("franchise_id")
            for fid in franchise_ids:
                monthly_doc = db["franchise_franchisemonthlyrevenue"].find_one(
                    {"franchise_id": fid},
                    sort=[("created_date", -1)]
                )
                
                if monthly_doc and monthly_doc.get("status") == "active":
                    wallet_doc = db["franchise_wallet"].find_one({"franchise_id": fid})
                    if wallet_doc:
                        total_revenue = safe_float(monthly_doc.get("total_revenue", 0))
                        franchiser_share = safe_float(monthly_doc.get("franchise_share", 0))
                        wallet_balance = safe_float(wallet_doc.get("balance", 0))
                        calculated_value = wallet_balance - (total_revenue - franchiser_share)

                        db["franchise_wallet"].update_one(
                            {"franchise_id": fid},
                            {
                                "$set": {
                                    "balance": calculated_value,
                                    "updated_date": datetime.utcnow()
                                }
                            }
                        )

                        db["franchise_franchisemonthlyrevenue"].update_one(
                            {
                                "franchise_id": fid, 
                                "month": monthly_doc.get("month"), 
                                "year": monthly_doc.get("year")
                            },
                            {
                                "$set": {
                                    "status": "closed",
                                    "closed_date": datetime.utcnow()
                                }
                            }
                        )
            message = "All active franchises closed successfully"

    franchise_ids = db["franchise_franchisemonthlyrevenue"].distinct("franchise_id")
    results = []

    for franchise_id in franchise_ids:
        monthly_doc = db["franchise_franchisemonthlyrevenue"].find_one(
            {"franchise_id": franchise_id},
            sort=[("created_date", -1)]
        )
        wallet_doc = db["franchise_wallet"].find_one({"franchise_id": franchise_id})

        if not monthly_doc or not wallet_doc:
            continue

        total_revenue = safe_float(monthly_doc.get("total_revenue", 0))
        franchiser_share = safe_float(monthly_doc.get("franchise_share", 0))
        wallet_balance = safe_float(wallet_doc.get("balance", 0))
        calculated_value = wallet_balance - (total_revenue - franchiser_share)

        results.append({
            "franchise_id": franchise_id,
            "month": monthly_doc.get("month"),
            "year": monthly_doc.get("year"),
            "total_revenue": total_revenue,
            "franchise_share": franchiser_share,
            "wallet_balance": wallet_balance,
            "current_wallet_balance": calculated_value,
            "status": monthly_doc.get("status", "active")
        })

    if request.method == "POST":
        return JsonResponse({"message": message, "data": results}, safe=False)
    return JsonResponse({"data": results}, safe=False)


LOCATION_PREFIX = "SDMF"

def generate_next_location_id():
    last_location = location_collection.find_one(
        {"location_id": {"$regex": f"^{LOCATION_PREFIX}"}},
        sort=[("location_id", -1)],
    )
    if not last_location:
        return f"{LOCATION_PREFIX}001"

    match = re.search(r"(\d+)$", last_location["location_id"])
    last_number = int(match.group(1)) if match else 0
    padding = len(match.group(1)) if match else 3
    return f"{LOCATION_PREFIX}{str(last_number + 1).zfill(padding)}"


@api_view(['POST'])
def post_location(request):
    serializer = FranchiseLocationSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    user_id = request.headers.get("auth-user-id", "system")
    now = datetime.now(timezone.utc)
    validated = serializer.validated_data

    location = FranchiseLocation(
        location_id=generate_next_location_id(),
        Cluster_Name=validated['Cluster_Name'],
        District=validated['District'],
        Covered_Areas=validated.get('Covered_Areas', ''),
        is_active=validated.get('is_active', True),
        created_by=user_id,
        created_date=now,
        lastmodified_by=user_id,
        lastmodified_date=now,
    )

    result = location_collection.insert_one(location.to_mongo_dict())
    response_data = location.to_mongo_dict()
    response_data["_id"] = str(result.inserted_id)
    return Response(response_data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PUT'])
@permission_classes([HasRolePermission])
def getandupdatebarcode(request):
    if request.method == 'GET':
        from_date_param = request.GET.get('from_date') or request.GET.get('from')
        to_date_param = request.GET.get('to_date') or request.GET.get('to')
        date_param = request.GET.get('date')

        tz = timezone.get_current_timezone()

        if from_date_param or to_date_param:
            from_date = parse_date(from_date_param) if from_date_param else None
            to_date = parse_date(to_date_param) if to_date_param else None

            if not from_date and from_date_param:
                return Response(
                    {'error': 'Invalid from_date format. Use YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if not to_date and to_date_param:
                return Response(
                    {'error': 'Invalid to_date format. Use YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            from_date = from_date or to_date or timezone.localdate()
            to_date = to_date or from_date or timezone.localdate()

            start_of_day = timezone.make_aware(
                datetime.combine(from_date, datetime.min.time()), tz
            )
            end_of_day = timezone.make_aware(
                datetime.combine(to_date + timedelta(days=1), datetime.min.time()), tz
            )
        elif date_param:
            selected_date = parse_date(date_param)
            if not selected_date:
                return Response(
                    {'error': 'Invalid date format. Use YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            start_of_day = timezone.make_aware(
                datetime.combine(selected_date, datetime.min.time()), tz
            )
            end_of_day = start_of_day + timedelta(days=1)
        else:
            selected_date = timezone.localdate()
            start_of_day = timezone.make_aware(
                datetime.combine(selected_date, datetime.min.time()), tz
            )
            end_of_day = start_of_day + timedelta(days=1)

        queryset = barcodestock.objects.filter(
            date__gte=start_of_day,
            date__lt=end_of_day
        ).order_by('-date')

        serializer = BarcodestockSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.method == 'PUT':
        barcode_id = request.data.get('barcode_id')
        if not barcode_id:
            return Response(
                {'error': 'barcode_id is required to update.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            existing = barcodestock.objects.get(barcode_id=barcode_id)
        except barcodestock.DoesNotExist:
            return Response(
                {'error': 'No record found for this barcode_id.'},
                status=status.HTTP_404_NOT_FOUND
            )

        user_id = (
            request.data.get('modifedby') or
            request.data.get('modifiedby') or
            request.data.get('auth-user-id') or
            (request.user.username if request.user and request.user.is_authenticated else '') or
            ''
        )

        update_fields = {
            'startbarcode': request.data.get('startbarcode', existing.startbarcode),
            'endbarcode': request.data.get('endbarcode', existing.endbarcode),
            'modifedby': user_id,
            'modifieddatetime': timezone.now(),
        }

        updated_count = barcodestock.objects.filter(barcode_id=barcode_id).update(**update_fields)

        if not updated_count:
            return Response(
                {'error': 'Update failed — no matching document.'},
                status=status.HTTP_404_NOT_FOUND
            )

        instance = barcodestock.objects.get(barcode_id=barcode_id)
        return Response(
            {'message': 'Barcode stock updated successfully', 'data': BarcodestockSerializer(instance).data},
            status=status.HTTP_200_OK
        )
