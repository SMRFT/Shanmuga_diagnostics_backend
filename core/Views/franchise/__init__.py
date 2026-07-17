# core/Views/franchise package
#
# This package replaces the former flat core/Views/franchise.py module.
# Every top-level function that used to live in that file is re-exported
# here so that existing code doing `from .Views import franchise` (see
# core/urls.py) and then calling e.g. `franchise.get_batch_generation_data(...)`
# keeps working unchanged.
#
# Submodules:
#   - samples.py : get_franchise_sample, update_franchise_sample
#   - batches.py : get_batch_generation_data, update_batch_received_status
#   - reports.py : get_department_status_franchise (helper),
#                  franchise_overall_report, franchise_patient_test_details,
#                  get_test_value_for_franchise

from .samples import get_franchise_sample, update_franchise_sample
from .batches import get_batch_generation_data, update_batch_received_status
from .reports import (
    get_department_status_franchise,
    franchise_overall_report,
    franchise_patient_test_details,
    get_test_value_for_franchise,
)

__all__ = [
    "get_franchise_sample",
    "update_franchise_sample",
    "get_batch_generation_data",
    "update_batch_received_status",
    "get_department_status_franchise",
    "franchise_overall_report",
    "franchise_patient_test_details",
    "get_test_value_for_franchise",
]
