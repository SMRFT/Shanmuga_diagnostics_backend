from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from core.mongo_client import get_client
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from core.models import TestValue, MBTestValue
load_dotenv()
logger = logging.getLogger(__name__)
import gridfs
import re
from core.pagination import paginate_queryset

# core/Views/franchise/batches.py
#
# Franchise batch views: listing pending (not-yet-received) batch
# generation records, and marking a batch as received/rejected.
#
# Split out of the original core/Views/franchise.py — see
# core/Views/franchise/__init__.py for the full re-export list.


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_batch_generation_data(request):
    """
    Get all batch generation data where received=false with optional date filtering
    """
    if request.method == "GET":
        client = None
        try:
            # Connect to MongoDB
            client = get_client()
            db = client.franchise  # Database name
            collection = db.franchise_batch  # Collection name

            # Build query with date filtering
            query = {"received": False}

            # Get date parameters from request
            from_date = request.GET.get('from_date')
            to_date = request.GET.get('to_date')

            # Add date filtering if provided
            if from_date or to_date:
                date_query = {}

                if from_date:
                    # Parse from_date and set time to start of day (00:00:00)
                    from_datetime = datetime.strptime(from_date, '%Y-%m-%d')
                    date_query['$gte'] = from_datetime

                if to_date:
                    # Parse to_date and set time to end of day (23:59:59)
                    to_datetime = datetime.strptime(to_date, '%Y-%m-%d')
                    to_datetime = to_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
                    date_query['$lte'] = to_datetime

                # Add date filter to query - FIXED: Use 'created_date' instead of 'createdDate'
                if date_query:
                    query['created_date'] = date_query

            # Add case-insensitive substring search across key fields if provided
            search = request.GET.get('search', '').strip()
            if search:
                pattern = re.escape(search)
                query["$or"] = [
                    {"batch_number": {"$regex": pattern, "$options": "i"}},
                    {"franchise_id": {"$regex": pattern, "$options": "i"}},
                    {"shipment_from": {"$regex": pattern, "$options": "i"}},
                    {"shipment_to": {"$regex": pattern, "$options": "i"}},
                    {"created_by": {"$regex": pattern, "$options": "i"}},
                    {"batch_details": {"$regex": pattern, "$options": "i"}},
                ]

            # Log the query for debugging
            logger.info(f"MongoDB query: {query}")

            # Fetch documents with the built query
            batches = list(collection.find(query))

            # Process the data
            processed_data = []
            for batch in batches:
                # Convert ObjectId to string for JSON serialization
                batch['_id'] = str(batch['_id'])

                # Parse JSON strings if they exist - FIXED: Use correct field names
                if 'batch_details' in batch and isinstance(batch['batch_details'], str):
                    try:
                        batch['batch_details'] = json.loads(batch['batch_details'])
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse batch_details for batch {batch['_id']}")
                        batch['batch_details'] = {}

                if 'specimen_count' in batch and isinstance(batch['specimen_count'], str):
                    try:
                        batch['specimen_count'] = json.loads(batch['specimen_count'])
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse specimen_count for batch {batch['_id']}")
                        batch['specimen_count'] = []

                # Convert datetime objects to ISO format strings - FIXED: Use correct field names
                if 'created_date' in batch:
                    batch['created_date'] = batch['created_date'].isoformat() if batch['created_date'] else None
                if 'lastmodified_date' in batch:
                    batch['lastmodified_date'] = batch['lastmodified_date'].isoformat() if batch['lastmodified_date'] else None

                processed_data.append(batch)

            # NOTE: "data" is now paginated (page/limit query params) instead
            # of returning every pending batch at once — "count" keeps its
            # original meaning (total matching records, not just this page);
            # total_pages/current_page/total_count from page_meta are merged
            # in for pagination.
            total_count = len(processed_data)
            page_obj, page_meta = paginate_queryset(processed_data, request)

            return JsonResponse({
                "status": "success",
                "data": list(page_obj),
                "count": total_count,
                "filters": {
                    "from_date": from_date,
                    "to_date": to_date
                },
                **page_meta
            }, safe=False)

        except ValueError as e:
            logger.error(f"Date parsing error: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": f"Invalid date format. Use YYYY-MM-DD format: {str(e)}"
            }, status=400)

        except Exception as e:
            logger.error(f"Error fetching batch generation data: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": f"Database connection error: {str(e)}"
            }, status=500)

        finally:
            # Note: `client` is the shared, pooled MongoClient — do not close it here.
            pass

@api_view(['PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def update_batch_received_status(request, batch_no):
    """
    Update the received status and optionally remarks for a specific batch using batch_no
    """
    # MongoDB connection details
    client = get_client()
    db = client.franchise
    collection = db.franchise_batch

    try:
        # Parse request body
        try:
            if hasattr(request, 'data'):
                body = request.data
            else:
                body = json.loads(request.body)
            received_status = body.get('received', True)
            remarks = body.get('remarks', None)
            employee_id = body.get('auth-user-id')
        except json.JSONDecodeError:
            received_status = True
            remarks = None

        # Validate remarks for rejection
        if received_status is False and (remarks is None or remarks.strip() == ""):
            return JsonResponse({
                "status": "error",
                "message": "Remarks are required when rejecting a batch"
            }, status=400)
        # Validate employee_id
        if employee_id is None:
            return JsonResponse({
                "status": "error",
                "message": "auth-user-id is required"
            }, status=400)
       # Use employee_id for lastmodified_by
        lastmodified_by = employee_id

        # Build update data
        update_data = {
            "$set": {
                "received": received_status,
                "lastmodified_date": datetime.now(),
                "lastmodified_by": lastmodified_by
            }
        }

        # Update remarks only if provided
        if remarks is not None:
            update_data["$set"]["remarks"] = remarks.strip()

        # Update document in MongoDB
        result = collection.update_one(
            {"batch_number": str(batch_no)},
            update_data
        )

        logger.info(f"Update attempt for batch {batch_no}: matched={result.matched_count}, modified={result.modified_count}, payload={body}")

        if result.matched_count == 0:
            return JsonResponse({
                "status": "error",
                "message": f"Batch with batch_number '{batch_no}' not found"
            }, status=404)

        if result.modified_count == 0:
            return JsonResponse({
                "status": "info",
                "message": "No changes made to the batch (already in desired state)",
                "batch_no": batch_no
            }, status=200)

        return JsonResponse({
            "status": "success",
            "message": "Batch status updated successfully",
            "batch_no": batch_no,
            "received": received_status,
            "remarks": remarks
        }, status=200)

    except Exception as e:
        logger.error(f"Error updating batch status for batch {batch_no}: {str(e)}")
        return JsonResponse({
            "status": "error",
            "message": f"Database update error: {str(e)}"
        }, status=500)

    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass
