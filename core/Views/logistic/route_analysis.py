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

import json
from bson import ObjectId
import requests
from django.http import HttpResponse
from rest_framework.decorators import parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from ...models import RouteSetup, RouteAnalysis, ClinicalName
from ...serializers import RouteSetupSerializer, RouteAnalysisSerializer

from .common import _as_list, _gridfs, get_clinical_name_map

logger = logging.getLogger(__name__)


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

        page_obj, page_meta = paginate_queryset(routes, request)
        serializer = RouteSetupSerializer(page_obj, many=True)
        # NOTE: response shape changed from a bare JSON array to a paginated
        # object ({"data": [...], total_pages, current_page, total_count}) to
        # bound the payload as RouteSetup rows grow; update any frontend
        # caller that expected a raw array here.
        return Response({"data": serializer.data, **page_meta}, status=status.HTTP_200_OK)

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
