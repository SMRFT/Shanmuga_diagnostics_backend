# This flat module has been superseded by the core/Views/franchise/ package.
#
# The original ~1671-line file was split into:
#   - core/Views/franchise/samples.py  : get_franchise_sample, update_franchise_sample
#   - core/Views/franchise/batches.py  : get_batch_generation_data, update_batch_received_status
#   - core/Views/franchise/reports.py  : get_department_status_franchise,
#                                         franchise_overall_report,
#                                         franchise_patient_test_details,
#                                         get_test_value_for_franchise
#   - core/Views/franchise/__init__.py : re-exports every name above so that
#                                         `from .Views import franchise` and
#                                         `franchise.<name>(...)` (as used in
#                                         core/urls.py) keep working unchanged.
#
# Because Python resolves a package directory (franchise/) before a
# same-named module (franchise.py) on sys.path, this file is no longer
# imported by anything — the franchise/ package above is what actually
# gets loaded. This stub is kept only so nobody accidentally edits stale
# dead code here. It is safe to delete this file entirely once the app
# has been verified to run correctly against the new package.
