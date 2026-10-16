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

def analyze_route(route):
    """
    Cleans GPS points, filters out cell-tower bounce spikes & stationary drift,
    computes accurate distance for salary accounting, and extracts stoppage intervals.
    Returns: (cleaned_distance_km, cleaned_points, stoppages, status_info)
    """
    if not route or not isinstance(route, list):
        return "0.00", [], [], {"status": "no_data", "idle_minutes": 0}

    # 1. Reject invalid, null, zero or out-of-range coordinates
    valid = []
    for p in route:
        try:
            lat = float(p.get("lat") or p.get("latitude") or 0)
            lng = float(p.get("lng") or p.get("longitude") or 0)
            if lat != 0.0 and lng != 0.0 and -90 <= lat <= 90 and -180 <= lng <= 180:
                valid.append({
                    "lat": str(lat),
                    "lng": str(lng),
                    "timestamp": p.get("timestamp")
                })
        except (ValueError, TypeError):
            continue

    if len(valid) < 2:
        if valid:
            return "0.00", valid, [], {"status": "stationary", "idle_minutes": 0}
        return "0.00", [], [], {"status": "no_data", "idle_minutes": 0}

    # 2. De-spike: Filter cell-tower bounce spikes (A -> B -> C where A and C are close, but B jumped far away)
    de_spiked = []
    i = 0
    while i < len(valid):
        curr = valid[i]
        if i + 2 < len(valid):
            nxt = valid[i+1]
            nxt2 = valid[i+2]
            try:
                c_lat, c_lng = float(curr["lat"]), float(curr["lng"])
                n_lat, n_lng = float(nxt["lat"]), float(nxt["lng"])
                n2_lat, n2_lng = float(nxt2["lat"]), float(nxt2["lng"])
                d1 = calculate_distance(c_lat, c_lng, n_lat, n_lng)
                d2 = calculate_distance(n_lat, n_lng, n2_lat, n2_lng)
                d_c_n2 = calculate_distance(c_lat, c_lng, n2_lat, n2_lng)

                # If nxt jumped > 200m away, but nxt2 returns to within 75m of curr
                if d1 > 200 and d2 > 200 and d_c_n2 < 75:
                    de_spiked.append(curr)
                    i += 2  # skip nxt spike
                    continue
            except Exception:
                pass
        de_spiked.append(curr)
        i += 1

    # 3. Distance calculation with realistic bike speed ceiling (25 m/s ~ 90 km/h) & jitter rejection (< 12m)
    # Also detects stoppages (stationary within 45m for >= 5 minutes)
    clean_pts = []
    total_m = 0.0
    last_valid = None

    stoppages = []
    stop_anchor = None
    stop_start = None

    for p in de_spiked:
        p_lat = float(p["lat"])
        p_lng = float(p["lng"])

        if last_valid is None:
            last_valid = p
            clean_pts.append(p)
            stop_anchor = p
            stop_start = p.get("timestamp")
            continue

        dist = calculate_distance(float(last_valid["lat"]), float(last_valid["lng"]), p_lat, p_lng)

        t1 = parse_iso_timestamp(last_valid.get("timestamp"))
        t2 = parse_iso_timestamp(p.get("timestamp"))
        dt = abs((t2 - t1).total_seconds()) if (t1 and t2) else 0

        # Rapid burst check (< 2s and > 30m)
        if dt < 2.0 and dist > 30.0:
            continue

        # Speed ceiling: 25 m/s = 90 km/h
        if dt > 0 and (dist / dt) > 25.0:
            continue

        # Check stoppage / dwell time in same area (~45m radius)
        dist_from_anchor = calculate_distance(float(stop_anchor["lat"]), float(stop_anchor["lng"]), p_lat, p_lng)
        if dist_from_anchor < 45.0:
            # Continuing stationary period
            pass
        else:
            # Rider moved to a new place
            if stop_start and t1:
                t_stop_start = parse_iso_timestamp(stop_start)
                if t_stop_start:
                    dur_mins = (t1 - t_stop_start).total_seconds() / 60.0
                    if dur_mins >= 8.0:  # 8+ minutes stationary
                        stoppages.append({
                            "lat": stop_anchor["lat"],
                            "lng": stop_anchor["lng"],
                            "startTime": stop_start,
                            "endTime": last_valid.get("timestamp"),
                            "durationMinutes": round(dur_mins, 1),
                            "isCurrent": False
                        })
            stop_anchor = p
            stop_start = p.get("timestamp")

        # Jitter filter: ignore micro-movements < 12 meters
        if dist < 12.0:
            continue

        total_m += dist
        last_valid = p
        clean_pts.append(p)

    # Check current ongoing stoppage at the end of the route
    idle_minutes = 0.0
    is_current_stopped = False
    if stop_anchor and stop_start and last_valid:
        t_start = parse_iso_timestamp(stop_start)
        t_end = parse_iso_timestamp(last_valid.get("timestamp")) or timezone.now()
        if t_start:
            idle_minutes = max(0.0, (t_end - t_start).total_seconds() / 60.0)
            if idle_minutes >= 5.0:
                is_current_stopped = True
                stoppages.append({
                    "lat": stop_anchor["lat"],
                    "lng": stop_anchor["lng"],
                    "startTime": stop_start,
                    "endTime": last_valid.get("timestamp"),
                    "durationMinutes": round(idle_minutes, 1),
                    "isCurrent": True
                })

    status_info = {
        "status": "stationary" if is_current_stopped else "moving",
        "idle_minutes": round(idle_minutes, 1)
    }

    return f"{(total_m / 1000):.2f}", clean_pts, stoppages, status_info

