import os
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q, Count, Case, When, IntegerField
from pymongo import MongoClient
from datetime import datetime
from django.core.files.storage import default_storage

from collections import defaultdict


from ..models import Logistics, Billing
from ..serializers import LogisticsSerializer, BillingSerializer

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
            employee_id = request.headers.get("auth-user-id")

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
@permission_classes([HasRoleAndDataPermission])
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
@permission_classes([HasRoleAndDataPermission])
def accept_task(request, task_id):
    try:
        task = Logistics.objects.get(task_id=task_id)
        employee_id = request.headers.get("auth-user-id")

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
@permission_classes([HasRoleAndDataPermission])
def reject_task(request, task_id):
    try:
        task = Logistics.objects.get(task_id=task_id)
        employee_id = request.headers.get("auth-user-id")

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
@permission_classes([HasRoleAndDataPermission])
def pickup_task(request, task_id):
    try:
        task = Logistics.objects.get(task_id=task_id)
        employee_id = request.headers.get("auth-user-id")

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
        employee_id = request.headers.get("auth-user-id")
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
                'sample_collector': collector,
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
                'sample_collector': task.sample_collector,
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
                'sample_collector': collector,
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
from gridfs import GridFS
from pymongo import MongoClient
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from ..models import RouteSetup, RouteAnalysis
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
    db = client["HMS"]
    return client, GridFS(db)


def get_clinical_name_map(referrer_codes):
    client = MongoClient(os.getenv("GLOBAL_DB_HOST"))
    db = client["HMS"]
    collection = db["clinicalname"]
    docs = collection.find(
        {"referrerCode": {"$in": referrer_codes}},
        {"_id": 0, "referrerCode": 1, "clinicalname": 1},
    )
    result = {doc["referrerCode"]: doc["clinicalname"] for doc in docs}
    client.close()
    return result


# ---------- Route Setup ----------

@api_view(['GET', 'POST'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def routesetup(request):
    try:
        if request.method == 'POST':
            data = request.data.copy()
            employee_id = request.data.get("auth-user-id")
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

        routes = RouteSetup.objects.all().order_by('-created_date')
        serializer = RouteSetupSerializer(routes, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Start / End / Get Route Analysis ----------

@api_view(['POST'])
@permission_classes([HasRoleAndDataPermission])
@csrf_exempt
def start_route_analysis(request):
    try:
        route_id = request.data.get("route_id")
        employee_id = request.data.get("auth-user-id")

        try:
            route = RouteSetup.objects.get(id=route_id)
        except RouteSetup.DoesNotExist:
            return Response({"error": "Route not found"}, status=status.HTTP_404_NOT_FOUND)

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
                "image": None,
                "uploaded_at": None,
                "latitude": None,
                "longitude": None,
            }
            for code in referrer_codes
        ]

        analysis = RouteAnalysis.objects.create(
            route_id=route.id,
            logistics_mapping=route.logistics_mapping,
            start_time=timezone.now(),
            status="in_progress",
            visits=visits,
            created_by=employee_id,
            created_date=timezone.now(),
        )

        serializer = RouteAnalysisSerializer(analysis)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

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
        analysis.save(update_fields=["end_time", "status"])

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
    try:
        analysis_id = request.data.get("analysis_id")
        referrer_code = request.data.get("referrerCode")

        if not analysis_id or not referrer_code:
            return Response({"error": "analysis_id and referrerCode are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            analysis = RouteAnalysis.objects.get(id=analysis_id)
        except RouteAnalysis.DoesNotExist:
            return Response({"error": "Route analysis not found"}, status=status.HTTP_404_NOT_FOUND)

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
                image_file_id = str(file_id)
            except Exception as upload_err:
                return Response({"error": f"Image upload failed: {upload_err}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            finally:
                client.close()

        latitude = request.data.get("latitude") or None
        longitude = request.data.get("longitude") or None
        uploaded_at = timezone.now().isoformat()

        visits = _as_list(analysis.visits)
        found = False
        for visit in visits:
            if visit.get("referrerCode") == referrer_code:
                visit["visited"] = True
                if image_file_id:
                    visit["image"] = image_file_id
                visit["uploaded_at"] = uploaded_at
                visit["latitude"] = latitude
                visit["longitude"] = longitude
                found = True
                break

        if not found:
            return Response({"error": "referrerCode not found on this route"}, status=status.HTTP_400_BAD_REQUEST)

        analysis.visits = visits
        analysis.save(update_fields=["visits"])

        serializer = RouteAnalysisSerializer(analysis)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([HasRoleAndDataPermission])
def get_route_image(request, file_id):
    client, fs = _gridfs()
    try:
        grid_out = fs.get(ObjectId(file_id))
        return HttpResponse(grid_out.read(), content_type=grid_out.content_type or "application/octet-stream")
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
    finally:
        client.close()