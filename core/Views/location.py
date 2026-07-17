from rest_framework.response import Response
from django.http import JsonResponse
from datetime import datetime
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import json
import math

from ..models import SampleCollectorLocation
from core.utils import get_employee_name
from core.pagination import paginate_queryset


def calculate_distance(lat1, lon1, lat2, lon2):
    """Haversine formula – returns distance in meters."""
    lat1, lon1, lat2, lon2 = map(
        math.radians, 
        [float(lat1), float(lon1), float(lat2), float(lon2)]
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    r = 6371000
    return c * r

def get_route(item):
    if not item or not item.location_history:
        return []
    if isinstance(item.location_history, str):
        try:
            return json.loads(item.location_history)
        except json.JSONDecodeError:
            return []
    elif isinstance(item.location_history, list):
        return item.location_history
    return []

def format_location_response(item):
    route = get_route(item)
    lat_start = route[0].get("lat") if route else None
    lng_start = route[0].get("lng") if route else None
    lat_end = route[-1].get("lat") if route and item.endTime else None
    lng_end = route[-1].get("lng") if route and item.endTime else None
    curr_lat = route[-1].get("lat") if route else lat_start
    curr_lng = route[-1].get("lng") if route else lng_start
    last_val_time = route[-1].get("timestamp") if route else None

    # Calculate total duration in seconds if both start and end exist
    total_duration = None
    if item.startTime and item.endTime:
        duration = (item.endTime - item.startTime).total_seconds()
        total_duration = str(duration)

    return {
        "id": str(item.location_id),
        "sampleCollector": get_employee_name(item.sampleCollector) if item.sampleCollector else item.sampleCollector,
        "date": item.date.isoformat() if item.date else None,
        "latitudeStart": lat_start,
        "longitudeStart": lng_start,
        "latitudeEnd": lat_end,
        "longitudeEnd": lng_end,
        "currentLatitude": curr_lat,
        "currentLongitude": curr_lng,
        "distance_travelled": item.distance_travelled or "0.00",
        "startTime": item.startTime.isoformat() if item.startTime else None,
        "endTime": item.endTime.isoformat() if item.endTime else None,
        "totalDuration": total_duration,
        "isActive": getattr(item, 'is_location_active', 0) == 1 if hasattr(item, 'is_location_active') else bool(item.startTime and not item.endTime),
        "lastUpdated": last_val_time or (item.startTime.isoformat() if item.startTime else timezone.now().isoformat()),
        "routePoints": route
    }


@api_view(['GET', 'POST', 'PUT'])
@csrf_exempt
def sample_collector_location(request):

    # ============================================================
    # 🟦 GET REQUEST — Retrieve location records
    # ============================================================
    if request.method == 'GET':
        try:
            date = request.GET.get("date")
            sample_collector = request.GET.get("sampleCollector")

            # ---------------------------
            # GET ALL DATA
            # ---------------------------
            if not date and not sample_collector:
                all_data = SampleCollectorLocation.objects.all().order_by('-date', '-startTime')
                response = [format_location_response(item) for item in all_data]
                return JsonResponse(response, safe=False)

            # ---------------------------
            # GET BY DATE ONLY
            # ---------------------------
            if date and not sample_collector:
                date_obj = datetime.strptime(date, "%Y-%m-%d").date()
                records = SampleCollectorLocation.objects.filter(date=date_obj).order_by('-startTime')
                response = [format_location_response(item) for item in records]
                return JsonResponse(response, safe=False)

            # ---------------------------
            # GET BY DATE + COLLECTOR
            # ---------------------------
            date_obj = datetime.strptime(date, "%Y-%m-%d").date()
            
            # Check for any active global tracking session first
            active_items = SampleCollectorLocation.objects.filter(
                sampleCollector=sample_collector, 
                is_location_active=1
            ).order_by('location_id')
            if active_items.exists():
                return JsonResponse([format_location_response(active_items.last())], safe=False)

            # Fallback to fetching today's last tracking session history
            items = SampleCollectorLocation.objects.filter(sampleCollector=sample_collector, date=date_obj).order_by('location_id')
            if items.exists():
                item = items.last()
                return JsonResponse([format_location_response(item)], safe=False)
            else:
                return JsonResponse([], safe=False)

        except SampleCollectorLocation.DoesNotExist:
            return JsonResponse([], safe=False)

        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)}, status=500)

    # ============================================================
    # 🟩 POST REQUEST — Start Tracking
    # ============================================================
    if request.method == 'POST':
        try:
            data = json.loads(request.body.decode())
            sample_collector = data.get("sampleCollector")
            date = data.get("date")

            lat = str(data.get("latitudeStart"))
            lng = str(data.get("longitudeStart"))

            date_obj = datetime.strptime(date, "%Y-%m-%d").date()

            new_history = [{
                "lat": lat,
                "lng": lng,
                "timestamp": timezone.now().isoformat()
            }]

            # Always create a new document. A collector can have multiple trips per day.
            location = SampleCollectorLocation.objects.create(
                sampleCollector=sample_collector,
                date=date_obj,
                startTime=timezone.now(),
                endTime=None,
                is_location_active=1,
                distance_travelled=None,
                location_history=new_history
            )

            return JsonResponse({
                "success": True,
                "message": "Tracking started",
                "data": {"id": str(location.location_id)}
            })

        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)}, status=500)

    # ============================================================
    # 🟥 PUT REQUEST — Update / End Tracking
    # ============================================================
    if request.method == 'PUT':
        try:
            data = json.loads(request.body.decode())

            sample_collector = data.get("sampleCollector")
            date = data.get("date")

            # Global lookup for active location, bypassing strict date_obj mapping for midnight crossover
            items = SampleCollectorLocation.objects.filter(
                sampleCollector=sample_collector, 
                is_location_active=1
            ).order_by('location_id')
            
            if not items.exists():
                return JsonResponse({"success": False, "message": "No active tracking"}, status=404)
            
            item = items.last()
            route = get_route(item)

            # ------------------------------------------------
            # END TRACKING
            # ------------------------------------------------
            if data.get("latitudeEnd") and data.get("longitudeEnd"):
                end_lat = str(data["latitudeEnd"])
                end_lng = str(data["longitudeEnd"])

                route.append({
                    "lat": end_lat,
                    "lng": end_lng,
                    "timestamp": timezone.now().isoformat()
                })

                # Distance calculation
                total = 0
                for i in range(1, len(route)):
                    total += calculate_distance(
                        route[i-1].get("lat", 0), route[i-1].get("lng", 0),
                        route[i].get("lat", 0), route[i].get("lng", 0)
                    )

                # Haversine distance is in meters, convert to km
                haversine_distance_km = total / 1000
                total_distance = f"{haversine_distance_km:.2f}"
                
                # If the frontend passes a more accurate distance (e.g. from Google Maps API)
                if data.get("distance_travelled"):
                    total_distance = str(data.get("distance_travelled"))

                item.endTime = timezone.now()
                item.distance_travelled = total_distance
                
                SampleCollectorLocation.objects.filter(location_id=item.location_id).update(
                    endTime=timezone.now(),
                    is_location_active=0,
                    distance_travelled=total_distance,
                    location_history=json.dumps(route) if isinstance(item.location_history, str) else route
                )

                return JsonResponse({
                    "success": True,
                    "message": "Tracking ended",
                    "distance": total_distance
                })

            # ------------------------------------------------
            # LIVE UPDATE LOCATION
            # ------------------------------------------------
            curr_lat = data.get("currentLatitude")
            curr_lng = data.get("currentLongitude")

            if curr_lat and curr_lng:
                route.append({
                    "lat": str(curr_lat),
                    "lng": str(curr_lng),
                    "timestamp": timezone.now().isoformat()
                })

                SampleCollectorLocation.objects.filter(location_id=item.location_id).update(
                    location_history=json.dumps(route) if isinstance(item.location_history, str) else route
                )

                return JsonResponse({"success": True, "message": "Location updated"})

            return JsonResponse({"success": False, "message": "No valid data"}, status=400)

        except SampleCollectorLocation.DoesNotExist:
            return JsonResponse({"success": False, "message": "No active tracking"}, status=404)

        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)}, status=500)

        

