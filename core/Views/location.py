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


def calculate_distance(lat1, lon1, lat2, lon2):
    """Haversine formula – returns distance in meters."""
    try:
        lat1, lon1, lat2, lon2 = map(
            math.radians, 
            [float(lat1), float(lon1), float(lat2), float(lon2)]
        )
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(max(0, min(1, a))))
        r = 6371000  # Earth radius in meters
        return c * r
    except (ValueError, TypeError):
        return 0.0

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

def parse_iso_timestamp(ts):
    if not ts:
        return None
    try:
        if isinstance(ts, datetime):
            return ts
        return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except Exception:
        return None

def calc_route_distance_km(route):
    """
    Computes accurate road/travel distance with:
    1. Stationary jitter rejection (ignores micro-movements < 15 meters)
    2. Outlier rejection (ignores unrealistic jumps > 140 km/h)
    3. Null / zero coordinate rejection
    """
    if not route or len(route) < 2:
        return "0.00"
    
    total_m = 0.0
    last_valid_point = None
    
    # Minimum movement threshold in meters to filter stationary GPS jitter
    MIN_MOVEMENT_METERS = 15.0
    # Maximum reasonable speed in m/s (40 m/s ~ 144 km/h)
    MAX_SPEED_MPS = 40.0

    for point in route:
        try:
            lat = float(point.get("lat") or point.get("latitude") or 0)
            lng = float(point.get("lng") or point.get("longitude") or 0)
            if not lat or not lng or lat == 0.0 or lng == 0.0:
                continue
            
            if last_valid_point is None:
                last_valid_point = {"lat": lat, "lng": lng, "timestamp": point.get("timestamp")}
                continue

            dist = calculate_distance(last_valid_point["lat"], last_valid_point["lng"], lat, lng)
            
            # Check for stationary jitter
            if dist < MIN_MOVEMENT_METERS:
                # User is stationary or micro-drifting; do not accumulate fake distance
                continue

            # Check for unreasonable teleport jumps if timestamps are present
            t1 = parse_iso_timestamp(last_valid_point.get("timestamp"))
            t2 = parse_iso_timestamp(point.get("timestamp"))
            if t1 and t2:
                time_diff = abs((t2 - t1).total_seconds())
                if time_diff > 0:
                    speed = dist / time_diff
                    if speed > MAX_SPEED_MPS and time_diff < 120:
                        # Unrealistic speed/GPS jump; ignore this outlier
                        continue

            total_m += dist
            last_valid_point = {"lat": lat, "lng": lng, "timestamp": point.get("timestamp")}
        except (ValueError, TypeError):
            continue

    return f"{(total_m / 1000):.2f}"

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

    dist_val = item.distance_travelled
    # Recalculate if not present or 0 with valid route
    if (not dist_val or dist_val == "0.00" or dist_val == "0") and len(route) > 1:
        dist_val = calc_route_distance_km(route)

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
        "distance_travelled": dist_val or "0.00",
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

                # Accurate Distance calculation with jitter & outlier filtering
                total_distance = calc_route_distance_km(route)
                
                # If the frontend/mobile passes a more accurate distance (e.g. from Google Maps Road API)
                if data.get("distance_travelled") and float(data.get("distance_travelled") or 0) > 0:
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
                try:
                    c_lat_f = float(curr_lat)
                    c_lng_f = float(curr_lng)
                except (ValueError, TypeError):
                    c_lat_f, c_lng_f = 0, 0

                # Only process valid coordinates
                if c_lat_f != 0 and c_lng_f != 0:
                    should_append = True
                    if route:
                        last_pt = route[-1]
                        try:
                            l_lat_f = float(last_pt.get("lat") or last_pt.get("latitude") or 0)
                            l_lng_f = float(last_pt.get("lng") or last_pt.get("longitude") or 0)
                            dist_from_last = calculate_distance(l_lat_f, l_lng_f, c_lat_f, c_lng_f)
                            # If moved less than 10 meters, simply update the timestamp of the last point instead of appending duplicate jitter
                            if dist_from_last < 10.0:
                                should_append = False
                                route[-1]["timestamp"] = timezone.now().isoformat()
                        except Exception:
                            should_append = True

                    if should_append:
                        route.append({
                            "lat": str(curr_lat),
                            "lng": str(curr_lng),
                            "timestamp": timezone.now().isoformat()
                        })

                    live_dist = calc_route_distance_km(route)
                    SampleCollectorLocation.objects.filter(location_id=item.location_id).update(
                        distance_travelled=live_dist,
                        location_history=json.dumps(route) if isinstance(item.location_history, str) else route
                    )

                    return JsonResponse({"success": True, "message": "Location updated", "distance": live_dist})

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

        response = [format_location_response(item) for item in qs]
        return JsonResponse(response, safe=False)

    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)}, status=500)
    




