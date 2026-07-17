"""
core/Views/report package.

This package replaces the original flat core/Views/report.py module.
core/urls.py imports this as `from .Views import ... report` and then
accesses view functions as module attributes, e.g. `report.overall_report`.
Re-exporting every name below preserves that exact attribute-access style
with ZERO changes required to core/urls.py.

Submodules:
  - dispatch_report.py      : get_department_status, overall_report, update_dispatch_status
  - patient_test_details.py : patient_test_sorting, get_patient_test_details
  - notifications.py        : send_email, send_approval_email
  - billing_reports.py      : b2b_ledger_report, get_home_collection_report
"""
from .dispatch_report import get_department_status, overall_report, update_dispatch_status
from .patient_test_details import patient_test_sorting, get_patient_test_details
from .notifications import send_email, send_approval_email
from .billing_reports import b2b_ledger_report, get_home_collection_report

__all__ = [
    "get_department_status",
    "overall_report",
    "update_dispatch_status",
    "patient_test_sorting",
    "get_patient_test_details",
    "send_email",
    "send_approval_email",
    "b2b_ledger_report",
    "get_home_collection_report",
]
