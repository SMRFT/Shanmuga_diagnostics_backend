# core/Views/franchiseentrollment.py

import os
import re
import uuid
from decimal import Decimal
from datetime import datetime, timedelta

from django.db import models
from djongo import models as djongo_models
from bson import ObjectId, Decimal128
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



from ..models import Franchise, barcodestock, FranchiseLocation, FranchiseHomeCollection
from .dbcollection import (
    profile_collection,
    location_collection,
    franchise_register,
    franchise_homecollection,
    franchise_barcode,
    franchise_patient,
    franchise_billing,
    franchise_db,
    diag_db,
    global_db,
    client
)




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

        franchise_id = data.get('franchise_id')
        if not franchise_id:
            return Response({
                'error': 'Franchise ID is required for registration'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Parse dob to ISODate / datetime
        dob_val = data.get("dob")
        if dob_val and isinstance(dob_val, str):
            try:
                dob_val = datetime.strptime(dob_val[:10], "%Y-%m-%d")
            except Exception:
                try:
                    dob_val = datetime.fromisoformat(dob_val.replace("Z", "+00:00"))
                except Exception:
                    pass

        # 1. Store in franchise_register (franchise_db["franchise_franchise"])
        franchise_doc = {
            "franchise_id": franchise_id,
            "franchise_name": data.get("franchise_name") or data.get("franchiser_name", ""),
            "location_id": data.get("location_id", ""),
            "contact_no": data.get("contact_no", ""),
            "email": data.get("email", ""),
            "alt_number": data.get("alt_number", ""),
            "address": data.get("address", ""),
            "qualification": data.get("qualification", ""),
            "age": int(data.get("age", 0)) if str(data.get("age", "")).isdigit() else (data.get("age") or 0),
            "gender": data.get("gender", ""),
            "pincode": data.get("pincode", ""),
            "dob": dob_val,
            "initialpayment": data.get("initialpayment", "No"),
            "is_active": True,
            "createdby": user_id,
            "lastmodifiedby": user_id,
            "createddate": datetime.utcnow(),
            "lastmodifieddate": datetime.utcnow(),
        }

        # Add file IDs if present
        for fkey in ['aadhaar_file_id', 'pan_file_id', 'payment_file_id', 'agreement_file_id', 'franchise_photo_file_id']:
            if data.get(fkey):
                franchise_doc[fkey] = data.get(fkey)

        franchise_register.update_one(
            {"franchise_id": franchise_id},
            {"$set": franchise_doc},
            upsert=True
        )

        # 2. Also save via FranchiseSerializer for ORM/Wallet records
        serializer = FranchiseSerializer(data=data)
        if serializer.is_valid():
            serializer.save()

        # 3. Create user credentials & reset token
        dummy_password = str(uuid.uuid4())[:8]
        hashed_password = make_password(dummy_password)
        reset_token = str(uuid.uuid4())

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

        # Store in franchise_user collections
        db["franchise_user"].update_one(
            {"franchise_id": franchise_id},
            {"$set": franchise_user_data},
            upsert=True
        )
        try:
            franchise_db["franchise_user"].update_one(
                {"franchise_id": franchise_id},
                {"$set": franchise_user_data},
                upsert=True
            )
        except Exception:
            pass

        send_franchise_welcome_email(
            email=data.get('email'),
            franchise_id=franchise_id,
            reset_token=reset_token,
            franchise_name=data.get('franchise_name') or data.get('franchiser_name', 'Franchise Partner')
        )

        return Response({
            'message': 'Franchise registered successfully. Password reset email sent.',
            'franchise_id': franchise_id
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response({
            'error': f'Registration failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        client.close()


def send_franchise_welcome_email(email, franchise_id, reset_token, franchise_name):
    try:
        reset_link = f"{os.getenv('FRONTEND_URL', 'http://127.0.0.1:2106')}/franchise/reset-password?token={reset_token}&franchise_id={franchise_id}"
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
            from_email=os.getenv('EMAIL_HOST_USER', 'noreply@shanmugafranchise.com'),
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
        mongo_franchises = list(franchise_register.find({}, {"_id": 0}))
        if mongo_franchises:
            return Response(mongo_franchises, status=status.HTTP_200_OK)

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

        # Primary: location_collection from dbcollection.py (franchise_db["franchise_location_details"])
        data = list(location_collection.find(active_query))
        if not data:
            data = list(location_collection.find())

        # Normalize fields for frontend dropdown (location_id, Cluster_Name, District)
        normalized_data = []
        for item in data:
            item_dict = dict(item)
            if "_id" in item_dict:
                item_dict["_id"] = str(item_dict["_id"])
            loc_id = item_dict.get("location_id") or item_dict.get("locationId") or item_dict.get("id") or str(item_dict.get("_id"))
            cluster_name = item_dict.get("Cluster_Name") or item_dict.get("cluster_name") or item_dict.get("ClusterName") or item_dict.get("clusterName") or ""
            district = item_dict.get("District") or item_dict.get("district") or ""
            
            item_dict["location_id"] = loc_id
            item_dict["Cluster_Name"] = cluster_name
            item_dict["District"] = district
            normalized_data.append(item_dict)

        return Response(normalized_data, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([HasRolePermission])
def get_all_franchise_locations(request):
    try:
        # Fetches all locations from location_collection (franchise_db["franchise_location_details"])
        data = list(location_collection.find())

        for item in data:
            item["_id"] = str(item["_id"])
            if "created_date" in item and hasattr(item["created_date"], "isoformat"):
                item["created_date"] = item["created_date"].isoformat()
            if "lastmodified_date" in item and hasattr(item["lastmodified_date"], "isoformat"):
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
        doc = franchise_register.find_one({"franchise_id": franchise_id}, {"_id": 0})
        if doc:
            franchise_data = {
                'franchise_id': doc.get('franchise_id', franchise_id),
                'franchiser_name': doc.get('franchise_name') or doc.get('franchiser_name', ''),
                'franchise_name': doc.get('franchise_name') or doc.get('franchiser_name', ''),
                'location': doc.get('location_id') or doc.get('location', ''),
                'location_id': doc.get('location_id') or doc.get('location', ''),
                'contact_no': doc.get('contact_no', ''),
                'email': doc.get('email', ''),
                'alt_number': doc.get('alt_number', ''),
                'address': doc.get('address', ''),
                'qualification': doc.get('qualification', ''),
                'age': doc.get('age', 0),
                'gender': doc.get('gender', ''),
                'pincode': doc.get('pincode', ''),
                'dob': str(doc.get('dob', '')) if doc.get('dob') else None,
                'is_active': doc.get('is_active', False),
                'aadhaar_file_id': doc.get('aadhaar_file_id'),
                'pan_file_id': doc.get('pan_file_id'),
                'payment_file_id': doc.get('payment_file_id'),
                'agreement_file_id': doc.get('agreement_file_id'),
                'franchise_photo_file_id': doc.get('franchise_photo_file_id'),
            }
            return JsonResponse(franchise_data)

        franchise = Franchise.objects.filter(franchise_id=franchise_id).first()
        if franchise:
            franchise_data = {
                'franchise_id': franchise.franchise_id,
                'franchiser_name': franchise.franchise_name,
                'franchise_name': franchise.franchise_name,
                'location': franchise.location_id,
                'location_id': franchise.location_id,
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

        return JsonResponse({'error': 'Franchise not found'}, status=404)
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
        user_id = request.headers.get("auth-user-id")
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
    try:
        last_franchise = franchise_register.find_one(
            {"franchise_id": {"$regex": r"^SHF"}},
            sort=[("franchise_id", -1)]
        )
        if last_franchise and last_franchise.get('franchise_id'):
            match = re.search(r"(\d+)$", str(last_franchise["franchise_id"]))
            last_id_num = int(match.group(1)) if match else 0
            padding = len(match.group(1)) if match else 3
            next_id_num = last_id_num + 1
            next_franchise_id = f'SHF{str(next_id_num).zfill(max(3, padding))}'
        else:
            all_docs = list(franchise_register.find({}, {"franchise_id": 1}))
            max_num = 0
            for doc in all_docs:
                fid = str(doc.get("franchise_id", ""))
                m = re.search(r"(\d+)$", fid)
                if m:
                    max_num = max(max_num, int(m.group(1)))
            if max_num > 0:
                next_franchise_id = f'SHF{str(max_num + 1).zfill(3)}'
            else:
                next_franchise_id = 'SHF001'

        return JsonResponse({'franchise_id': next_franchise_id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


    

@api_view(['POST'])
@permission_classes([HasRolePermission])
def savestockbarcode(request):
    data = request.data.copy()
    user_id = (request.headers.get('auth-user-id') )

    startbarcode = str(data.get('startbarcode', '')).strip()
    endbarcode = str(data.get('endbarcode', '')).strip()

    if not startbarcode or not endbarcode:
        return Response({'error': 'startbarcode and endbarcode are required.'}, status=status.HTTP_400_BAD_REQUEST)

    # Generate sequential barcode_id (e.g. BC00001, BC00002, ...)
    last_doc = franchise_barcode.find_one(sort=[("_id", -1)])
    if last_doc and last_doc.get("barcode_id"):
        try:
            num = int(str(last_doc["barcode_id"]).replace("BC", ""))
            barcode_id = f"BC{num + 1:05d}"
        except Exception:
            barcode_id = f"BC{int(datetime.utcnow().timestamp())}"
    else:
        barcode_id = "BC00001"

    now = datetime.utcnow()
    mongo_doc = {
        "barcode_id": barcode_id,
        "startbarcode": startbarcode,
        "endbarcode": endbarcode,
        "date": now,
        "createddate": now,
        "createdby": user_id,
        "modifedby": "",
        "modifieddatetime": None
    }
    franchise_barcode.insert_one(mongo_doc)

    return Response({
        'message': 'Barcode stock saved successfully',
        'barcode_id': barcode_id,
        'data': {
            "barcode_id": barcode_id,
            "startbarcode": startbarcode,
            "endbarcode": endbarcode,
            "createdby": user_id,
            "createddate": now.isoformat()
        }
    }, status=status.HTTP_201_CREATED)


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
            from_email=os.getenv('EMAIL_HOST_USER', 'noreply@shanmugafranchise.com'),
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
        data = json.loads(request.body.decode("utf-8") if hasattr(request, "body") else "{}")
        patient_id = data.get('patient_id')
        barcode = data.get('barcode')
        test_name = data.get('test_name')
        test_id = data.get('test_id')
        action = data.get('action')  # 'approve' or 'reject'
        new_status = data.get('new_status')

        if not patient_id:
            return JsonResponse({"error": "patient_id is required"}, status=400)

        # Normalize action and status
        if action == "approve" or new_status == "Cancel Approved":
            resolved_action = "approve"
            target_status = "Cancel Approved"
        elif action == "reject" or new_status in ["Rejected", "Reject"]:
            resolved_action = "reject"
            target_status = "Rejected"
        elif new_status:
            resolved_action = None
            target_status = new_status
        else:
            return JsonResponse({"error": "Valid action or new_status is required"}, status=400)
        
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        client = MongoClient(mongo_url)
        db = client["franchise"]
        franchise_collection = db["franchise_billing"]
        franchise_revenue_collection = db["franchise_franchisemonthlyrevenue"]
        
        query = {"patient_id": patient_id}
        if barcode:
            query["barcode"] = barcode

        document = franchise_collection.find_one(query)
        if not document:
            try:
                document = client["Diagnostics"]["franchise_billing"].find_one(query)
                if document:
                    franchise_collection = client["Diagnostics"]["franchise_billing"]
                    franchise_revenue_collection = client["Diagnostics"]["franchise_franchisemonthlyrevenue"]
            except Exception:
                pass
        
        if not document:
            return JsonResponse({"error": "Document not found"}, status=404)
        
        if "testdetails" not in document or not document["testdetails"]:
            return JsonResponse({"error": "No test details found"}, status=404)
        
        raw = document.get("testdetails", "[]")
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
        
        test_found = False
        matched_test = None
        now_iso = datetime.utcnow().isoformat()

        user_id = (
            request.headers.get("auth-user-id") or
            data.get("auth-user-id") or
            data.get("approved_by") or
            data.get("user_id") or
            (request.user.username if request.user and request.user.is_authenticated else '') or
            "system"
        )

        for test in testdetails:
            id_match = test_id and str(test.get("test_id")) == str(test_id)
            name_match = test_name and str(test.get("test_name", "")).strip().lower() == str(test_name).strip().lower()

            if id_match or name_match:
                test["status"] = target_status
                if target_status == "Cancel Approved":
                    test["Approveddatetime"] = now_iso
                    test["cancel_approved_date"] = now_iso
                    test["approved_by"] = user_id
                elif target_status == "Rejected":
                    test["Rejecteddatetime"] = now_iso
                    test["rejected_date"] = now_iso
                    test["rejected_by"] = user_id
                test_found = True
                matched_test = test
                break
        
        if not test_found:
            return JsonResponse({"error": "Test not found"}, status=404)
        
        # Deduct total & netAmount and franchise monthly revenue if Cancel Approved
        update_dict = {
            "testdetails": testdetails,
            "lastmodified_by": user_id,
            "lastmodified_date": datetime.utcnow()
        }

        if target_status == "Cancel Approved" and matched_test:
            try:
                test_mrp = float(matched_test.get("MRP", 0))
                discount_percentage = float(matched_test.get("discountPercentage", 0))
                discount_amount = (test_mrp * discount_percentage) / 100
                final_amount = test_mrp - discount_amount

                current_total = float(document.get("total").to_decimal() if isinstance(document.get("total"), Decimal128) else (document.get("total") or 0))
                current_net = float(document.get("netAmount").to_decimal() if isinstance(document.get("netAmount"), Decimal128) else (document.get("netAmount") or 0))

                update_dict["total"] = Decimal128(f"{max(0.0, current_total - final_amount):.2f}")
                update_dict["netAmount"] = Decimal128(f"{max(0.0, current_net - final_amount):.2f}")

                franchise_id = document.get("franchise_id")
                current_date = datetime.utcnow()
                month = current_date.month
                year = current_date.year

                if franchise_id and franchise_revenue_collection is not None:
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
            except Exception as deduction_err:
                print("Error updating revenue/total deduction:", deduction_err)

        franchise_collection.update_one(
            {"patient_id": patient_id},
            {"$set": update_dict}
        )
        
        return JsonResponse({
            "success": True,
            "message": f"Test status updated to {target_status} successfully",
            "patient_id": patient_id,
            "barcode": barcode,
            "test_name": test_name,
            "new_status": target_status,
            "Approveddatetime": matched_test.get("Approveddatetime") if matched_test else None,
            "Rejecteddatetime": matched_test.get("Rejecteddatetime") if matched_test else None
        }, status=200)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({
            "error": f"Internal server error: {str(e)}"
        }, status=500)
    finally:
        if 'client' in locals():
            client.close()


@api_view(['GET'])
@permission_classes([HasRolePermission])
def get_cancel_requested_tests(request):
    try:
        # Only bills that have at least one test with this status
        query = {"testdetails.status": "Cancel Requested"}
        projection = {
            "_id": 0,
            "patient_id": 1,
            "barcode": 1,
            "registrationDate": 1,
            "referredDoctor": 1,
            "franchise_id": 1,
            "testdetails": 1,
        }

        documents = list(franchise_billing.find(query, projection))

        # Collect unique patient_ids and franchise_ids for batch lookup
        all_patient_ids = list({str(d.get("patient_id")) for d in documents if d.get("patient_id")})
        all_franchise_ids = list({str(d.get("franchise_id")) for d in documents if d.get("franchise_id")})

        patient_map = {}
        if all_patient_ids:
            try:
                for p in franchise_patient.find({"patient_id": {"$in": all_patient_ids}}):
                    pid = str(p.get("patient_id", ""))
                    pname = p.get("patientname") or p.get("patient_name") or p.get("name") or ""
                    patient_map[pid] = pname
            except Exception as e:
                print("Error loading patient names:", e)

        franchise_map = {}
        if all_franchise_ids:
            try:
                for f in franchise_register.find({"franchise_id": {"$in": all_franchise_ids}}):
                    fid_key = str(f.get("franchise_id", ""))
                    fname = f.get("franchise_name") or f.get("name") or ""
                    franchise_map[fid_key] = fname
            except Exception as e:
                print("Error loading franchise names:", e)

        result = []
        for doc in documents:
            cancel_tests = [
                t for t in doc.get("testdetails", [])
                if t.get("status") == "Cancel Requested"
            ]
            if not cancel_tests:
                continue

            # Same patient can cancel different tests at different times —
            # keep each one as its own entry, newest first.
            cancel_tests.sort(key=lambda t: t.get("requested_date") or "", reverse=True)

            pid = str(doc.get("patient_id", ""))
            fid = str(doc.get("franchise_id", ""))
            reg_date = doc.get("registrationDate")

            result.append({
                "patient_id": pid,
                "patient_name": patient_map.get(pid, ""),
                "barcode": doc.get("barcode"),
                "registrationDate": reg_date.isoformat() if reg_date else None,
                "referredDoctor": doc.get("referredDoctor"),
                "franchise_id": fid,
                "franchise_name": franchise_map.get(fid, ""),
                "cancel_requested_tests": cancel_tests,
            })

        # Sort bills by their most recent cancellation request
        def latest_request_time(item):
            times = [t.get("requested_date") for t in item["cancel_requested_tests"] if t.get("requested_date")]
            return max(times) if times else ""

        result.sort(key=latest_request_time, reverse=True)

        return Response({"cancel_requested": result}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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

        query = {}
        if from_date_param or to_date_param:
            from_date = parse_date(from_date_param) if from_date_param else None
            to_date = parse_date(to_date_param) if to_date_param else None
            if from_date and to_date:
                start_dt = datetime.combine(from_date, datetime.min.time())
                end_dt = datetime.combine(to_date, datetime.max.time())
                query["$or"] = [
                    {"date": {"$gte": start_dt, "$lte": end_dt}},
                    {"createddate": {"$gte": start_dt, "$lte": end_dt}}
                ]
            elif from_date:
                start_dt = datetime.combine(from_date, datetime.min.time())
                query["$or"] = [
                    {"date": {"$gte": start_dt}},
                    {"createddate": {"$gte": start_dt}}
                ]
        elif date_param:
            selected_date = parse_date(date_param)
            if selected_date:
                start_dt = datetime.combine(selected_date, datetime.min.time())
                end_dt = datetime.combine(selected_date, datetime.max.time())
                query["$or"] = [
                    {"date": {"$gte": start_dt, "$lte": end_dt}},
                    {"createddate": {"$gte": start_dt, "$lte": end_dt}}
                ]

        # Read directly from franchise_barcode collection (franchise_db["franchise_barcodestock"])
        records = list(franchise_barcode.find(query).sort("_id", -1))

        # Format records
        for item in records:
            item["_id"] = str(item["_id"])
            if "date" in item and hasattr(item["date"], "isoformat"):
                item["date"] = item["date"].isoformat()
            if "createddate" in item and hasattr(item["createddate"], "isoformat"):
                item["createddate"] = item["createddate"].isoformat()
            if "modifieddatetime" in item and hasattr(item["modifieddatetime"], "isoformat"):
                item["modifieddatetime"] = item["modifieddatetime"].isoformat()

        return Response(records, status=status.HTTP_200_OK)

    if request.method == 'PUT':
        barcode_id = request.data.get('barcode_id')
        if not barcode_id:
            return Response(
                {'error': 'barcode_id is required to update.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user_id = (
            request.data.get('modifedby') or
            request.data.get('modifiedby') or
            request.headers.get('auth-user-id') or
            request.data.get('auth-user-id') or
            (request.user.username if request.user and request.user.is_authenticated else '') or
            'system'
        )

        startbarcode = request.data.get('startbarcode')
        endbarcode = request.data.get('endbarcode')

        update_fields = {
            'modifedby': user_id,
            'modifieddatetime': datetime.utcnow(),
        }
        if startbarcode is not None:
            update_fields['startbarcode'] = startbarcode
        if endbarcode is not None:
            update_fields['endbarcode'] = endbarcode

        # Update in franchise_barcode collection
        franchise_barcode.update_one(
            {"barcode_id": barcode_id},
            {"$set": update_fields}
        )

        # Also update Django ORM
        try:
            barcodestock.objects.filter(barcode_id=barcode_id).update(**update_fields)
        except Exception:
            pass

        # Retrieve updated doc
        updated_doc = franchise_barcode.find_one({"barcode_id": barcode_id})
        if updated_doc:
            updated_doc["_id"] = str(updated_doc["_id"])
            if "date" in updated_doc and hasattr(updated_doc["date"], "isoformat"):
                updated_doc["date"] = updated_doc["date"].isoformat()
            if "createddate" in updated_doc and hasattr(updated_doc["createddate"], "isoformat"):
                updated_doc["createddate"] = updated_doc["createddate"].isoformat()
            if "modifieddatetime" in updated_doc and hasattr(updated_doc["modifieddatetime"], "isoformat"):
                updated_doc["modifieddatetime"] = updated_doc["modifieddatetime"].isoformat()
            return Response(
                {'message': 'Barcode stock updated successfully', 'data': updated_doc},
                status=status.HTTP_200_OK
            )

        return Response(
            {'message': 'Barcode stock updated successfully'},
            status=status.HTTP_200_OK
        )


@api_view(['GET', 'POST'])
@permission_classes([HasRolePermission])
def franchise_home_collection_views(request):
    if request.method == 'POST':
        data = request.data.copy()
        user_id = (
            request.headers.get("auth-user-id") or
            data.get("auth-user-id") or
            data.get("created_by") or
            (request.user.username if request.user and request.user.is_authenticated else "") or
            "system"
        )
        data['created_by'] = user_id
        data['lastmodified_by'] = user_id
        data['status'] = data.get('status') or 'Assigned'

        patient_name = data.get('patient_name', '').strip()
        address = data.get('address', '').strip()
        phone = data.get('phone', '').strip()
        franchise_id = data.get('franchise_id', '').strip()
        status_val = data.get('status', 'Assigned')

        if not patient_name:
            return Response({"error": "Patient name is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not franchise_id:
            return Response({"error": "Franchise ID is required."}, status=status.HTTP_400_BAD_REQUEST)

        franchise_name = data.get('franchise_name', '').strip()
        if not franchise_name and franchise_id:
            try:
                f_doc = franchise_register.find_one({"franchise_id": franchise_id})
                if f_doc:
                    franchise_name = f_doc.get("franchise_name") or f_doc.get("franchiser_name") or ""
            except Exception:
                pass

        # 1. Store directly into franchise_homecollection
        mongo_doc = {
            "patient_name": patient_name,
            "address": address,
            "phone": phone,
            "franchise_id": franchise_id,
            "franchise_name": franchise_name,
            "status": status_val,
            "date": datetime.utcnow(),
            "Remarks": data.get("Remarks", ""),
            "accepted_by": data.get("accepted_by", ""),
            "sample_accepted_time": data.get("sample_accepted_time", None),
            "created_by": user_id,
            "created_date": datetime.utcnow(),
            "lastmodified_by": user_id,
            "lastmodified_date": datetime.utcnow(),
        }
        res = franchise_homecollection.insert_one(mongo_doc)
        mongo_doc["_id"] = str(res.inserted_id)
        if "date" in mongo_doc and hasattr(mongo_doc["date"], "isoformat"):
            mongo_doc["date"] = mongo_doc["date"].isoformat()
        if "created_date" in mongo_doc and hasattr(mongo_doc["created_date"], "isoformat"):
            mongo_doc["created_date"] = mongo_doc["created_date"].isoformat()
        if "lastmodified_date" in mongo_doc and hasattr(mongo_doc["lastmodified_date"], "isoformat"):
            mongo_doc["lastmodified_date"] = mongo_doc["lastmodified_date"].isoformat()

        # 2. Also save to Django ORM via Serializer if needed
        try:
            serializer = FranchiseHomeCollectionSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
        except Exception as e:
            print("FranchiseHomeCollection serializer save log:", e)

        return Response(
            {"message": "Home collection request saved successfully", "data": mongo_doc},
            status=status.HTTP_201_CREATED
        )

    elif request.method == 'GET':
        try:
            franchise_id = request.GET.get('franchise_id')
            status_filter = request.GET.get('status')

            query = {}
            if franchise_id:
                query['franchise_id'] = franchise_id
            if status_filter and status_filter != 'all':
                query['status'] = status_filter

            # Build lookup dictionary for franchise names
            franchise_map = {}
            try:
                for f in franchise_register.find({}, {"franchise_id": 1, "franchise_name": 1, "franchiser_name": 1}):
                    fid = f.get("franchise_id")
                    fname = f.get("franchise_name") or f.get("franchiser_name") or ""
                    if fid:
                        franchise_map[fid] = fname
            except Exception:
                pass

            # Read from franchise_homecollection
            records = list(franchise_homecollection.find(query).sort("_id", -1))
            for item in records:
                item["_id"] = str(item["_id"])
                fid = item.get("franchise_id")
                if not item.get("franchise_name") and fid:
                    item["franchise_name"] = franchise_map.get(fid, "")
                if "date" in item and hasattr(item["date"], "isoformat"):
                    item["date"] = item["date"].isoformat()
                if "created_date" in item and hasattr(item["created_date"], "isoformat"):
                    item["created_date"] = item["created_date"].isoformat()
                if "lastmodified_date" in item and hasattr(item["lastmodified_date"], "isoformat"):
                    item["lastmodified_date"] = item["lastmodified_date"].isoformat()
                if "sample_accepted_time" in item and hasattr(item["sample_accepted_time"], "isoformat"):
                    item["sample_accepted_time"] = item["sample_accepted_time"].isoformat()

            return Response(records, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PUT', 'POST', 'PATCH'])
# @permission_classes([HasRolePermission])
def update_franchise_home_collection(request, collection_id):
    try:
        user_id = request.headers.get("auth-user-id", "system")
        data = request.data

        update_fields = {}
        for k in ['patient_name', 'address', 'phone', 'franchise_id', 'status', 'Remarks', 'accepted_by']:
            if k in data:
                update_fields[k] = data[k]

        update_fields['lastmodified_by'] = user_id
        update_fields['lastmodified_date'] = datetime.utcnow()

        if data.get('status') == 'Collected' and 'sample_accepted_time' not in data:
            update_fields['sample_accepted_time'] = datetime.utcnow()

        # Update in mongo
        query = {}
        try:
            query = {"_id": ObjectId(collection_id)}
        except Exception:
            query = {"_id": collection_id}

        franchise_homecollection.update_one(query, {"$set": update_fields})

        return Response({"message": "Home collection request updated successfully"}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([HasRolePermission])
def get_cancelled_bill_report(request):
    try:
        from_date = request.GET.get('from_date', '').strip()  # YYYY-MM-DD
        to_date = request.GET.get('to_date', '').strip()      # YYYY-MM-DD
        franchise_id = request.GET.get('franchise_id', '').strip()
        status_filter = request.GET.get('status', 'all').strip()

        query = {}
        if franchise_id:
            query["franchise_id"] = franchise_id

        documents = list(franchise_billing.find(query))

        # Collect unique patient_ids and franchise_ids for batch lookup
        all_patient_ids = list({str(d.get("patient_id", "")).strip() for d in documents if d.get("patient_id")})
        all_franchise_ids = list({str(d.get("franchise_id", "")).strip() for d in documents if d.get("franchise_id")})

        patient_map = {}
        for pid in all_patient_ids:
            if not pid:
                continue
            candidate_queries = [
                (franchise_patient, {"patient_id": pid}),
                (franchise_patient, {"patient_id": {"$regex": f"^{re.escape(pid)}$", "$options": "i"}}),
                (diag_db["franchise_patient"], {"patient_id": pid}),
                (diag_db["franchise_patient"], {"patient_id": {"$regex": f"^{re.escape(pid)}$", "$options": "i"}}),
                (diag_db["patient_patientdetails"], {"patient_id": pid}),
                (diag_db["backend_diagnostics_patient"], {"patient_id": pid}),
            ]
            for col_ref, q in candidate_queries:
                try:
                    p_doc = col_ref.find_one(q)
                    if p_doc:
                        pname = (
                            p_doc.get("patientname") or
                            p_doc.get("patient_name") or
                            p_doc.get("patientName") or
                            p_doc.get("name") or
                            f"{p_doc.get('title', '')} {p_doc.get('first_name', '')} {p_doc.get('last_name', '')}".strip() or
                            ""
                        )
                        if pname:
                            patient_map[pid] = pname
                            break
                except Exception:
                    pass

        print(f"--> [get_cancelled_bill_report] Loaded patient_map: {patient_map}")

        franchise_map = {}
        if all_franchise_ids:
            try:
                for f in franchise_register.find({"franchise_id": {"$in": all_franchise_ids}}):
                    fid_key = str(f.get("franchise_id", "")).strip()
                    fname = f.get("franchise_name") or f.get("name") or ""
                    if fid_key and fname:
                        franchise_map[fid_key] = fname

                missing_fids = [fid for fid in all_franchise_ids if fid not in franchise_map]
                if missing_fids:
                    for f in diag_db["franchise_franchise"].find({"franchise_id": {"$in": missing_fids}}):
                        fid_key = str(f.get("franchise_id", "")).strip()
                        fname = f.get("franchise_name") or f.get("name") or ""
                        if fid_key and fname:
                            franchise_map[fid_key] = fname
            except Exception as e:
                print("Error loading franchise names:", e)

        # Collect employee IDs for batch lookup from profile_collection
        all_emp_ids = set()
        for d in documents:
            if d.get("lastmodified_by"):
                all_emp_ids.add(str(d.get("lastmodified_by")).strip())
            for t in (d.get("testdetails") or []):
                if isinstance(t, dict):
                    if t.get("approved_by"):
                        all_emp_ids.add(str(t.get("approved_by")).strip())
                    if t.get("rejected_by"):
                        all_emp_ids.add(str(t.get("rejected_by")).strip())

        employee_map = {}
        for eid in all_emp_ids:
            if not eid:
                continue
            try:
                emp = (
                    profile_collection.find_one({"employeeId": eid}) or
                    profile_collection.find_one({"employeeId": {"$regex": f"^{re.escape(eid)}$", "$options": "i"}}) or
                    diag_db["backend_diagnostics_profile"].find_one({"employeeId": eid}) or
                    global_db["backend_diagnostics_profile"].find_one({"employeeId": eid})
                )
                if emp:
                    ename = emp.get("employeeName") or emp.get("name") or ""
                    if ename:
                        employee_map[eid] = ename
            except Exception as emp_err:
                print("Error loading employee profile:", emp_err)

        print(f"--> [get_cancelled_bill_report] Loaded employee_map: {employee_map}")

        report_rows = []
        total_mrp_cancelled = 0.0
        total_approved = 0
        total_rejected = 0
        total_pending = 0

        for doc in documents:
            raw = doc.get("testdetails", [])
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
                continue

            pid = str(doc.get("patient_id", "")).strip()
            barcode = str(doc.get("barcode", "")).strip()
            fid = str(doc.get("franchise_id", "")).strip()
            doctor = str(doc.get("referredDoctor", "")).strip()
            reg_date = doc.get("registrationDate")
            reg_date_str = reg_date.isoformat() if hasattr(reg_date, "isoformat") else (str(reg_date) if reg_date else "")

            # Resolve patient name and franchise name
            doc_pname = doc.get("patientname") or doc.get("patient_name") or doc.get("patientName") or doc.get("name") or ""
            patient_name = patient_map.get(pid) or doc_pname or ""
            franchise_name = franchise_map.get(fid, "")

            for test in testdetails:
                if not isinstance(test, dict):
                    continue

                test_st = test.get("status", "")
                if not test_st:
                    continue

                st_lower = str(test_st).strip().lower()
                is_cancelled_test = ("cancel" in st_lower) or (st_lower in ["rejected", "reject"])
                if not is_cancelled_test:
                    continue

                # Status filtering
                if status_filter and status_filter.lower() != 'all':
                    if status_filter.lower() not in st_lower:
                        continue

                # Determine effective date
                req_date = test.get("requested_date") or test.get("request_time") or doc.get("created_date")
                req_date_str = req_date.isoformat() if hasattr(req_date, "isoformat") else (str(req_date) if req_date else "")
                
                # Check date range filter (on requested_date or registrationDate)
                test_date_val = req_date_str[:10] if req_date_str else (reg_date_str[:10] if reg_date_str else "")
                
                if from_date and test_date_val and test_date_val < from_date:
                    continue
                if to_date and test_date_val and test_date_val > to_date:
                    continue

                mrp_val = float(test.get("MRP", 0))

                if "approved" in st_lower:
                    total_approved += 1
                    total_mrp_cancelled += mrp_val
                elif "reject" in st_lower:
                    total_rejected += 1
                else:
                    total_pending += 1

                app_time = test.get("Approveddatetime") or test.get("cancel_approved_date")
                app_time_str = app_time.isoformat() if hasattr(app_time, "isoformat") else (str(app_time) if app_time else "")
                rej_time = test.get("Rejecteddatetime") or test.get("rejected_date")
                rej_time_str = rej_time.isoformat() if hasattr(rej_time, "isoformat") else (str(rej_time) if rej_time else "")

                app_by_val = str(test.get("approved_by") or doc.get("lastmodified_by") or "").strip()
                app_by_display = employee_map.get(app_by_val, app_by_val)

                rej_by_val = str(test.get("rejected_by") or "").strip()
                rej_by_display = employee_map.get(rej_by_val, rej_by_val)

                req_by_id = str(test.get("requested_by") or fid or "").strip()
                req_by_name = franchise_map.get(req_by_id) or employee_map.get(req_by_id) or req_by_id

                report_rows.append({
                    "patient_id": pid,
                    "patient_name": patient_name,
                    "barcode": barcode,
                    "franchise_id": fid,
                    "franchise_name": franchise_name,
                    "referredDoctor": doctor,
                    "registrationDate": reg_date_str,
                    "test_id": test.get("test_id"),
                    "test_name": test.get("test_name"),
                    "MRP": mrp_val,
                    "status": test_st,
                    "requested_by": req_by_name,
                    "requested_by_id": req_by_id,
                    "requested_date": req_date_str,
                    "approved_by": app_by_display,
                    "approved_by_id": app_by_val,
                    "approved_date": app_time_str,
                    "rejected_by": rej_by_display,
                    "rejected_by_id": rej_by_val,
                    "rejected_date": rej_time_str,
                    "total": float(doc.get("total").to_decimal()) if (doc.get("total") is not None and isinstance(doc.get("total"), Decimal128)) else float(doc.get("total") or 0.0),
                    "netAmount": float(doc.get("netAmount").to_decimal()) if (doc.get("netAmount") is not None and isinstance(doc.get("netAmount"), Decimal128)) else float(doc.get("netAmount") or 0.0),
                    "paymentMode": doc.get("paymentMode", "")
                })

        report_rows.sort(key=lambda r: r.get("approved_date") or r.get("requested_date") or "", reverse=True)

        return Response({
            "success": True,
            "summary": {
                "total_records": len(report_rows),
                "total_approved": total_approved,
                "total_rejected": total_rejected,
                "total_pending": total_pending,
                "total_cancelled_amount": round(total_mrp_cancelled, 2)
            },
            "report": report_rows
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"success": False, "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

