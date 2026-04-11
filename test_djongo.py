import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "shanmuga_backend.settings")
import sys
sys.path.append(r"d:\Shanmuga Diagnostics\Shanmuga_diagnostics_backend")

try:
    django.setup()
    from core.models import SampleCollectorLocation
    qs = SampleCollectorLocation.objects.filter(is_location_active__in=[True]).order_by('location_id')
    print("QUERY COUNT:", qs.count())
    print("SUCCESS")
except Exception as e:
    import traceback
    traceback.print_exc()
