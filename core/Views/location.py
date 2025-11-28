from rest_framework.response import Response
from django.http import JsonResponse
from datetime import datetime
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import json
import math

from ..models import SampleCollectorLocation


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
                response = []

                for item in all_data:
                    route = item.location_history or []

                    response.append({
                        "id": str(item.id),
                        "sampleCollector": item.sampleCollector,
                        "date": item.date.isoformat() if item.date else None,
                        "latitudeStart": item.latitudeStart,
                        "longitudeStart": item.longitudeStart,
                        "latitudeEnd": item.latitudeEnd,
                        "longitudeEnd": item.longitudeEnd,
                        "currentLatitude": route[-1]["lat"] if route else item.latitudeStart,
                        "currentLongitude": route[-1]["lng"] if route else item.longitudeStart,
                        "distance_travelled": item.distance_travelled or "0.00",
                        "startTime": item.startTime.isoformat() if item.startTime else None,
                        "endTime": item.endTime.isoformat() if item.endTime else None,
                        "isActive": bool(item.latitudeStart and not item.latitudeEnd),
                        "lastUpdated": item.startTime.isoformat() if item.startTime else timezone.now().isoformat(),
                        "routePoints": route
                    })

                return JsonResponse(response, safe=False)

            # ---------------------------
            # GET BY DATE ONLY
            # ---------------------------
            if date and not sample_collector:
                date_obj = datetime.strptime(date, "%Y-%m-%d").date()
                records = SampleCollectorLocation.objects.filter(date=date_obj).order_by('-startTime')

                response = []
                for item in records:
                    route = item.location_history or []
                    response.append({
                        "id": str(item.id),
                        "sampleCollector": item.sampleCollector,
                        "date": item.date.isoformat(),
                        "latitudeStart": item.latitudeStart,
                        "longitudeStart": item.longitudeStart,
                        "latitudeEnd": item.latitudeEnd,
                        "longitudeEnd": item.longitudeEnd,
                        "currentLatitude": route[-1]["lat"] if route else item.latitudeStart,
                        "currentLongitude": route[-1]["lng"] if route else item.longitudeStart,
                        "distance_travelled": item.distance_travelled or "0.00",
                        "startTime": item.startTime.isoformat(),
                        "endTime": item.endTime.isoformat() if item.endTime else None,
                        "isActive": bool(item.latitudeStart and not item.latitudeEnd),
                        "routePoints": route
                    })
                return JsonResponse(response, safe=False)

            # ---------------------------
            # GET BY DATE + COLLECTOR
            # ---------------------------
            date_obj = datetime.strptime(date, "%Y-%m-%d").date()
            item = SampleCollectorLocation.objects.get(sampleCollector=sample_collector, date=date_obj)

            route = item.location_history or []
            return JsonResponse([{
                "id": str(item.id),
                "sampleCollector": item.sampleCollector,
                "date": item.date.isoformat(),
                "latitudeStart": item.latitudeStart,
                "longitudeStart": item.longitudeStart,
                "latitudeEnd": item.latitudeEnd,
                "longitudeEnd": item.longitudeEnd,
                "currentLatitude": route[-1]["lat"] if route else item.latitudeStart,
                "currentLongitude": route[-1]["lng"] if route else item.longitudeStart,
                "distance_travelled": item.distance_travelled or "0.00",
                "startTime": item.startTime.isoformat(),
                "endTime": item.endTime.isoformat() if item.endTime else None,
                "isActive": bool(item.latitudeStart and not item.latitudeEnd),
                "routePoints": route
            }], safe=False)

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

            location, created = SampleCollectorLocation.objects.update_or_create(
                sampleCollector=sample_collector,
                date=date_obj,
                defaults={
                    "latitudeStart": lat,
                    "longitudeStart": lng,
                    "startTime": timezone.now(),
                    "latitudeEnd": None,
                    "longitudeEnd": None,
                    "endTime": None,
                    "distance_travelled": None,
                    "location_history": [{
                        "lat": lat,
                        "lng": lng,
                        "timestamp": timezone.now().isoformat()
                    }]
                }
            )

            return JsonResponse({
                "success": True,
                "message": "Tracking started",
                "data": {"id": str(location.id)}
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

            date_obj = datetime.strptime(date, "%Y-%m-%d").date()

            item = SampleCollectorLocation.objects.get(sampleCollector=sample_collector, date=date_obj)

            route = item.location_history or []

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
                        route[i-1]["lat"], route[i-1]["lng"],
                        route[i]["lat"], route[i]["lng"]
                    )

                item.latitudeEnd = end_lat
                item.longitudeEnd = end_lng
                item.endTime = timezone.now()
                item.distance_travelled = f"{total:.2f}"
                item.location_history = route
                item.save()

                return JsonResponse({
                    "success": True,
                    "message": "Tracking ended",
                    "distance": item.distance_travelled
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

                item.location_history = route
                item.save()

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
            isActive=True
        ).values(
            'sampleCollector',
            'currentLatitude',
            'currentLongitude',
            'startTime',
            'lastUpdated',
            'distance_travelled'
        )
        
        return JsonResponse({
            'success': True,
            'data': list(active_collectors)
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
            
            location_data = SampleCollectorLocation.objects.get(
                sampleCollector=sample_collector,
                date=date_obj
            )
            
            route_points = []
            if location_data.routePoints:
                try:
                    route_points = json.loads(location_data.routePoints)
                except json.JSONDecodeError:
                    route_points = []
            
            return JsonResponse({
                'success': True,
                'data': {
                    'sampleCollector': location_data.sampleCollector,
                    'date': location_data.date,
                    'routePoints': route_points,
                    'distance_travelled': location_data.distance_travelled,
                    'totalDuration': location_data.totalDuration,
                    'isActive': location_data.isActive
                }
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
        
        # Get all location data for today
        today_data = SampleCollectorLocation.objects.filter(
            date=today
        ).order_by('-lastUpdated')
        
        response_data = {
            'success': True,
            'data': []
        }
        
        for location_data in today_data:
            # Parse route points if available
            route_points = []
            if location_data.routePoints:
                try:
                    route_points = json.loads(location_data.routePoints)
                except json.JSONDecodeError:
                    route_points = []
            
            response_data['data'].append({
                'id': location_data.id,
                'sampleCollector': location_data.sampleCollector,
                'date': location_data.date,
                'latitudeStart': location_data.latitudeStart,
                'longitudeStart': location_data.longitudeStart,
                'latitudeEnd': location_data.latitudeEnd,
                'longitudeEnd': location_data.longitudeEnd,
                'currentLatitude': location_data.currentLatitude,
                'currentLongitude': location_data.currentLongitude,
                'distance_travelled': location_data.distance_travelled or "0.00",
                'startTime': location_data.startTime.isoformat() if location_data.startTime else None,
                'endTime': location_data.endTime.isoformat() if location_data.endTime else None,
                'totalDuration': location_data.totalDuration,
                'isActive': location_data.isActive,
                'lastUpdated': location_data.lastUpdated.isoformat(),
                'routePoints': route_points
            })
        
        return JsonResponse(response_data)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error retrieving live tracking data: {str(e)}'
        }, status=500)