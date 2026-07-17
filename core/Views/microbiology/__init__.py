# core/Views/microbiology package
#
# This package replaces the former flat core/Views/microbiology.py module.
# Every top-level function that used to live in that file is re-exported
# here so that existing code doing `from .Views import microbiology` and then
# calling e.g. `microbiology.mb_get_test_values(...)` keeps working unchanged.
#
# Submodules:
#   - testvalue.py          : micro_biology_testvalue, mb_compare_test_details,
#                              mb_save_test_value, normalize_testname, mb_get_test_values
#   - approval.py            : mb_approve_test_detail, mb_rerun_test_detail,
#                              mb_update_dispatch_status
#   - reports.py             : mb_patient_test_sorting, mb_get_patient_test_details
#   - hms_reports.py         : hms_mb_get_patient_test_details
#   - franchise_reports.py   : franchise_mb_get_patient_test_details

from .testvalue import (
    micro_biology_testvalue,
    mb_compare_test_details,
    mb_save_test_value,
    normalize_testname,
    mb_get_test_values,
)
from .approval import (
    mb_approve_test_detail,
    mb_rerun_test_detail,
    mb_update_dispatch_status,
)
from .reports import (
    mb_patient_test_sorting,
    mb_get_patient_test_details,
)
from .hms_reports import hms_mb_get_patient_test_details
from .franchise_reports import franchise_mb_get_patient_test_details

__all__ = [
    "micro_biology_testvalue",
    "mb_compare_test_details",
    "mb_save_test_value",
    "normalize_testname",
    "mb_get_test_values",
    "mb_approve_test_detail",
    "mb_rerun_test_detail",
    "mb_update_dispatch_status",
    "mb_patient_test_sorting",
    "mb_get_patient_test_details",
    "hms_mb_get_patient_test_details",
    "franchise_mb_get_patient_test_details",
]
