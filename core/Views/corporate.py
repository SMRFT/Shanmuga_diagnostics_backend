# NOTE: This module has been split into a package for maintainability.
#
# All code that used to live in this file (core/Views/corporate.py) has
# moved to core/Views/corporate/ (a package), split as follows:
#
#   core/Views/corporate/common.py        - shared helper functions/constants
#   core/Views/corporate/samples.py       - batch/sample transfer views
#   core/Views/corporate/reports.py       - overall/approval/health reports,
#                                            investigation lookups
#   core/Views/corporate/investigation.py - batch investigation/approval
#                                            workflows
#   core/Views/corporate/billing.py       - credit billing & invoices
#   core/Views/corporate/__init__.py      - re-exports every public name so
#                                            `from .Views import corporate`
#                                            and `corporate.<name>` usages in
#                                            core/urls.py keep working
#                                            unchanged.
#
# Because Python resolves a package directory (core/Views/corporate/) in
# preference to a same-named module file (core/Views/corporate.py) when both
# exist, this stub file is dead code and is never imported. It is kept only
# so nobody accidentally edits stale logic here.
#
# It is safe to delete this file once you've verified the application runs
# correctly against the new core/Views/corporate/ package.
