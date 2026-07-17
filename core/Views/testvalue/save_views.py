from rest_framework.response import Response
from django.http import JsonResponse , HttpResponse
from datetime import datetime
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework import  status
from urllib.parse import quote_plus
from core.mongo_client import get_client
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from django.utils import timezone  # Import Django's timezone module
import re
from django.core.mail import EmailMessage
from django.conf import settings
from django.utils.timezone import make_aware
from datetime import datetime, date  # Import `date` separately
import pytz
from rest_framework.views import APIView
import traceback
from django.conf import settings  # To access the settings for DEFAULT_FROM_EMAIL
import json
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ...models import Patient,Hmssamplestatus
from ...models import SampleStatus
from ...models import TestValue
from ...models import SampleStatus
from ...models import BarcodeTestDetails
from django.http import JsonResponse
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from ...models import SampleStatus, TestValue, Hmssamplestatus, Hmsbarcode, HmspatientBilling
from ...serializers import SampleStatusSerializer
from ...serializers import TestValueSerializer
import os
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv
from core.pagination import paginate_queryset
load_dotenv()

logger = logging.getLogger(__name__)


def update_processing_status(barcode, test_code, device_id, latest_record_id_str):
    """
    Helper function to update processing status:
    - Mark the latest record as 'Completed'
    - Mark older records as 'Ignored'
    """
    client = None
    try:
        client = get_client()
        db = client.Diagnostics
        interface_testvalue_collection = db.interface_testvalue

        # Convert string ID back to ObjectId
        try:
            latest_record_id = ObjectId(latest_record_id_str)
        except Exception as e:
            logger.error(f"Invalid ObjectId format: {latest_record_id_str}, Error: {str(e)}")
            return False

        # Debug logging
        logger.debug(f"DEBUG: Updating processing status for:")
        logger.debug(f"  Barcode: {barcode}")
        logger.debug(f"  TestCode: {test_code}")
        logger.debug(f"  DeviceID: {device_id}")
        logger.debug(f"  RecordID: {latest_record_id_str}")

        # First, verify the record exists
        latest_record = interface_testvalue_collection.find_one({"_id": latest_record_id})
        if not latest_record:
            logger.debug(f"Record with ID {latest_record_id_str} not found")
            return False

        # Check current processing status
        current_status = latest_record.get('processingstatus', 'N/A')
        logger.debug(f"Current processing status: {current_status}")

        # Skip if already processed
        if current_status in ['Completed', 'Ignored']:
            logger.debug(f"Record already processed with status: {current_status}")
            return True

        # Update the latest record to 'Completed'
        update_result = interface_testvalue_collection.update_one(
            {
                "_id": latest_record_id,
                "Barcode": barcode,
                "TestCode": test_code,
                "DeviceID": device_id
            },
            {
                "$set": {
                    "processingstatus": "Completed",
                    "processesdate": datetime.utcnow()
                }
            }
        )

        if update_result.modified_count > 0:
            logger.debug(f"Successfully updated latest record to Completed")
        else:
            logger.debug(f"Failed to update latest record - criteria may not match")
            return False

        # Update older records for the same barcode, test_code, and device_id to 'Ignored'
        ignore_query = {
            "Barcode": barcode,
            "TestCode": test_code,
            "DeviceID": device_id,
            "_id": {"$ne": latest_record_id},
            "processingstatus": {"$nin": ["Ignored", "Completed"]}
        }

        count_to_ignore = interface_testvalue_collection.count_documents(ignore_query)
        logger.debug(f"Records to be marked as Ignored: {count_to_ignore}")

        if count_to_ignore > 0:
            ignore_result = interface_testvalue_collection.update_many(
                ignore_query,
                {
                    "$set": {
                        "processingstatus": "Ignored",
                        "processesdate": datetime.utcnow()
                    }
                }
            )
            logger.debug(f"Updated {ignore_result.modified_count} older records to Ignored")

        return True

    except Exception as e:
        logger.error(f"Error updating processing status for barcode {barcode}, test_code {test_code}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass


@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
def save_test_value(request):
    if request.method == 'POST':
        payload = request.data
        employee_id = payload.get('auth-user-id')
        try:
            test_details_json = payload.get("testdetails", [])
            barcode           = payload.get("barcode")
            locationId        = payload.get("locationId")
            processed_records = payload.get("processed_records", [])

            if not isinstance(test_details_json, list) or not test_details_json:
                return Response(
                    {"error": "Invalid test details format"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            for test in test_details_json:
                if not test.get('test_id'):
                    return Response(
                        {"error": "Missing test_id in test details"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # ── Duplicate check ───────────────────────────────────────────────
            # For each test_id in the incoming payload, check if a TestValue
            # record already exists for this barcode with that test_id.
            #
            # Save is BLOCKED if the existing record has:
            #   - approve = False  (pending, not yet rerun)
            #   - approve = True   (already approved)
            #
            # Save is ALLOWED only when:
            #   - No existing record at all, OR
            #   - Existing record has approve=False AND rerun=True  (rerun in progress)
            # ─────────────────────────────────────────────────────────────────

            blocked_tests = []

            # Fetch all existing TestValue records for this barcode at once
            existing_records = TestValue.objects.filter(barcode=barcode)

            for incoming_test in test_details_json:
                incoming_test_id = incoming_test.get('test_id')

                for existing_record in existing_records:
                    # testdetails is stored as a JSON string or list
                    try:
                        existing_testdetails = (
                            json.loads(existing_record.testdetails)
                            if isinstance(existing_record.testdetails, str)
                            else existing_record.testdetails
                        )
                    except (json.JSONDecodeError, TypeError):
                        existing_testdetails = []

                    if not isinstance(existing_testdetails, list):
                        continue

                    for existing_test in existing_testdetails:
                        if str(existing_test.get('test_id')) != str(incoming_test_id):
                            continue

                        approve = existing_test.get('approve', False)
                        rerun   = existing_test.get('rerun',   False)
                        # Block: approve=True (already approved — cannot overwrite)
                        if approve is True:
                            blocked_tests.append({
                                'test_id': incoming_test_id,
                                'reason': 'already approved'
                            })
                            break

                        # Block: approve=False AND rerun=False
                        # (data exists but not flagged for rerun — duplicate entry)
                        if approve is False and rerun is False:
                            blocked_tests.append({
                                'test_id': incoming_test_id,
                                'reason': 'already exists and not flagged for rerun'
                            })
                            break

                        # Block: approve=None AND rerun=False
                        # (saved but pending manual approval — not flagged for rerun)
                        if approve is None and rerun is False:
                            blocked_tests.append({
                                'test_id': incoming_test_id,
                                'reason': 'already exists and not flagged for rerun'
                            })
                            break

                        # Allow: approve=False AND rerun=True → fall through (rerun scenario)

            if blocked_tests:
                reasons = "; ".join(
                    f"test_id {b['test_id']}: {b['reason']}" for b in blocked_tests
                )
                return Response(
                    {
                        "error": f"Save blocked for the following tests — {reasons}",
                        "blocked_tests": blocked_tests,
                    },
                    status=status.HTTP_409_CONFLICT
                )

            # ── All checks passed — inject approve_by and save ────────────────
            AUTO_APPROVE_BY = "60463"

            for test in test_details_json:
                if test.get('approve') is True:
                    test['approve_by'] = AUTO_APPROVE_BY

            test_value_record = TestValue.objects.create(
                created_by=employee_id,
                date=payload.get('date'),
                barcode=barcode,
                locationId=locationId,
                testdetails=test_details_json,
)
            # ── Update processing status for interface records ─────────────────
            update_success_count = 0
            update_errors        = []

            for record in processed_records:
                try:
                    success = update_processing_status(
                        record['barcode'],
                        record['test_code'],
                        record['device_id'],
                        record['record_id']
                    )
                    if success:
                        update_success_count += 1
                    else:
                        update_errors.append(f"Failed to update record {record['record_id']}")
                except Exception as e:
                    error_msg = f"Error updating record {record['record_id']}: {str(e)}"
                    logger.error(error_msg)
                    update_errors.append(error_msg)

            response_message = "Test details saved successfully."
            if processed_records:
                response_message += (
                    f" Updated processing status for "
                    f"{update_success_count}/{len(processed_records)} records."
                )
                if update_errors:
                    response_message += f" Errors: {'; '.join(update_errors[:3])}"

            return Response(
                {
                    "message": response_message,
                    "updated_records": update_success_count,
                    "total_records":   len(processed_records),
                    "errors":          update_errors if update_errors else None,
                },
                status=status.HTTP_201_CREATED
            )

        except Patient.DoesNotExist:
            logger.debug("DEBUG: Patient not found")
            return Response(
                {"error": "Patient not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"DEBUG: POST error: {str(e)}")
            return Response(
                {"error": f"An error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
