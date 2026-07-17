"""
Shared pagination helper for function-based DRF views in core/Views/.

This mirrors the manual pagination convention already established in
core/Views/patients.py (see get_patient_list / patient_record_dashboard):
plain Django `Paginator`, 1-indexed `page` query param, `limit` query param
for page size, and a small metadata block (total_pages/current_page/
total_count) merged into the JSON response.

Usage:
    from core.pagination import paginate_queryset, DEFAULT_PAGE_SIZE

    page_obj, meta = paginate_queryset(queryset_or_list, request)
    serializer = SomeSerializer(page_obj, many=True)
    return Response({
        "success": True,
        "data": serializer.data,
        **meta,
    })
"""

from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

DEFAULT_PAGE_SIZE = 50


def paginate_queryset(queryset_or_list, request, default_limit=DEFAULT_PAGE_SIZE):
    """
    Paginate a queryset or plain list using the page/limit convention used
    across core/Views/.

    Returns:
        (page_obj, meta) where `page_obj` is the Django Page object (iterate
        it or pass it to a serializer with many=True) and `meta` is a dict
        with keys `total_pages`, `current_page`, `total_count` ready to be
        merged (via **meta) into a Response payload.
    """
    try:
        page = int(request.GET.get('page', 1))
    except (TypeError, ValueError):
        page = 1

    try:
        limit = int(request.GET.get('limit', request.GET.get('page_size', default_limit)))
    except (TypeError, ValueError):
        limit = default_limit

    if limit <= 0:
        limit = default_limit

    paginator = Paginator(queryset_or_list, limit)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else paginator.page(1)

    meta = {
        "total_pages": paginator.num_pages,
        "current_page": page_obj.number,
        "total_count": paginator.count,
    }
    return page_obj, meta
