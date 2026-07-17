from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
import json
from core.mongo_client import get_client
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from core.models import TestValue
from bson import json_util
import gridfs
import base64
from bson.objectid import ObjectId
import re
from datetime import timezone, timedelta
from core.pagination import paginate_queryset


load_dotenv()
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# HELPER: Build CHC-test status map from core_investigation
#
# core_investigation.test_results is a list like:
#   [{"test_id": "CHCT006", "test_name": "PFT", "report": "...", "files": [...], "notes": "..."}, ...]
#
# Returns a dict keyed by barcode → {test_id → {report, files, notes, has_report, has_file}}
# ──────────────────────────────────────────────────────────────────────────────
def _build_chc_status_from_investigation(investigation_records):
    """
    Returns: { barcode_str: { test_id_str: { has_report, has_file, report, notes, files, inv_status } } }
    Now also carries the top-level investigation status per barcode.
    """
    result = {}
    for inv in investigation_records:
        bc = str(inv.get("barcode", ""))
        if not bc:
            continue

        inv_status = inv.get("status", "").strip().lower()  # ← capture status

        test_results = inv.get("test_results", [])
        if isinstance(test_results, str):
            try:
                test_results = json.loads(test_results)
            except Exception:
                test_results = []

        result.setdefault(bc, {})

        for tr in (test_results or []):
            tid = str(tr.get("test_id", ""))
            if not tid:
                continue
            report = tr.get("report", "")
            files = tr.get("files", [])
            notes = tr.get("notes", "")
            has_report = bool(report and report.strip())
            has_file = bool(files)

            result[bc][tid] = {
                "has_report": has_report,
                "has_file": has_file,
                "report": report,
                "notes": notes,
                "files": files,
                "inv_status": inv_status,  # ← store it per test entry
            }

    return result


# ──────────────────────────────────────────────────────────────────────────────
# HELPER: Determine overall CHC approval status for a barcode
#
# "All Approved" means every chctest has either a report OR a file.
# Returns (status_string, pending_list, approved_list)
# ──────────────────────────────────────────────────────────────────────────────
def _chc_approval_status(chc_tests, chc_status_map):
    """
    chc_tests: list of {"testname": ..., "test_id": ...} from billing.chctestdetails
    chc_status_map: {test_id: {has_report, has_file, ...}} for one barcode
    Returns: (overall_status, pending_names, approved_names)
    """
    if not chc_tests:
        return "No CHC Tests", [], []

    pending = []
    approved = []
    for t in chc_tests:
        tid = str(t.get("test_id", ""))
        tname = t.get("testname", tid)
        info = chc_status_map.get(tid, {})
        if info.get("has_report") or info.get("has_file"):
            approved.append(tname)
        else:
            pending.append(tname)

    if not pending:
        return "All Approved", pending, approved
    if approved:
        return "Partial", pending, approved
    return "Pending", pending, approved


