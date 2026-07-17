# NOTE: This module has been split into the core/Views/logistic/ package.
#
# The original ~1678-line flat module was broken up into:
#   - core/Views/logistic/common.py           (shared helpers: _as_list, _gridfs, get_clinical_name_map)
#   - core/Views/logistic/task_management.py  (create_logistics, logistics_by_collector, accept_task,
#                                               reject_task, pickup_task, reassign_task,
#                                               logistics_dashboard, logistics_tat_report)
#   - core/Views/logistic/route_analysis.py   (routesetup, get_todays_route_status,
#                                               route_analysis_admin_report, start_route_analysis,
#                                               end_route_analysis, get_route_analysis,
#                                               get_active_route_analysis, mark_visit, get_route_image)
#   - core/Views/logistic/bus_fare.py         (bus_fare, bus_fare_photo)
#   - core/Views/logistic/employees.py        (get_b2b_employees, get_b2b_lab_employees)
#   - core/Views/logistic/complaints.py       (customer_complaints)
#   - core/Views/logistic/__init__.py         (re-exports every public name above, so
#                                               `from .Views import logistic` / `logistic.<name>`
#                                               in core/urls.py and
#                                               `from .Views.logistic import get_clinical_name_map, _as_list`
#                                               in core/serializers.py keep working unchanged)
#
# Because Python resolves a package directory (core/Views/logistic/) before a
# same-named module file (core/Views/logistic.py), this stub file is never
# actually imported by the running application. It is kept only so nobody
# mistakes it for live code. It is safe to delete this file entirely once the
# application has been verified to run correctly against the new package.
