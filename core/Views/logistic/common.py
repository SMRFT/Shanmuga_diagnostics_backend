"""
Shared helper functions used by multiple sub-modules of the core/Views/logistic
package (route analysis, bus fare photo storage, and the RouteSetup serializer
in core/serializers.py, which imports get_clinical_name_map and _as_list
directly from this package).
"""
import os
import json
import logging
from pymongo import MongoClient
import certifi
from gridfs import GridFS

from ...models import ClinicalName

logger = logging.getLogger(__name__)


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
