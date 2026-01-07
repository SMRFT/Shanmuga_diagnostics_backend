from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from pymongo import MongoClient
from datetime import datetime, timedelta
import os
import traceback

from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv

load_dotenv()


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def preetham_hospital_report(request):
    """
    Report for PREETHAM HOSPITAL B2B patients
    - Filters by date range
    - Matches patient_id with core_patient
    - Gets barcode from core_barcode
    """

    try:
        # ------------------ MongoDB Setup ------------------
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics

        billing_collection = db["core_billing"]
        patient_collection = db["core_patient"]
        barcode_collection = db["core_barcodetestdetails"]

        # ------------------ Date Filters ------------------
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")

        if not from_date or not to_date:
            return JsonResponse(
                {"error": "Both 'from_date' and 'to_date' are required (YYYY-MM-DD)"},
                status=400
            )

        try:
            from_date_parsed = datetime.strptime(from_date, "%Y-%m-%d")
            to_date_parsed = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            return JsonResponse(
                {"error": "Invalid date format. Use YYYY-MM-DD"},
                status=400
            )

        # ------------------ Billing Query ------------------
        billing_query = {
            "date": {"$gte": from_date_parsed, "$lt": to_date_parsed},
            "B2B": "PREETHAM HOSPITAL"
        }

        billing_records = list(billing_collection.find(billing_query))

        if not billing_records:
            return JsonResponse([], safe=False)

        # ------------------ Collect Patient IDs ------------------
        patient_ids = list({
            record.get("patient_id")
            for record in billing_records
            if record.get("patient_id")
        })

        # ------------------ Fetch Patients ------------------
        patients = list(
            patient_collection.find(
                {"patient_id": {"$in": patient_ids}},
                {"_id": 0}
            )
        )

        patient_map = {
            p.get("patient_id"): p
            for p in patients
        }

        # ------------------ Fetch Barcodes ------------------
        barcodes = list(
            barcode_collection.find(
                {"patient_id": {"$in": patient_ids}},
                {"_id": 0}
            )
        )

        barcode_map = {
            b.get("patient_id"): b.get("barcode")
            for b in barcodes
        }

        # ------------------ Format Response ------------------
        formatted_data = []

        for record in billing_records:
            patient_id = record.get("patient_id", "N/A")

            patient = patient_map.get(patient_id, {})
            barcode = barcode_map.get(patient_id, "N/A")

            # Amount parsing
            try:
                total_amount = int(float(record.get("totalAmount", 0) or 0))
            except:
                total_amount = 0

            try:
                discount = int(float(record.get("discount", 0) or 0))
            except:
                discount = 0

            # Test names
            test_names_str = record.get("test_names", "")
            test_list = [
                t.strip() for t in test_names_str.split(",") if t.strip()
            ] if test_names_str else []

            # Date formatting
            bill_date = (
                record["date"].strftime("%Y-%m-%d")
                if record.get("date") else "N/A"
            )

            formatted_data.append({
                "date": bill_date,
                "patient_id": patient_id,
                "patient_name": patient.get("patientname", "N/A"),
                "age": patient.get("age", "N/A"),
                "gender": patient.get("gender", "N/A"),
                "phone": patient.get("phone", "N/A"),
                "bill_no": record.get("bill_no", "N/A"),
                "barcode": barcode,
                "test_names": test_list,
                "no_of_tests": len(test_list),
                "total_amount": total_amount,
                "discount": discount,
                "status": record.get("status", "Registered"),
                "refby": record.get("refby", "N/A"),
                "sample_collector": record.get("sample_collector", "N/A"),
            })

        return JsonResponse(formatted_data, safe=False)

    except Exception as e:
        print("Error in preetham_hospital_report:", str(e))
        print(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)