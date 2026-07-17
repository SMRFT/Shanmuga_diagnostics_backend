"""
core/Views/logistic package.

This package replaces the former single-file core/Views/logistic.py module.
All public names that used to live in that flat module are re-exported here
so that existing `from .Views import logistic` / `logistic.<name>(...)`
usage in core/urls.py (module-attribute access) keeps working unchanged,
and so that direct submodule-style imports such as:

    from .Views.logistic import get_clinical_name_map, _as_list

(used inside core/serializers.py) continue to resolve correctly.

Submodules:
- task_management.py : logistics task CRUD/assign/accept/reject/pickup/reassign,
                       dashboard and TAT report views
- route_analysis.py  : route setup, route analysis start/end/mark-visit/status,
                       route image retrieval, admin report
- bus_fare.py        : bus fare entry CRUD and photo retrieval
- employees.py       : B2B / B2B lab employee listing
- complaints.py       : customer complaint CRUD
- common.py          : shared helpers (_as_list, _gridfs, get_clinical_name_map)
                       used by 2+ of the above submodules and by
                       core/serializers.py
"""

from .common import get_clinical_name_map, _as_list, _gridfs

from .task_management import (
    create_logistics,
    logistics_by_collector,
    accept_task,
    reject_task,
    pickup_task,
    reassign_task,
    logistics_dashboard,
    logistics_tat_report,
)

from .route_analysis import (
    routesetup,
    get_todays_route_status,
    route_analysis_admin_report,
    start_route_analysis,
    end_route_analysis,
    get_route_analysis,
    get_active_route_analysis,
    mark_visit,
    get_route_image,
)

from .bus_fare import (
    bus_fare,
    bus_fare_photo,
)

from .employees import (
    get_b2b_employees,
    get_b2b_lab_employees,
)

from .complaints import (
    customer_complaints,
)

__all__ = [
    "get_clinical_name_map",
    "_as_list",
    "_gridfs",
    "create_logistics",
    "logistics_by_collector",
    "accept_task",
    "reject_task",
    "pickup_task",
    "reassign_task",
    "logistics_dashboard",
    "logistics_tat_report",
    "routesetup",
    "get_todays_route_status",
    "route_analysis_admin_report",
    "start_route_analysis",
    "end_route_analysis",
    "get_route_analysis",
    "get_active_route_analysis",
    "mark_visit",
    "get_route_image",
    "bus_fare",
    "bus_fare_photo",
    "get_b2b_employees",
    "get_b2b_lab_employees",
    "customer_complaints",
]
