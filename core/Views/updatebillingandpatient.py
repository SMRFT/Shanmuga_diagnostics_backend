from rest_framework.response import Response
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from django.views.decorators.csrf import csrf_exempt
from urllib.parse import quote_plus
from pymongo import MongoClient
from datetime import datetime
import json
from ..models import Patient
import certifi
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
import os
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv

load_dotenv()
@api_view(['PUT'])
@csrf_exempt
@permission_classes([ HasRoleAndDataPermission])
def update_patient(request, patient_id):
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client["Diagnostics"]
    collection = db["core_billing"]
    
    if request.method == "PUT":
        try:
            data = json.loads(request.body)
            
            # Include all fields, even empty strings (only exclude None values)
            update_data = {
                key: value for key, value in data.items() if value is not None
            }
            
            # Convert date string to datetime object if it exists
            if "date" in update_data:
                try:
                    update_data["date"] = datetime.fromisoformat(update_data["date"])
                except ValueError:
                    return JsonResponse({"error": "Invalid date format"}, status=400)
            
            # Always update lastmodified fields to ensure some change occurs
            update_data["lastmodified_by"] = "system"  # or get from request
            update_data["lastmodified_date"] = datetime.now()
            
            result = collection.update_one({"patient_id": patient_id}, {"$set": update_data})
            
            if result.matched_count == 0:
                return JsonResponse({"error": "Patient not found"}, status=404)
            
            if result.modified_count > 0:
                return JsonResponse({"message": "Patient updated successfully"}, status=200)
            else:
                return JsonResponse({"message": "No changes made"}, status=200)
                
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    
    return JsonResponse({"error": "Invalid request method"}, status=400)