def get_department_status_corporate(test_list, employee_id, sample_status_map, test_value_map):
    """
    Determine status for each department based on test details
    Returns dict: {department_name: status}
    """
    department_status = {}

    tests_by_dept = {}
    for test in test_list:
        dept = test.get('department', 'N/A')
        if dept and dept != 'N/A':
            if dept not in tests_by_dept:
                tests_by_dept[dept] = []
            tests_by_dept[dept].append(test)

    if not tests_by_dept:
        return {}

    for dept, tests in tests_by_dept.items():
        dept_test_ids = {t.get('test_id') for t in tests if t.get('test_id')}

        all_test_values = test_value_map.get(employee_id, {}).get('testdetails', [])
        dept_test_values = [tv for tv in all_test_values
                            if tv.get('test_id') in dept_test_ids and not tv.get('rerun', False)]

        sample_tests = sample_status_map.get(employee_id, [])
        dept_samples = [st for st in sample_tests if st.get('test_id') in dept_test_ids]

        if dept_test_values:
            def has_test_values(test):
                parameters = test.get("parameters", [])
                if not parameters:
                    return bool(test.get("value"))
                return any(
                    param.get("value") is not None and str(param.get("value")).strip() != ""
                    for param in parameters
                )

            all_tested = all(has_test_values(tv) for tv in dept_test_values)
            approved_test_ids = {tv.get('test_id') for tv in dept_test_values if tv.get('approve', False)}
            all_approved = dept_test_ids.issubset(approved_test_ids) and len(approved_test_ids) > 0
            approved_dept_tests = [tv for tv in dept_test_values if tv.get('approve', False)]
            all_dispatched = all(tv.get('dispatch', False) for tv in approved_dept_tests) if approved_dept_tests else False

            if all_dispatched and all_approved:
                department_status[dept] = 'Dispatched'
            elif all_approved:
                department_status[dept] = 'Approved'
            elif all_tested:
                department_status[dept] = 'Tested'
            else:
                if dept_samples:
                    all_received = all(t.get('samplestatus') == 'Received' for t in dept_samples)
                    if all_received:
                        department_status[dept] = 'Received'
                    else:
                        department_status[dept] = 'In Progress'
                else:
                    department_status[dept] = 'In Progress'
        elif dept_samples:
            all_collected = all(t.get('samplestatus') == 'Collected' for t in dept_samples)
            all_transferred = all(t.get('samplestatus') == 'Transferred' for t in dept_samples)
            all_received = all(t.get('samplestatus') == 'Received' for t in dept_samples)

            if all_received:
                department_status[dept] = 'Received'
            elif all_transferred:
                department_status[dept] = 'Transferred'
            elif all_collected:
                department_status[dept] = 'Collected'
            else:
                department_status[dept] = 'Pending'
        else:
            department_status[dept] = 'Pending'

    return department_status


# ── HELPER: Check if a notes string is a whitelisted normal statement ─────────
NORMAL_NOTES_WHITELIST = {
    "Normal Study.",
    "No significant finding in the lungs or mediastinum.",
    "No significant abnormality detected.",
    "Normal Study within normal limits.",
}

NORMAL_VITAL_STATUSES = {"BP_status", "bmi_status", "spo2_status"}


def _is_normal_notes(notes: str) -> bool:
    if not notes or not notes.strip():
        return False
    return notes.strip() in NORMAL_NOTES_WHITELIST  # no .lower()


def _is_vitals_normal(vitals: dict) -> bool:
    """
    Returns True only when all three vital status fields are present
    and each equals "Normal" (case-insensitive).
    Returns False if any field is missing, empty, or not "Normal".
    """
    if not vitals or not isinstance(vitals, dict):
        return False

    for key in NORMAL_VITAL_STATUSES:
        value = vitals.get(key, "")
        if not value or str(value).strip().lower() != "normal":
            return False

    return True


def _compute_corporate_approval_status(
    bc,
    approval_status_map,
    chc_tests_with_status,
    lab_test_values,
    no_of_tests,
    investigation_status,
    vitals=None,
):
    chc_all_normal = (
        investigation_status == "approved"
        and len(chc_tests_with_status) > 0
        and all(_is_normal_notes(ct.get("notes", "")) for ct in chc_tests_with_status)
    )
    valid_lab_tests = [
        t for t in lab_test_values
        if not t.get("rerun", False) and t.get("approve", False)
    ]
    lab_all_normal = (
        no_of_tests > 0
        and len(valid_lab_tests) >= no_of_tests
        and all(t.get("status", "").strip().lower() == "normal" for t in valid_lab_tests)
    )
    vitals_all_normal = _is_vitals_normal(vitals)

    if chc_all_normal and lab_all_normal and vitals_all_normal:
        return "Approved", "auto", None

    if bc and bc in approval_status_map:
        approver_name = approval_status_map[bc].get("approved_by_name", "Unknown")
        return "Approved", "manual", approver_name

    return "Pending", "none", None
