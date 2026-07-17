from datetime import datetime
from rest_framework.decorators import api_view
from urllib.parse import quote_plus
from core.mongo_client import get_client
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from django.utils import timezone
from django.conf import settings
from django.utils.timezone import make_aware
from datetime import datetime
from django.conf import settings
import json
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from ...models import TestValue,Patient,Hmsbarcode
from django.http import JsonResponse
from datetime import datetime
import os, json
from django.utils.timezone import make_aware
from dotenv import load_dotenv
load_dotenv()

from urllib.parse import unquote_plus
import re
from core.pagination import paginate_queryset

# 🟢 3️⃣ Process testname filter once
def normalize_testname(name):
    if not name:
        return ""
    normalized = re.sub(r'\s+', ' ', name.strip().lower())
    normalized = normalized.replace('&', 'and').replace('/', ' ')
    return normalized