@api_view(['GET'])
@permission_classes([ HasRoleAndDataPermission])
def get_patient_tests(request, patient_id, date):
    """Fetch test details for a given patient ID and date"""
    try:
        # Convert string date to a datetime object
        try:
            date_obj = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return Response({"error": "Invalid date format, use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        # Define start and end of the day to match any time on that date
        start_of_day = datetime(date_obj.year, date_obj.month, date_obj.day, 0, 0, 0)
        end_of_day = datetime(date_obj.year, date_obj.month, date_obj.day, 23, 59, 59)

        # Retrieve the patient test details using a date range
        patient = Patient.objects.filter(patient_id=patient_id, date__gte=start_of_day, date__lte=end_of_day).first()

        if not patient:
            return Response({"error": "No tests found for the given date and patient ID"}, status=status.HTTP_404_NOT_FOUND)

        # Ensure tests are in the correct format (list)
        tests = patient.testname  # Assuming `testname` is stored as JSON

        if isinstance(tests, str):
            try:
                tests = json.loads(tests)  # Convert JSON string to list
            except json.JSONDecodeError:
                return Response({"error": "Invalid test data format"}, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(tests, list):
            tests = []  # Default to an empty list if tests are not in a valid format

        # Parse payment method if stored as JSON string
        payment_method = patient.payment_method
        if isinstance(payment_method, str):
            try:
                payment_method = json.loads(payment_method)
            except json.JSONDecodeError:
                payment_method = {}

        # Prepare response data
        response_data = {
            "testname": tests,
            "discount": patient.discount,
            "payment_method": payment_method
        }

        return Response(response_data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from urllib.parse import quote_plus
from pymongo import MongoClient
import os

@api_view(['PATCH'])
@permission_classes([ HasRoleAndDataPermission])
def update_billing(request, patient_id):
    password = quote_plus('Smrft@2024')
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db['core_billing']

    patient = collection.find_one({"patient_id": patient_id})
    if not patient:
        return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)

    new_data = request.data

    if "totalAmount" in new_data:
        try:
            new_data["totalAmount"] = str(new_data["totalAmount"])
        except ValueError:
            return Response({"error": "Invalid totalAmount format"}, status=status.HTTP_400_BAD_REQUEST)

    if "credit_amount" in new_data:
        try:
            new_data["credit_amount"] = str(new_data["credit_amount"]) if new_data["credit_amount"] else '0'
        except ValueError:
            return Response({"error": "Invalid credit_amount format"}, status=status.HTTP_400_BAD_REQUEST)

    if "testname" in new_data and not isinstance(new_data["testname"], (list, str)):
        return Response({"error": "Invalid testname format"}, status=status.HTTP_400_BAD_REQUEST)

    collection.update_one({"patient_id": patient_id}, {"$set": new_data})

    updated_patient = collection.find_one({"patient_id": patient_id})
    if updated_patient and "_id" in updated_patient:
        updated_patient["_id"] = str(updated_patient["_id"])

    return Response(updated_patient, status=status.HTTP_200_OK)






@api_view(['PATCH'])
@permission_classes([ HasRoleAndDataPermission])
def update_credit_amount(request, patient_id):
    # MongoDB connection setup
    password = quote_plus('Smrft@2024')

        # MongoDB connection with TLS certificate
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))

    db = client.Lab  # Database name
    collection = db['labbackend_patient']
    # Retrieve the Django patient record, return 404 if not found
    patient = get_object_or_404(Patient, patient_id=patient_id)
    # Update only the credit amount if provided in the request data
    credit_amount = request.data.get("credit_amount")
    if credit_amount is not None:
        # Convert credit_amount to an integer for MongoDB consistency
        try:
            credit_amount = (credit_amount)
        except ValueError:
            return Response({"error": "Credit amount must be an integer."}, status=status.HTTP_400_BAD_REQUEST)
        # Update the credit amount in Django
        patient.credit_amount = credit_amount
        patient_id_str = str(patient_id)
        # Update MongoDB with credit_amount as an integer
        result = collection.update_one(
            {"patient_id": patient_id_str},
            {"$set": {"credit_amount": credit_amount}},
            upsert=False
        )
        # Check if the document was matched in MongoDB
        if result.matched_count == 0:
            return Response({"error": "Patient not found in MongoDB."}, status=status.HTTP_404_NOT_FOUND)
        # Save the updated credit amount in the Django model
        try:
            patient.save()
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "message": "Credit amount updated successfully",
            "credit_amount": credit_amount
        }, status=status.HTTP_200_OK)
    # Return an error if credit_amount was not provided in the request
    return Response({"error": "Credit amount is required."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@csrf_exempt
# @permission_classes([ HasRoleAndDataPermission])
def credit_amount_update(request, patient_id):    
    # MongoDB connection with TLS certificate
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Diagnostics
    collection = db['core_billing']
    if request.method == "PATCH":
        try:
            body = json.loads(request.body)
            # Convert incoming values
            credit_amount = str(body.get("credit_amount", "0"))  # Store as a string
            amount_paid = int(float(body.get("amount_paid", 0)))
            paid_date = body.get("paid_date", None)
            payment_method = body.get("payment_method", "N/A")  # Default to "N/A" if missing
            # Validate required fields
            if not credit_amount:
                return JsonResponse({"error": "Missing required field: credit_amount."}, status=400)
            # Fetch the patient document
            patient = collection.find_one({"patient_id": patient_id})
            if not patient:
                return JsonResponse({"error": "Patient not found."}, status=404)
            # Parse existing credit details safely
            credit_details = patient.get("credit_details", [])
            if isinstance(credit_details, str):
                try:
                    credit_details = json.loads(credit_details)
                except json.JSONDecodeError:
                    credit_details = []
            # Calculate the updated credit amount
            current_credit_amount = int(float(patient.get("credit_amount", 0)))
            updated_credit_amount = str(current_credit_amount - amount_paid)  # Store as string
            # Append the new entry to `credit_details`
            credit_details.append({
                "credit_amount": credit_amount,  # Stored as a string
                "amount_paid": amount_paid,
                "paid_date": paid_date,
                "payment_method": payment_method,  # Store payment method
                "remaining_amount": updated_credit_amount  # Stored as a string
            })
            # Update the database
            collection.update_one(
                {"patient_id": patient_id},
                {
                    "$set": {
                        "credit_amount": updated_credit_amount,  # Store as a string
                        "credit_details": json.dumps(credit_details)  # Store as JSON string
                    }
                }
            )
            return JsonResponse({
                "message": "Credit amount updated successfully.",
                "credit_details": credit_details
            })
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON format."}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    else:
        return JsonResponse({"error": "Invalid request method. Only PATCH is allowed."}, status=405)