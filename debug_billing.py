
import os
import django
import sys
from bson import ObjectId

# Add the project root to sys.path
sys.path.append(os.getcwd())

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "shanmuga_backend.settings")
django.setup()

from core.models import Billing
from core.serializers import BillingSerializer

try:
    print("Fetching one Billing object...")
    billing_obj = Billing.objects.first()
    if billing_obj:
        print(f"Found Billing Object: {billing_obj}")
        print("Dir(obj):", dir(billing_obj))
        
        if hasattr(billing_obj, 'id'):
            print("Object HAS 'id' attribute:", billing_obj.id)
        else:
            print("Object does NOT have 'id' attribute.")

        if hasattr(billing_obj, '_id'):
            print("Object HAS '_id' attribute:", billing_obj._id)
        else:
            print("Object does NOT have '_id' attribute.")

        print("\nAttempting Serialization with current serializer...")
        serializer = BillingSerializer(billing_obj)
        print("Serialized Data:", serializer.data)
    else:
        print("No Billing objects found in DB.")

except Exception as e:
    print(f"\nCAUGHT EXCEPTION: {e}")
    import traceback
    traceback.print_exc()
