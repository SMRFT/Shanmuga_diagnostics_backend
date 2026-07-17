# This module was split into the `core/Views/testvalue/` package on 2026-07-15.
#
# Python resolves packages before same-named modules, so
# `core/Views/testvalue/` (the directory) is what actually gets imported
# wherever code does `from .Views import testvalue` — this stale flat file
# is no longer loaded and is kept only so nobody edits dead code by mistake.
#
# New submodules (all functions re-exported via testvalue/__init__.py):
#   - core/Views/testvalue/sample_status_views.py
#       get_samplestatus_testvalue, worklist_view
#   - core/Views/testvalue/compare_views.py
#       resolve_gender_fields, process_test_data, compare_test_details
#   - core/Views/testvalue/save_views.py
#       update_processing_status, save_test_value
#
# core/urls.py required ZERO changes — it still does
# `from .Views import ... testvalue ...` and calls `testvalue.<name>(...)`.
#
# Recommended: once you've verified the app runs correctly against the new
# package, delete this file manually.
