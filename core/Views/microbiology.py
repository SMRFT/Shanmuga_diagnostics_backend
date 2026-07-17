# NOTE: This flat module has been superseded.
#
# The original ~2588-line core/Views/microbiology.py has been split into the
# core/Views/microbiology/ package (a Python package directory takes
# precedence over a same-named .py file during import resolution, so this
# stub file is never actually imported/executed while microbiology/ exists
# alongside it).
#
# New submodules (all functions re-exported via microbiology/__init__.py):
#   - core/Views/microbiology/testvalue.py
#   - core/Views/microbiology/approval.py
#   - core/Views/microbiology/reports.py
#   - core/Views/microbiology/hms_reports.py
#   - core/Views/microbiology/franchise_reports.py
#
# core/urls.py required ZERO changes: it still does
# `from .Views import ... microbiology ...` and calls
# `microbiology.mb_get_test_values(...)` etc., which now resolve through the
# package's __init__.py re-exports.
#
# This stub is safe to delete once the app has been verified to run correctly
# against the new package.
