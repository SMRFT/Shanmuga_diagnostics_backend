import os
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q, Count, Case, When, IntegerField
from pymongo import MongoClient
import certifi
from gridfs import GridFS
from datetime import datetime, timedelta
from django.core.files.storage import default_storage
from pymongo import MongoClient
import certifi
from gridfs import GridFS

from collections import defaultdict
from core.utils import get_employee_name

from ...models import Logistics, Billing, CustomerComplaint
from ...serializers import LogisticsSerializer, BillingSerializer, CustomerComplaintSerializer
from core.pagination import paginate_queryset


from ..dbcollection import profile_collection, B2B_ROLES, B2B_LAB_Roles, BIO_CHESMISTRY_ROLES

#auth
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission
import os
from dotenv import load_dotenv

from bson import ObjectId
from bson.errors import InvalidId
from django.http import HttpResponse

from ...models import Busfare
from ...serializers import BusfareSerializer

from .common import _gridfs

logger = logging.getLogger(__name__)


@api_view(['GET', 'POST', 'PATCH'])
@permission_classes([HasRoleAndDataPermission])
def bus_fare(request):
    """
    GET   /bus_fare/                                -> list all bus fare entries
    GET   /bus_fare/?date=YYYY-MM-DD                 -> filter by exact date (optional)
    GET   /bus_fare/?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
                                                      -> filter by date range (optional)
                                                         (from_date alone = date >= from_date,
                                                          to_date alone   = date <= to_date)
    GET   /bus_fare/?collectedby=NAME                -> filter by collectedby (optional)
    POST  /bus_fare/                  -> create a new bus fare entry
                                          - image field: bustphoto (optional, stored in GridFS)
                                          Frontend retrieves the image via:
                                          GET /bus_fare/photo/<file_id>/
                                          Response shape: { "message": str, "data": {...} }
                                          on success (201), or
                                          { "message": str, "errors": {...} } on
                                          validation failure (400).
    PATCH /bus_fare/                  -> mark an entry as picked up
                                          body: { "busfare_id": <id>, "pickedupby": "<employeeId>" }
                                          Response shape: { "message": str, "data": {...} }
                                          on success (200).
    """
    try:
        if request.method == 'GET':
            queryset = Busfare.objects.all().order_by('-date', '-busfare_id')

            date_filter = request.query_params.get('date')
            if date_filter:
                queryset = queryset.filter(date=date_filter)

            # Date range filter — used by the table's From/To date picker.
            # Independent of the exact `date` filter above so either can be
            # used on its own without interfering with the other.
            from_date = request.query_params.get('from_date')
            to_date = request.query_params.get('to_date')
            if from_date and to_date:
                queryset = queryset.filter(date__gte=from_date, date__lte=to_date)
            elif from_date:
                queryset = queryset.filter(date__gte=from_date)
            elif to_date:
                queryset = queryset.filter(date__lte=to_date)

            collectedby_filter = request.query_params.get('collectedby')
            if collectedby_filter:
                queryset = queryset.filter(collectedby=collectedby_filter)

            # NOTE: response shape changed from a bare JSON array to a
            # paginated object ({"results": [...], total_count, ...}) to
            # bound the payload as Busfare entries grow; update any
            # frontend caller that expected a raw array here.
            page_obj, page_meta = paginate_queryset(queryset, request)
            serializer = BusfareSerializer(page_obj, many=True)
            return Response({'results': serializer.data, **page_meta}, status=status.HTTP_200_OK)

        if request.method == 'POST':
            # NOTE: for multipart requests, DRF's request.data is a QueryDict
            # that merges POST fields AND uploaded files together — so a plain
            # request.data.copy() triggers QueryDict.__deepcopy__(), which
            # tries to pickle every value, including the raw uploaded file
            # object (wrapping an unpicklable _io.BufferedRandom/_io.BytesIO).
            # That crash mid-deepcopy is also what leaves Django's temp file
            # cleanup in a bad state (the noisy but harmless
            # "TemporaryFile object has no attribute 'close_called'" error
            # you see logged right after).
            #
            # Fix: exclude file keys before copying, since the file itself is
            # already read out separately below via request.FILES.
            file_keys = set(request.FILES.keys())
            data = {key: value for key, value in request.data.items() if key not in file_keys}

            employee_id = request.data.get("auth-user-id")
            data['created_by'] = employee_id
            data['created_date'] = timezone.now()

            # Defensive cleanup: multipart/form-data requests can hand us
            # amount as something other than a clean decimal string (stray
            # whitespace, thousands separators, etc). Normalize it so
            # DecimalField doesn't choke on it.
            raw_amount = data.get('amount')
            if raw_amount is not None:
                data['amount'] = str(raw_amount).strip().replace(',', '')

            # ── Upload photo to GridFS (if provided) ──────────────────────
            uploaded_file = request.FILES.get('bustphoto')
            logger.debug(f"bus_fare POST request.FILES keys={list(request.FILES.keys())} bustphoto={uploaded_file!r}")
            if uploaded_file:
                client, fs = _gridfs()
                try:
                    file_id = fs.put(
                        uploaded_file.read(),
                        filename=uploaded_file.name,
                        content_type=uploaded_file.content_type,
                    )
                    data['bustphoto'] = str(file_id)  # ObjectId -> plain string
                    logger.info(f"bus_fare POST photo stored in GridFS, file_id={file_id}")
                except Exception as upload_err:
                    logger.error(f"bus_fare POST photo upload FAILED: {upload_err}")
                    return Response(
                        {'message': f'Image upload failed: {upload_err}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
                finally:
                    client.close()
            else:
                logger.debug("bus_fare POST no bustphoto file present in request.FILES")
                data.pop('bustphoto', None)

            serializer = BusfareSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {
                        'message': 'Bus fare entry saved successfully.',
                        'data': serializer.data,
                    },
                    status=status.HTTP_201_CREATED
                )

            return Response(
                {
                    'message': 'Failed to save bus fare entry. Please check the highlighted fields.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # PATCH - mark a bus fare entry as picked up
        busfare_id = request.data.get('busfare_id')
        if not busfare_id:
            return Response(
                {'message': 'busfare_id is required to update an entry.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        pickedupby = request.data.get('pickedupby')
        if not pickedupby:
            return Response(
                {'message': 'pickedupby is required to mark this entry as picked up.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        employee_id = request.data.get("auth-user-id")

        updated_count = Busfare.objects.filter(busfare_id=busfare_id).update(
            pickedupby=pickedupby,
            lastmodified_by=employee_id,
            lastmodified_date=timezone.now(),
        )

        if not updated_count:
            return Response(
                {'message': f'No bus fare entry found with busfare_id {busfare_id}. It may have already been removed.'},
                status=status.HTTP_404_NOT_FOUND
            )

        busfare = Busfare.objects.get(busfare_id=busfare_id)
        serializer = BusfareSerializer(busfare)
        return Response(
            {
                'message': 'Pickup details updated successfully.',
                'data': serializer.data,
            },
            status=status.HTTP_200_OK
        )

    except Exception as e:
        return Response(
            {'message': f'Something went wrong: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
def bus_fare_photo(request):
    """
    GET /bus_fare_photo/?file_id=<file_id> -> streams a GridFS-stored
    bus fare photo back so the frontend can display it directly in an
    <img src="..."> tag.
    """
    file_id = request.query_params.get('file_id')
    if not file_id:
        return Response({'error': 'file_id is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        client, fs = _gridfs()
        try:
            grid_file = fs.get(ObjectId(file_id))
            content = grid_file.read()
            content_type = grid_file.content_type or 'image/jpeg'
        finally:
            client.close()

        return HttpResponse(content, content_type=content_type)

    except (InvalidId, GridFS.NoFile):
        return Response({'error': 'Photo not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