@api_view(['GET'])
def get_active_collectors(request):
    """Get all currently active collectors for live tracking"""
    try:
        today = datetime.now().date()
        active_collectors = SampleCollectorLocation.objects.filter(
            date=today,
            endTime__isnull=True,
            startTime__isnull=False
        )
        
        data_list = []
        for item in active_collectors:
            resp = format_location_response(item)
            data_list.append({
                'sampleCollector': resp['sampleCollector'],
                'currentLatitude': resp['currentLatitude'],
                'currentLongitude': resp['currentLongitude'],
                'startTime': resp['startTime'],
                'lastUpdated': resp['lastUpdated'],
                'distance_travelled': resp['distance_travelled']
            })
            
        return JsonResponse({
            'success': True,
            'data': data_list
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error retrieving active collectors: {str(e)}'
        }, status=500)

@api_view(['GET'])
def get_collector_route(request):
    """Get the complete route for a collector on a specific date"""
    try:
        sample_collector = request.GET.get('sampleCollector')
        date = request.GET.get('date')
        
        if not sample_collector or not date:
            return JsonResponse({
                'success': False,
                'message': 'sampleCollector and date parameters are required'
            }, status=400)
        
        try:
            date_obj = datetime.strptime(date, '%Y-%m-%d').date()
            
            items = SampleCollectorLocation.objects.filter(
                sampleCollector=sample_collector,
                date=date_obj
            )
            
            if items.exists():
                return JsonResponse({
                    'success': True,
                    'data': format_location_response(items.last())
                })
            else:
                return JsonResponse({
                    'success': False,
                    'message': 'No route data found for the specified collector and date'
                })
            
        except ValueError:
            return JsonResponse({
                'success': False,
                'message': 'Invalid date format. Please use YYYY-MM-DD format'
            }, status=400)
        except SampleCollectorLocation.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'No route data found for the specified collector and date'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error retrieving route data: {str(e)}'
        }, status=500)

