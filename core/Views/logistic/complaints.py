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

from datetime import datetime, time, timedelta

logger = logging.getLogger(__name__)


@api_view(['GET', 'POST', 'PATCH'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def customer_complaints(request):
    """
    GET   /customer_complaints/                                -> list all complaints, ordered by complaint_id (ascending)
    GET   /customer_complaints/?status=pending                 -> filter by status (optional)
    GET   /customer_complaints/?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
                                                                 -> filter by created_date range (optional)
                                                                    (from_date alone = created_date >= start of from_date,
                                                                     to_date alone   = created_date <  start of the day AFTER to_date)
                                                                    NOTE: uses plain __gte/__lt on created_date rather than
                                                                    the __date lookup, because djongo/Mongo's DB backend
                                                                    doesn't implement datetime_cast_date_sql() (that's a
                                                                    SQL-only cast) — so __date__gte/__date__lte raise
                                                                    "subclasses of BaseDatabaseOperations may require a
                                                                    datetime_cast_date_sql() method." on Mongo.
    POST  /customer_complaints/                                 -> create a new complaint
                                                                    body: { labcode, issuetype, comments, assignedby }
                                                                    status is always forced to 'pending' on create;
                                                                    completion_comments starts out null.
    PATCH /customer_complaints/                                 -> mark a complaint completed
                                                                    body: { complaint_id, completion_comments }
                                                                    sets status -> 'completed'
    """
    try:
        if request.method == 'GET':
            # Ascending by complaint_id so the table reads ID 1, 2, 3... in order.
            queryset = CustomerComplaint.objects.all().order_by('complaint_id')

            status_filter = request.query_params.get('status')
            if status_filter:
                queryset = queryset.filter(status=status_filter)

            # Date range filter — used by the table's From/To date picker.
            # Built as explicit datetime bounds (start of from_date, start
            # of the day AFTER to_date) so we only ever need __gte/__lt on
            # the raw created_date field — Mongo-safe, no SQL date casting.
            from_date = request.query_params.get('from_date')
            to_date = request.query_params.get('to_date')

            if from_date:
                try:
                    from_day = datetime.strptime(from_date, '%Y-%m-%d').date()
                    start_dt = timezone.make_aware(datetime.combine(from_day, time.min))
                    queryset = queryset.filter(created_date__gte=start_dt)
                except ValueError:
                    return Response(
                        {'error': 'from_date must be in YYYY-MM-DD format'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            if to_date:
                try:
                    to_day = datetime.strptime(to_date, '%Y-%m-%d').date()
                    end_dt = timezone.make_aware(datetime.combine(to_day + timedelta(days=1), time.min))
                    queryset = queryset.filter(created_date__lt=end_dt)
                except ValueError:
                    return Response(
                        {'error': 'to_date must be in YYYY-MM-DD format'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # NOTE: response shape changed from a bare JSON array to a
            # paginated object ({"results": [...], total_count, ...}) to
            # bound the payload as complaints grow; update any frontend
            # caller that expected a raw array here.
            page_obj, page_meta = paginate_queryset(queryset, request)
            serializer = CustomerComplaintSerializer(page_obj, many=True)
            return Response({'results': serializer.data, **page_meta}, status=status.HTTP_200_OK)

        if request.method == 'POST':
            data = request.data.copy()

            employee_id = request.data.get("auth-user-id")

            required_fields = ['labcode', 'issuetype', 'comments', 'assignedby']
            missing = [f for f in required_fields if not data.get(f)]
            if missing:
                return Response(
                    {'error': f"Missing required field(s): {', '.join(missing)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Every new complaint starts pending with no completion comments,
            # regardless of what (if anything) the client sent for these.
            data['status'] = 'pending'
            data['completion_comments'] = None
            data['created_by'] = employee_id

            serializer = CustomerComplaintSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)

            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # PATCH - mark a complaint as completed, with mandatory comments
        complaint_id = request.data.get('complaint_id')
        completion_comments = request.data.get('completion_comments')

        if not complaint_id:
            return Response(
                {'error': 'complaint_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not completion_comments:
            return Response(
                {'error': 'completion_comments is required to mark a complaint as completed'},
                status=status.HTTP_400_BAD_REQUEST
            )

        employee_id = request.data.get("auth-user-id")

        updated_count = CustomerComplaint.objects.filter(complaint_id=complaint_id).update(
            completion_comments=completion_comments,
            status='completed',
            lastmodified_by=employee_id,
            lastmodified_date=timezone.now(),
        )

        if not updated_count:
            return Response(
                {'error': f'Complaint {complaint_id} not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        complaint = CustomerComplaint.objects.get(complaint_id=complaint_id)
        serializer = CustomerComplaintSerializer(complaint)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
