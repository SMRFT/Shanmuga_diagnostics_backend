# This module has been split into the core/Views/testapproval/ package.
#
# The original flat core/Views/testapproval.py (~1233 lines) was broken up
# into smaller, logically-grouped files:
#   - core/Views/testapproval/common.py  : normalize_testname (shared helper)
#   - core/Views/testapproval/listing.py : get_test_values, get_approved_values
#   - core/Views/testapproval/actions.py : approve_test_detail, rerun_test_detail, edit_test_value
#   - core/Views/testapproval/__init__.py: re-exports all of the above so
#     `from .Views import testapproval` + `testapproval.get_test_values(...)`
#     (as used in core/urls.py) continues to work unchanged.
#
# Because Python resolves a package directory (testapproval/) before a
# same-named module file (testapproval.py) on sys.path, this file is no
# longer imported anywhere — it is dead code kept only as a placeholder.
# It should be deleted once the split has been verified to work.