@api_view(['GET'])
def get_live_tracking_data(request):
    """Get live tracking data for current date"""
    try:
        today = datetime.now().date()
        
        # Get all location data for today ordered by startTime desc
        today_data = SampleCollectorLocation.objects.filter(
            date=today
        ).order_by('-startTime')
        
        response_data = {
            'success': True,
            'data': [format_location_response(item) for item in today_data]
        }
        
        return JsonResponse(response_data)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error retrieving live tracking data: {str(e)}'
        }, status=500)


@api_view(['GET'])
@csrf_exempt
def sample_collector_location_history(request):
    """
    GET endpoint for admin tracking history with date-range and collector filters.
    Query params: from_date, to_date (YYYY-MM-DD), sampleCollector (optional)
    """
    try:
        from_date = request.GET.get('from_date')
        to_date = request.GET.get('to_date')
        sample_collector = request.GET.get('sampleCollector')

        if not from_date or not to_date:
            return JsonResponse({"error": "from_date and to_date are required"}, status=400)

        from_date_obj = datetime.strptime(from_date, "%Y-%m-%d").date()
        to_date_obj = datetime.strptime(to_date, "%Y-%m-%d").date()

        qs = SampleCollectorLocation.objects.filter(
            date__gte=from_date_obj,
            date__lte=to_date_obj
        )

        if sample_collector:
            qs = qs.filter(sampleCollector=sample_collector)

        qs = qs.order_by('-date', '-startTime')

        page_obj, page_meta = paginate_queryset(qs, request)

        response = [format_location_response(item) for item in page_obj]

        # NOTE: response shape changed from a bare JSON array to a paginated
        # object ({"data": [...], total_count, total_pages, current_page}) to
        # bound the payload as this admin history query can span an
        # arbitrary caller-supplied date range; update any frontend caller
        # that expected a raw array here.
        return JsonResponse({
            "data": response,
            **page_meta
        })

    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)}, status=500)
    