def calc_route_distance_km(route):
    km, _, _, _ = analyze_route(route)
    return km

def format_location_response(item):
    route = get_route(item)
    dist_km, clean_route, stoppages, status_info = analyze_route(route)

    # Use clean despiked route if available, otherwise original
    display_route = clean_route if clean_route else route
    lat_start = display_route[0].get("lat") if display_route else None
    lng_start = display_route[0].get("lng") if display_route else None
    lat_end = display_route[-1].get("lat") if display_route and item.endTime else None
    lng_end = display_route[-1].get("lng") if display_route and item.endTime else None
    curr_lat = display_route[-1].get("lat") if display_route else lat_start
    curr_lng = display_route[-1].get("lng") if display_route else lng_start
    last_val_time = display_route[-1].get("timestamp") if display_route else None

    # Calculate total duration in seconds if both start and end exist
    total_duration = None
    if item.startTime and item.endTime:
        duration = (item.endTime - item.startTime).total_seconds()
        total_duration = str(duration)

    is_active = getattr(item, 'is_location_active', 0) == 1 if hasattr(item, 'is_location_active') else bool(item.startTime and not item.endTime)

    # Determine real-time tracking status
    final_status = "completed"
    if is_active:
        if not display_route or (lat_start == "0" and lng_start == "0"):
            final_status = "no_data"
        elif status_info.get("status") == "stationary":
            final_status = "stationary"
        else:
            final_status = "moving"

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
        "distance_travelled": dist_km,
        "startTime": item.startTime.isoformat() if item.startTime else None,
        "endTime": item.endTime.isoformat() if item.endTime else None,
        "totalDuration": total_duration,
        "isActive": is_active,
        "trackingStatus": final_status,
        "idleMinutes": status_info.get("idle_minutes", 0),
        "stoppages": stoppages,
        "lastUpdated": last_val_time or (item.startTime.isoformat() if item.startTime else timezone.now().isoformat()),
        "routePoints": display_route
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

            lat_raw = data.get("latitudeStart")
            lng_raw = data.get("longitudeStart")
            try:
                lat_f = float(lat_raw or 0)
                lng_f = float(lng_raw or 0)
            except (ValueError, TypeError):
                lat_f, lng_f = 0, 0

            if lat_f == 0.0 or lng_f == 0.0 or abs(lat_f) > 90 or abs(lng_f) > 180:
                return JsonResponse({"success": False, "message": "Invalid or unacquired starting GPS coordinates"}, status=400)

            date_obj = datetime.strptime(date, "%Y-%m-%d").date()

            new_history = [{
                "lat": str(lat_f),
                "lng": str(lng_f),
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
    




