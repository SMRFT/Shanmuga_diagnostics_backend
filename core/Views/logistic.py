import os
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

from ..models import Logistics, Billing, CustomerComplaint
from ..serializers import LogisticsSerializer, BillingSerializer,CustomerComplaintSerializer


from .dbcollection import profile_collection, B2B_ROLES, B2B_LAB_Roles, BIO_CHESMISTRY_ROLES

#auth
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from pyauth.auth import HasRoleAndDataPermission
import os
from dotenv import load_dotenv


@api_view(['GET', 'POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def create_logistics(request):
    try:
        if request.method == 'GET':
            queryset = Logistics.objects.all().order_by('-created_date')

            # ── Date range filtering ──
            start_date = request.query_params.get('start_date')
            end_date   = request.query_params.get('end_date')

            if start_date:
                queryset = queryset.filter(date__gte=start_date)
            if end_date:
                queryset = queryset.filter(date__lte=end_date)

            serializer = LogisticsSerializer(queryset, many=True)
            return Response(
                {'count': queryset.count(), 'results': serializer.data},
                status=status.HTTP_200_OK
            )

        if request.method == 'POST':
            data = request.data.copy()
            employee_id = request.data.get("auth-user-id")

            data['status']       = 'Assigned'
            data['created_by']   = employee_id
            data['created_date'] = timezone.now()

            if not data.get('date'):
                data['date'] = timezone.now().date()

            if not data.get('sampleordertime'):
                data['sampleordertime'] = timezone.now()

            serializer = LogisticsSerializer(data=data)

            if serializer.is_valid():
                serializer.save()
                return Response(
                    {'message': 'Task assigned successfully', 'data': serializer.data},
                    status=status.HTTP_201_CREATED
                )

            return Response(
                {'error': 'Validation failed', 'details': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
# @permission_classes([HasRoleAndDataPermission])
def logistics_by_collector(request):
    """
    GET: Fetch logistics tasks for a specific sample collector
    Query params: sample_collector (required)
    
    Returns:
    - today_tasks: Tasks assigned for current date (all statuses)
    - pending_tasks: Tasks from previous dates that are still in 'Assigned' status (grouped by date)
    """
    collector = request.query_params.get('sample_collector')

    if not collector:
        return Response(
            {'error': 'sample_collector is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Get current date (date only, no time)
    today = timezone.now().date()

    # Today's tasks (all statuses for current date)
    today_tasks = Logistics.objects.filter(
        sample_collector__iexact=collector,
        date=today
    ).order_by('-sampleordertime')

    # Pending tasks from previous dates (only 'Assigned' status)
    pending_tasks = Logistics.objects.filter(
        sample_collector__iexact=collector,
        date__lt=today,
        status='Assigned'
    ).order_by('-date', '-sampleordertime')

    # Group pending tasks by date
    pending_by_date = defaultdict(list)
    for task in pending_tasks:
        task_data = LogisticsSerializer(task).data
        pending_by_date[str(task.date)].append(task_data)

    # Convert to list format for easier frontend handling
    pending_grouped = [
        {
            'date': date,
            'tasks': tasks
        }
        for date, tasks in sorted(pending_by_date.items(), reverse=True)
    ]

    today_serializer = LogisticsSerializer(today_tasks, many=True)

    return Response(
        {
            'today_count': today_tasks.count(),
            'today_tasks': today_serializer.data,
            'pending_count': pending_tasks.count(),
            'pending_tasks': pending_grouped
        },
        status=status.HTTP_200_OK
    )


@api_view(['PATCH'])
# @permission_classes([HasRoleAndDataPermission])
def accept_task(request, task_id):
    try:
        task = Logistics.objects.get(task_id=task_id)
        employee_id = request.data.get("auth-user-id")

        if task.status != 'Assigned':
            return Response(
                {'error': f'Task cannot be accepted. Current status: {task.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        task.status = 'Accepted'
        task.sampleacceptedtime = timezone.now()
        task.lastmodified_by = employee_id
        task.lastmodified_date = timezone.now()
        task.save()

        return Response(
            {'message': 'Task accepted successfully', 'data': LogisticsSerializer(task).data},
            status=status.HTTP_200_OK
        )

    except Logistics.DoesNotExist:
        return Response({'error': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['PATCH'])
# @permission_classes([HasRoleAndDataPermission])
def reject_task(request, task_id):
    try:
        task = Logistics.objects.get(task_id=task_id)
        employee_id = request.data.get("auth-user-id")

        if task.status != 'Assigned':
            return Response(
                {'error': f'Task cannot be rejected. Current status: {task.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        remarks = request.data.get('remarks', '').strip()
        if not remarks:
            return Response(
                {'error': 'Remarks are required when rejecting a task'},
                status=status.HTTP_400_BAD_REQUEST
            )

        task.remarks = f"REJECTED: {remarks}"
        task.lastmodified_by = employee_id
        task.lastmodified_date = timezone.now()
        task.save()

        return Response(
            {'message': 'Task rejected successfully', 'data': LogisticsSerializer(task).data},
            status=status.HTTP_200_OK
        )

    except Logistics.DoesNotExist:
        return Response({'error': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['PATCH'])
# @permission_classes([HasRoleAndDataPermission])
def pickup_task(request, task_id):
    try:
        task = Logistics.objects.get(task_id=task_id)
        employee_id = request.data.get("auth-user-id")

        if task.status != 'Accepted':
            return Response(
                {'error': f'Task must be accepted before pickup. Current status: {task.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        task.status = 'PickedUp'
        task.samplepickeduptime = timezone.now()
        task.lastmodified_by = employee_id
        task.lastmodified_date = timezone.now()
        task.save()

        return Response(
            {'message': 'Sample picked up successfully', 'data': LogisticsSerializer(task).data},
            status=status.HTTP_200_OK
        )

    except Logistics.DoesNotExist:
        return Response({'error': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['PATCH'])
@permission_classes([HasRoleAndDataPermission])
def reassign_task(request, task_id):
    """
    PATCH endpoint for Admin to reassign a rejected task.
    Takes new_collector in request data.
    """
    try:
        task = Logistics.objects.get(task_id=task_id)
        employee_id = request.data.get("auth-user-id")
        new_collector = request.data.get('new_collector', '').strip()

        if not new_collector:
            return Response(
                {'error': 'New sample collector name is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Logic: If it was rejected, we clear the rejected mark or just overwrite.
        # User said "If Rejected status...". We'll allow reassigning even if not rejected?
        # Usually it's for rejected ones.
        
        task.sample_collector = new_collector
        task.reassigned_to = new_collector
        task.status = 'Assigned'
        
        # Reset times for the new collector
        task.sampleacceptedtime = None
        task.samplepickeduptime = None
        
        # Optionally update remarks to indicate reassignment
        old_remarks = task.remarks or ""
        task.remarks = f"{old_remarks}\n(Reassigned to {new_collector} by {employee_id})".strip()

        task.lastmodified_by = employee_id
        task.lastmodified_date = timezone.now()
        task.save()

        return Response(
            {'message': 'Task reassigned successfully', 'data': LogisticsSerializer(task).data},
            status=status.HTTP_200_OK
        )

    except Logistics.DoesNotExist:
        return Response({'error': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)


    

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def logistics_dashboard(request):
    try:
        collector = request.query_params.get('sample_collector')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        # Build base query for logistics
        logistics_query = Q()
        
        if collector:
            logistics_query &= Q(sample_collector__iexact=collector)
        
        # Parse dates
        start_dt = None
        end_dt = None
        
        if start_date:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
                logistics_query &= Q(date__gte=start_dt)
            except ValueError:
                return Response(
                    {'error': 'Invalid start_date format. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if end_date:
            try:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
                logistics_query &= Q(date__lte=end_dt)
            except ValueError:
                return Response(
                    {'error': 'Invalid end_date format. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Get logistics tasks
        logistics_tasks = Logistics.objects.filter(logistics_query).order_by('-date', '-sampleordertime')
        
        # Build query for billing data
        billing_query = Q()
        
        if collector:
            billing_query &= Q(sample_collector__iexact=collector)
        
        if start_dt:
            billing_query &= Q(date__gte=start_dt)
        
        if end_dt:
            billing_query &= Q(date__lte=end_dt)
        
        # Get billing data from core_billing collection
        billing_data = Billing.objects.filter(billing_query).order_by('-date', '-created_date')
        
        # Calculate summary statistics
        total_assigned = logistics_tasks.count()
        total_accepted = logistics_tasks.filter(Q(status='Accepted') | Q(status='PickedUp')).count()
        total_rejected = logistics_tasks.filter(remarks__icontains='REJECTED').count()
        total_picked_up = logistics_tasks.filter(status='PickedUp').count()
        total_billed = billing_data.filter(status='Billed').count()
        
        # Get task lists by status
        assigned_tasks = logistics_tasks.filter(status='Assigned')
        accepted_tasks = logistics_tasks.filter(status='Accepted')
        picked_up_tasks = logistics_tasks.filter(status='PickedUp')
        rejected_tasks = logistics_tasks.filter(remarks__icontains='REJECTED')
        billed_tasks = billing_data.filter(status='Billed')
        
        # Serialize data
        assigned_serializer = LogisticsSerializer(assigned_tasks, many=True)
        accepted_serializer = LogisticsSerializer(accepted_tasks, many=True)
        picked_up_serializer = LogisticsSerializer(picked_up_tasks, many=True)
        rejected_serializer = LogisticsSerializer(rejected_tasks, many=True)
        billed_serializer = BillingSerializer(billed_tasks, many=True)
        
        # Build response
        response_data = {
            'summary': {
                'total_assigned': total_assigned,
                'total_accepted': total_accepted,
                'total_rejected': total_rejected,
                'total_picked_up': total_picked_up,
                'total_billed': total_billed,
                'pending_assigned': assigned_tasks.count(),
                'pending_accepted': accepted_tasks.count(),
            },
            'tasks': {
                'assigned': {
                    'count': assigned_tasks.count(),
                    'data': assigned_serializer.data
                },
                'accepted': {
                    'count': accepted_tasks.count(),
                    'data': accepted_serializer.data
                },
                'picked_up': {
                    'count': picked_up_tasks.count(),
                    'data': picked_up_serializer.data
                },
                'rejected': {
                    'count': rejected_tasks.count(),
                    'data': rejected_serializer.data
                },
                'billed': {
                    'count': billed_tasks.count(),
                    'data': billed_serializer.data
                }
            },
            'filters': {
                'sample_collector': get_employee_name(collector) if collector else collector,
                'start_date': start_date,
                'end_date': end_date
            }
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    

@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def logistics_tat_report(request):
    """
    GET: Fetch Logistics Turn Around Time (TAT) Report
    Query params:
    - sample_collector (optional): Filter by specific collector
    - clinicalname (optional): Filter by clinic/lab name
    - start_date (optional): Start date in YYYY-MM-DD format
    - end_date (optional): End date in YYYY-MM-DD format
    - status (optional): Filter by status (Accepted, PickedUp)
    
    Returns:
    - TAT report with time differences between stages
    - Lab Name, Sample Collector, Date, Order Time, Accepted Time, Picked Up Time
    - Order to Accept TAT (in minutes)
    - Accept to Pickup TAT (in minutes)
    """
    try:
        collector = request.query_params.get('sample_collector')
        clinicalname = request.query_params.get('clinicalname')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        status_filter = request.query_params.get('status')
        
        # Build base query
        query = Q()
        
        if collector:
            query &= Q(sample_collector__icontains=collector)
        
        if clinicalname:
            query &= Q(clinicalname__icontains=clinicalname)
        
        if start_date:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
                query &= Q(date__gte=start_dt)
            except ValueError:
                return Response(
                    {'error': 'Invalid start_date format. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if end_date:
            try:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
                query &= Q(date__lte=end_dt)
            except ValueError:
                return Response(
                    {'error': 'Invalid end_date format. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if status_filter:
            query &= Q(status=status_filter)
        else:
            # By default, only show tasks that have at least been accepted
            query &= Q(status__in=['Accepted', 'PickedUp'])
        
        # Get logistics tasks
        logistics_tasks = Logistics.objects.filter(query).order_by('-date', '-sampleordertime')
        
        # Process TAT data
        tat_data = []
        
        for task in logistics_tasks:
            # Parse timestamps
            order_time = None
            accepted_time = None
            picked_up_time = None
            
            # Parse sampleordertime
            if task.sampleordertime:
                try:
                    if isinstance(task.sampleordertime, str):
                        order_time = datetime.fromisoformat(task.sampleordertime.replace('Z', '+00:00'))
                    else:
                        order_time = task.sampleordertime
                except:
                    pass
            
            # Parse sampleacceptedtime
            if task.sampleacceptedtime:
                try:
                    if isinstance(task.sampleacceptedtime, str):
                        # Handle format like "2026-01-28 12:31:41.660901+00:00"
                        accepted_time = datetime.fromisoformat(task.sampleacceptedtime.replace(' ', 'T'))
                    else:
                        accepted_time = task.sampleacceptedtime
                except:
                    pass
            
            # Parse samplepickeduptime
            if task.samplepickeduptime:
                try:
                    if isinstance(task.samplepickeduptime, str):
                        picked_up_time = datetime.fromisoformat(task.samplepickeduptime.replace(' ', 'T'))
                    else:
                        picked_up_time = task.samplepickeduptime
                except:
                    pass
            
            # Calculate TAT in minutes
            order_to_accept_tat = None
            accept_to_pickup_tat = None
            total_tat = None
            
            if order_time and accepted_time:
                try:
                    # Make both timezone aware if needed
                    if order_time.tzinfo is None:
                        order_time = timezone.make_aware(order_time)
                    if accepted_time.tzinfo is None:
                        accepted_time = timezone.make_aware(accepted_time)
                    
                    time_diff = accepted_time - order_time
                    order_to_accept_tat = round(time_diff.total_seconds() / 60, 2)  # Convert to minutes
                except:
                    pass
            
            if accepted_time and picked_up_time:
                try:
                    # Make both timezone aware if needed
                    if accepted_time.tzinfo is None:
                        accepted_time = timezone.make_aware(accepted_time)
                    if picked_up_time.tzinfo is None:
                        picked_up_time = timezone.make_aware(picked_up_time)
                    
                    time_diff = picked_up_time - accepted_time
                    accept_to_pickup_tat = round(time_diff.total_seconds() / 60, 2)  # Convert to minutes
                except:
                    pass
            
            # Calculate total TAT (order to pickup)
            if order_time and picked_up_time:
                try:
                    if order_time.tzinfo is None:
                        order_time = timezone.make_aware(order_time)
                    if picked_up_time.tzinfo is None:
                        picked_up_time = timezone.make_aware(picked_up_time)
                    
                    time_diff = picked_up_time - order_time
                    total_tat = round(time_diff.total_seconds() / 60, 2)  # Convert to minutes
                except:
                    pass
            
            # Format times for display
            order_time_str = order_time.strftime('%Y-%m-%d %H:%M:%S') if order_time else None
            accepted_time_str = accepted_time.strftime('%Y-%m-%d %H:%M:%S') if accepted_time else None
            picked_up_time_str = picked_up_time.strftime('%Y-%m-%d %H:%M:%S') if picked_up_time else None
            
            tat_data.append({
                'task_id': task.task_id,
                'lab_name': task.clinicalname,
                'sample_collector': get_employee_name(task.sample_collector) if task.sample_collector else task.sample_collector,
                'sales_person': task.sales_person if hasattr(task, 'sales_person') else None,
                'date': str(task.date),
                'order_time': order_time_str,
                'accepted_time': accepted_time_str,
                'picked_up_time': picked_up_time_str,
                'order_to_accept_tat_minutes': order_to_accept_tat,
                'accept_to_pickup_tat_minutes': accept_to_pickup_tat,
                'total_tat_minutes': total_tat,
                'status': task.status,
                'remarks': task.remarks
            })
        
        # Calculate summary statistics
        total_records = len(tat_data)
        
        # Calculate averages (excluding None values)
        order_to_accept_values = [x['order_to_accept_tat_minutes'] for x in tat_data if x['order_to_accept_tat_minutes'] is not None]
        accept_to_pickup_values = [x['accept_to_pickup_tat_minutes'] for x in tat_data if x['accept_to_pickup_tat_minutes'] is not None]
        total_tat_values = [x['total_tat_minutes'] for x in tat_data if x['total_tat_minutes'] is not None]
        
        avg_order_to_accept = round(sum(order_to_accept_values) / len(order_to_accept_values), 2) if order_to_accept_values else None
        avg_accept_to_pickup = round(sum(accept_to_pickup_values) / len(accept_to_pickup_values), 2) if accept_to_pickup_values else None
        avg_total_tat = round(sum(total_tat_values) / len(total_tat_values), 2) if total_tat_values else None
        
        response_data = {
            'summary': {
                'total_records': total_records,
                'avg_order_to_accept_tat_minutes': avg_order_to_accept,
                'avg_accept_to_pickup_tat_minutes': avg_accept_to_pickup,
                'avg_total_tat_minutes': avg_total_tat,
            },
            'filters': {
                'sample_collector': get_employee_name(collector) if collector else collector,
                'clinicalname': clinicalname,
                'start_date': start_date,
                'end_date': end_date,
                'status': status_filter
            },
            'data': tat_data
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


import json
from bson import ObjectId
import os
import requests
from pymongo import MongoClient
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from ..models import RouteSetup, RouteAnalysis, ClinicalName
from ..serializers import RouteSetupSerializer, RouteAnalysisSerializer


def _as_list(value):
    """Defensive parse in case any legacy rows have JSON stored as a string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return value or []


def _gridfs():
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    db = client["Diagnostics"]
    return client, GridFS(db)


def get_clinical_name_map(referrer_codes):
    """
    Looks up ClinicalName records via Django ORM (same DB the app uses).
    Returns dict: { referrerCode -> clinicalname }
    Handles the case where referrer_codes may be a JSON string instead of a list.
    """
    codes = _as_list(referrer_codes) if not isinstance(referrer_codes, list) else referrer_codes
    if not codes:
        return {}
    records = ClinicalName.objects.filter(referrerCode__in=codes).values("referrerCode", "clinicalname")
    return {r["referrerCode"]: r["clinicalname"] for r in records}


# ---------- Route Setup ----------

@api_view(['GET', 'POST'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def routesetup(request):
    try:
        if request.method == 'POST':
            data = request.data.copy()
            employee_id = request.data.get("auth-user-id")   # read from header, not body
            data["created_by"] = employee_id
            data["created_date"] = timezone.now()

            # clinical_name must be a real list, not a JSON string
            if isinstance(data.get("clinical_name"), str):
                data["clinical_name"] = _as_list(data["clinical_name"])

            serializer = RouteSetupSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {"message": "Route created successfully", "data": serializer.data},
                    status=status.HTTP_201_CREATED
                )
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        # GET — support optional filters
        # ?collector_id=<employeeId>  → routes assigned to that collector
        # ?date=YYYY-MM-DD            → routes created on that date (not yet used for filtering,
        #                               but returned so frontend can show date-based lists)
        collector_id = request.query_params.get("collector_id")
        date_str     = request.query_params.get("date")

        routes = RouteSetup.objects.all().order_by('-created_date')

        if collector_id:
            routes = routes.filter(logistics_mapping=collector_id)
        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d")
                start_of_day = timezone.make_aware(target_date.replace(hour=0, minute=0, second=0, microsecond=0))
                end_of_day   = timezone.make_aware(target_date.replace(hour=23, minute=59, second=59, microsecond=999999))
                routes = routes.filter(created_date__gte=start_of_day, created_date__lte=end_of_day)
            except ValueError:
                pass # ignore invalid date format

        serializer = RouteSetupSerializer(routes, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def get_todays_route_status(request):
    """
    GET: Returns route analysis statuses for a given date (defaults to today).
    Accepts optional ?date=YYYY-MM-DD and ?collector_id=<id> query params.
    Uses datetime range instead of __date lookup (djongo compatibility).
    """
    try:
        date_str     = request.query_params.get("date")
        collector_id = request.query_params.get("collector_id")

        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d")
                start_of_day = timezone.make_aware(target_date.replace(hour=0, minute=0, second=0, microsecond=0))
                end_of_day   = timezone.make_aware(target_date.replace(hour=23, minute=59, second=59, microsecond=999999))
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            now = timezone.now()
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day   = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        analyses = RouteAnalysis.objects.filter(
            start_time__gte=start_of_day,
            start_time__lte=end_of_day,
        )

        # If collector_id supplied, filter to routes belonging to that collector
        if collector_id:
            collector_route_ids = list(
                RouteSetup.objects.filter(logistics_mapping=collector_id).values_list("id", flat=True)
            )
            analyses = analyses.filter(route_id__in=collector_route_ids)

        analyses = analyses.values("route_id", "status", "id")

        route_statuses = [
            {
                "route_id":    a["route_id"],
                "status":      a["status"],
                "analysis_id": a["id"],
            }
            for a in analyses
        ]

        return Response({"route_statuses": route_statuses}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def route_analysis_admin_report(request):
    """
    GET: Admin dashboard report.
    Query params:
      ?from_date=YYYY-MM-DD    (default: today)
      ?to_date=YYYY-MM-DD      (default: today)
      ?collector_id=<empId>    (optional: filter by specific collector)

    Returns per-collector, per-route breakdown across a date range:
      - Matrix of dates and lab visits (Y/N)
    """
    try:
        from_date_str = request.query_params.get("from_date")
        to_date_str   = request.query_params.get("to_date")
        collector_id  = request.query_params.get("collector_id")

        # ── Resolve date range ────────────────────────────────────────────────
        now = timezone.now()
        
        try:
            if from_date_str:
                from_date_obj = datetime.strptime(from_date_str, "%Y-%m-%d")
            else:
                from_date_obj = now
                from_date_str = now.strftime("%Y-%m-%d")
                
            if to_date_str:
                to_date_obj = datetime.strptime(to_date_str, "%Y-%m-%d")
            else:
                to_date_obj = from_date_obj
                to_date_str = from_date_obj.strftime("%Y-%m-%d")
                
            start_of_day = timezone.make_aware(from_date_obj.replace(hour=0,  minute=0,  second=0,  microsecond=0))
            end_of_day   = timezone.make_aware(to_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999))
            
            if start_of_day > end_of_day:
                start_of_day, end_of_day = end_of_day, start_of_day
                from_date_obj, to_date_obj = to_date_obj, from_date_obj
                from_date_str, to_date_str = to_date_str, from_date_str
                
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

        # Generate list of dates in the range
        date_list = []
        current_date = from_date_obj
        while current_date.date() <= to_date_obj.date():
            date_list.append(current_date.strftime("%Y-%m-%d"))
            current_date += timedelta(days=1)

        # ── Fetch all routes assigned in this date range ──────────────────────────
        route_qs = RouteSetup.objects.filter(
            created_date__gte=start_of_day,
            created_date__lte=end_of_day
        )
        if collector_id:
            route_qs = route_qs.filter(logistics_mapping=collector_id)

        # ── Fetch analyses for that date range ───────────────────────────────
        analysis_qs = RouteAnalysis.objects.filter(
            start_time__gte=start_of_day,
            start_time__lte=end_of_day,
        )
        if collector_id:
            analysis_qs = analysis_qs.filter(
                route_id__in=list(route_qs.values_list("id", flat=True))
            )

        # Build analysis lookup: route_id -> date_str -> analysis obj
        analysis_by_route_and_date = {}
        for a in analysis_qs:
            # We determine the date of this analysis based on start_time
            if a.start_time:
                a_date_str = timezone.localtime(a.start_time).strftime("%Y-%m-%d")
                if a.route_id not in analysis_by_route_and_date:
                    analysis_by_route_and_date[a.route_id] = {}
                analysis_by_route_and_date[a.route_id][a_date_str] = a

        # ── Resolve collector names from Global DB ────────────────────────────
        collector_ids = list(set(r.logistics_mapping for r in route_qs if r.logistics_mapping))
        collector_name_map = {}
        if collector_ids:
            try:
                from pymongo import MongoClient as _MC
                _client = _MC(os.getenv("GLOBAL_DB_HOST"))
                _col = _client["Global"]["backend_diagnostics_profile"]
                for doc in _col.find(
                    {"employeeId": {"$in": collector_ids}},
                    {"employeeId": 1, "employeeName": 1, "_id": 0}
                ):
                    collector_name_map[doc["employeeId"]] = doc["employeeName"]
                _client.close()
            except Exception:
                pass  # fallback: show IDs only

        # ── Build report matrix ───────────────────────────────────────────────
        report_routes = []
        summary = {
            "total_routes":       0,
            "total_labs":         0,
            "visited_labs":       0,
        }

        for route in route_qs:
            codes    = _as_list(route.clinical_name)
            name_map = get_clinical_name_map(codes)
            total_labs = len(codes)

            summary["total_routes"] += 1
            summary["total_labs"]   += total_labs * len(date_list)

            labs_detail = []
            for code in codes:
                lab_info = {
                    "referrerCode": code,
                    "clinicalname": name_map.get(code, code),
                    "total_visits": 0,
                    "visits": {}
                }
                
                # Check visits for each date
                for d_str in date_list:
                    visited = False
                    visited_at = None
                    image_id = None
                    latitude = None
                    longitude = None
                    
                    analysis = analysis_by_route_and_date.get(route.id, {}).get(d_str)
                    if analysis:
                        visits = _as_list(analysis.visits)
                        v = next((v for v in visits if v.get("referrerCode") == code), None)
                        if v and v.get("visited"):
                            visited = True
                            visited_at = v.get("uploaded_at")
                            image_id = v.get("image")
                            latitude = v.get("latitude")
                            longitude = v.get("longitude")
                            
                    if visited:
                        lab_info["total_visits"] += 1
                        summary["visited_labs"] += 1
                        
                    lab_info["visits"][d_str] = {
                        "visited": visited,
                        "visited_at": visited_at,
                        "image_id": image_id,
                        "latitude": latitude,
                        "longitude": longitude
                    }
                
                labs_detail.append(lab_info)

            report_routes.append({
                "route_id":       route.id,
                "route_name":     route.route_name,
                "collector_id":   route.logistics_mapping,
                "collector_name": collector_name_map.get(route.logistics_mapping, route.logistics_mapping),
                "total_labs":     total_labs,
                "labs":           labs_detail,
            })

        # Collector-level summary (optional now since the matrix is the main focus, but keeping it for completeness)
        collector_summary = {}
        for row in report_routes:
            cid = row["collector_id"]
            if cid not in collector_summary:
                collector_summary[cid] = {
                    "collector_id":   cid,
                    "collector_name": row["collector_name"],
                    "total_routes":   0,
                    "total_labs":     0,
                    "visited_labs":   0,
                }
            cs = collector_summary[cid]
            cs["total_routes"] += 1
            cs["total_labs"]   += row["total_labs"] * len(date_list)
            for lab in row["labs"]:
                cs["visited_labs"] += lab["total_visits"]

        return Response({
            "from_date":          from_date_str,
            "to_date":            to_date_str,
            "dates":              date_list,
            "summary":            summary,
            "collector_summary":  list(collector_summary.values()),
            "routes":             report_routes,
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Start / End / Get Route Analysis ----------

@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def start_route_analysis(request):
    """
    POST: Start a route analysis session.
    Stores start_time on creation. If an in_progress session already exists,
    returns it instead of creating a duplicate.
    """
    try:
        route_id = request.data.get("route_id")
        employee_id = request.data.get("auth-user-id")

        if not route_id:
            return Response({"error": "route_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            route = RouteSetup.objects.get(id=route_id)
        except RouteSetup.DoesNotExist:
            return Response({"error": "Route not found"}, status=status.HTTP_404_NOT_FOUND)

        # Return existing in-progress session so the frontend can resume
        existing = RouteAnalysis.objects.filter(route_id=route.id, status="in_progress").first()
        if existing:
            serializer = RouteAnalysisSerializer(existing)
            return Response(
                {"message": "Route is already in progress", "data": serializer.data},
                status=status.HTTP_200_OK
            )

        referrer_codes = _as_list(route.clinical_name)
        name_map = get_clinical_name_map(referrer_codes)

        visits = [
            {
                "referrerCode": code,
                "clinicalname": name_map.get(code),
                "visited": False,
                "image": None,        # will hold GridFS ObjectId string after upload
                "uploaded_at": None,
                "latitude": None,
                "longitude": None,
            }
            for code in referrer_codes
        ]

        data = {
            "route_id": route.id,
            "logistics_mapping": route.logistics_mapping,
            "start_time": timezone.now(),
            "status": "in_progress",
            "visits": visits,
            "created_by": employee_id,
            "created_date": timezone.now(),
        }

        serializer = RouteAnalysisSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        return Response({"error": "Validation failed", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def end_route_analysis(request):
    try:
        analysis_id = request.data.get("analysis_id")
        try:
            analysis = RouteAnalysis.objects.get(id=analysis_id)
        except RouteAnalysis.DoesNotExist:
            return Response({"error": "Route analysis not found"}, status=status.HTTP_404_NOT_FOUND)

        analysis.end_time = timezone.now()
        analysis.status = "completed"
        analysis.save()

        serializer = RouteAnalysisSerializer(analysis)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def get_route_analysis(request, analysis_id):
    try:
        analysis = RouteAnalysis.objects.get(id=analysis_id)
        serializer = RouteAnalysisSerializer(analysis)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except RouteAnalysis.DoesNotExist:
        return Response({"error": "Route analysis not found"}, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def get_active_route_analysis(request, route_id):
    """Lets the frontend check on load whether a route already has an
    in-progress run, so the Start button can be replaced by End/visit UI."""
    analysis = RouteAnalysis.objects.filter(route_id=route_id, status="in_progress").first()
    if not analysis:
        return Response({"data": None}, status=status.HTTP_200_OK)
    serializer = RouteAnalysisSerializer(analysis)
    return Response({"data": serializer.data}, status=status.HTTP_200_OK)


# ---------- Mark Visit (PATCH, multipart file upload via GridFS) ----------

@api_view(['PATCH'])
@permission_classes([HasRoleAndDataPermission])
@parser_classes([MultiPartParser, FormParser, JSONParser])
@csrf_exempt
def mark_visit(request):
    """
    PATCH: Mark a clinic as visited and optionally upload a photo.
    - analysis_id   : RouteAnalysis record id (required)
    - referrerCode  : clinic referrer code to mark visited (required)
    - image         : image file (optional, stored in GridFS)
    - latitude      : GPS latitude (optional)
    - longitude     : GPS longitude (optional)

    Stores the GridFS ObjectId string in visit["image"].
    Frontend retrieves the image via: GET /route-analysis/image/<file_id>/
    """
    try:
        analysis_id = request.data.get("analysis_id")
        referrer_code = request.data.get("referrerCode")
        employee_id = request.data.get("auth-user-id")

        if not analysis_id or not referrer_code:
            return Response(
                {"error": "analysis_id and referrerCode are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            analysis = RouteAnalysis.objects.get(id=analysis_id)
        except RouteAnalysis.DoesNotExist:
            return Response({"error": "Route analysis not found"}, status=status.HTTP_404_NOT_FOUND)

        if analysis.status != "in_progress":
            return Response(
                {"error": "Cannot update a completed route analysis"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ── Upload image to GridFS (if provided) ──────────────────────────────
        image_file_id = None
        uploaded_file = request.FILES.get("image")
        if uploaded_file:
            client, fs = _gridfs()
            try:
                file_id = fs.put(
                    uploaded_file.read(),
                    filename=uploaded_file.name,
                    content_type=uploaded_file.content_type,
                )
                image_file_id = str(file_id)   # ObjectId → plain string stored in JSON
            except Exception as upload_err:
                return Response(
                    {"error": f"Image upload failed: {upload_err}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            finally:
                client.close()

        latitude = request.data.get("latitude") or None
        longitude = request.data.get("longitude") or None
        uploaded_at = timezone.now().isoformat()

        # ── Update the matching visit entry ───────────────────────────────────
        visits = _as_list(analysis.visits)
        found = False
        for visit in visits:
            if visit.get("referrerCode") == referrer_code:
                visit["visited"] = True
                if image_file_id:
                    visit["image"] = image_file_id   # GridFS ObjectId string
                visit["uploaded_at"] = uploaded_at
                visit["latitude"] = latitude
                visit["longitude"] = longitude
                visit["updated_by"] = employee_id
                found = True
                break

        if not found:
            return Response(
                {"error": "referrerCode not found on this route"},
                status=status.HTTP_400_BAD_REQUEST
            )

        analysis.visits = visits
        analysis.lastmodified_by = employee_id
        analysis.lastmodified_date = timezone.now()
        analysis.save()

        serializer = RouteAnalysisSerializer(analysis)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(['GET'])
@permission_classes([AllowAny])   # ObjectId in URL acts as a bearer token; no extra auth needed
def get_route_image(request, file_id):
    client, fs = _gridfs()
    try:
        grid_out = fs.get(ObjectId(file_id))
        return HttpResponse(grid_out.read(), content_type=grid_out.content_type or "application/octet-stream")
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
    finally:
        client.close()



@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_b2b_employees(request):
    try:
        employee_id =  request.data.get("auth-user-id")
        print(f"Fetching B2B employees for employee_id: {employee_id}")
 
        query = {
            "$or": [
                {"primaryRole": {"$in": B2B_ROLES}},
                {"additionalRoles": {"$in": B2B_ROLES}}
            ]
        }
 
        projection = {
            "_id": 0,
            "employeeId": 1,
            "employeeName": 1,
            "primaryRole": 1,
            "additionalRoles": 1,
            "hospitalCode": 1
        }
 
        employees = list(profile_collection.find(query, projection))
 
        return Response(
            {
                "status": True,
                "message": "B2B Employees fetched successfully",
                "data": employees
            },
            status=status.HTTP_200_OK
        )
 
    except Exception as e:
        return Response(
            {
                "status": False,
                "message": str(e),
                "data": []
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )



from bson import ObjectId
from bson.errors import InvalidId

from ..models import Busfare
from ..serializers import BusfareSerializer




 
 
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
    PATCH /bus_fare/                  -> mark an entry as picked up
                                          body: { "busfare_id": <id>, "pickedupby": "<employeeId>" }
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

            serializer = BusfareSerializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

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
            print(f"bus_fare POST request.FILES keys={list(request.FILES.keys())} bustphoto={uploaded_file!r}")
            if uploaded_file:
                client, fs = _gridfs()
                try:
                    file_id = fs.put(
                        uploaded_file.read(),
                        filename=uploaded_file.name,
                        content_type=uploaded_file.content_type,
                    )
                    data['bustphoto'] = str(file_id)  # ObjectId -> plain string
                    print(f"bus_fare POST photo stored in GridFS, file_id={file_id}")
                except Exception as upload_err:
                    print(f"bus_fare POST photo upload FAILED: {upload_err}")
                    return Response(
                        {'error': f'Image upload failed: {upload_err}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
                finally:
                    client.close()
            else:
                print("bus_fare POST no bustphoto file present in request.FILES")
                data.pop('bustphoto', None)

            serializer = BusfareSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)

            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # PATCH - mark a bus fare entry as picked up
        busfare_id = request.data.get('busfare_id')
        if not busfare_id:
            return Response(
                {'error': 'busfare_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        employee_id = request.data.get("auth-user-id")

        updated_count = Busfare.objects.filter(busfare_id=busfare_id).update(
            pickedupby=request.data.get('pickedupby'),
            lastmodified_by=employee_id,
            lastmodified_date=timezone.now(),
        )

        if not updated_count:
            return Response(
                {'error': f'Busfare {busfare_id} not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        busfare = Busfare.objects.get(busfare_id=busfare_id)
        serializer = BusfareSerializer(busfare)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
 





 
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
    




from .dbcollection import profile_collection, B2B_ROLES, B2B_LAB_Roles


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_b2b_employees(request):
    try:
        employee_id = request.data.get("auth-user-id")
        print(f"Fetching B2B employees for employee_id: {employee_id}")

        query = {
            "$or": [
                {"primaryRole": {"$in": B2B_ROLES}},
                {"additionalRoles": {"$in": B2B_ROLES}}
            ]
        }

        projection = {
            "_id": 0,
            "employeeId": 1,
            "employeeName": 1,
            "primaryRole": 1,
            "additionalRoles": 1,
            "hospitalCode": 1
        }

        employees = list(profile_collection.find(query, projection))

        return Response(
            {
                "status": True,
                "message": "B2B Employees fetched successfully",
                "data": employees
            },
            status=status.HTTP_200_OK
        )

    except Exception as e:
        return Response(
            {
                "status": False,
                "message": str(e),
                "data": []
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )





@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_b2b_lab_employees(request):
    try:
        employee_id = request.data.get("auth-user-id")
        print(f"Fetching B2B Lab employees for employee_id: {employee_id}")

        query = {
            "$or": [
                {"primaryRole": {"$in": B2B_LAB_Roles}},
                {"additionalRoles": {"$in": B2B_LAB_Roles}},
                {"designation": {"$in": BIO_CHESMISTRY_ROLES}}
            ]
        }

        projection = {
            "_id": 0,
            "employeeId": 1,
            "employeeName": 1,
            "primaryRole": 1,
            "additionalRoles": 1,
            "designation": 1,
            "hospitalCode": 1
        }

        employees = list(profile_collection.find(query, projection))

        return Response(
            {
                "status": True,
                "message": "B2B Lab Employees fetched successfully",
                "data": employees
            },
            status=status.HTTP_200_OK
        )

    except Exception as e:
        return Response(
            {
                "status": False,
                "message": str(e),
                "data": []
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )





from datetime import datetime, time, timedelta
 
from django.utils import timezone


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

            serializer = CustomerComplaintSerializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

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
