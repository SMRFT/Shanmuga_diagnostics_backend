# core/Views/testapproval package
#
# This package replaces the former flat core/Views/testapproval.py module.
# Every top-level function that used to live in that file is re-exported
# here so that existing code doing `from .Views import testapproval` and then
# calling e.g. `testapproval.get_test_values(...)` keeps working unchanged.
#
# Submodules:
#   - common.py  : normalize_testname (shared helper used by listing.py)
#   - listing.py : get_test_values, get_approved_values
#   - actions.py : approve_test_detail, rerun_test_detail, edit_test_value

from .common import normalize_testname
from .listing import get_test_values, get_approved_values
from .actions import approve_test_detail, rerun_test_detail, edit_test_value

__all__ = [
    "normalize_testname",
    "get_test_values",
    "get_approved_values",
    "approve_test_detail",
    "rerun_test_detail",
    "edit_test_value",
]
