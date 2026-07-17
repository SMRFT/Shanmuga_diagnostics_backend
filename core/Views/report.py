# NOTE: This module has been split into the core/Views/report/ package.
#
# Python resolves package directories (with an __init__.py) before
# same-named .py files on the import path, so core/Views/report/ is what
# actually gets imported as `core.Views.report` — this stub file is dead
# code left in place only because this environment has no delete tool.
#
# The original ~1872-line contents now live here:
#   core/Views/report/__init__.py            - re-exports every public
#                                               name below so that
#                                               `from .Views import report`
#                                               + `report.<name>` in
#                                               core/urls.py keeps working
#                                               unchanged.
#   core/Views/report/dispatch_report.py      - get_department_status,
#                                               overall_report,
#                                               update_dispatch_status
#   core/Views/report/patient_test_details.py - patient_test_sorting,
#                                               get_patient_test_details
#   core/Views/report/notifications.py        - send_email,
#                                               send_approval_email
#   core/Views/report/billing_reports.py      - b2b_ledger_report,
#                                               get_home_collection_report
#
# This file is safe to delete manually once the split has been verified
# to work (recommended cleanup — it currently serves no purpose and is
# never imported).
