# PoBuSA stats_views.py — v1.0.0
# Powers Phase 2, View 1 (Main/Landing): Today/This-week sales + buy-in stats.
#
# SECURITY NOTE: no permission check right now, matching branding_views.py's
# current stance — no login flow/frontend auth exists yet. MUST be re-secured
# (proper auth login flow + IsStoreMember + has_store_access) before this is
# ever exposed to a real Client or the public internet.

from datetime import timedelta

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Sum, Count
from django.utils import timezone

from .models import Store, Sale, Invoice


def _range_totals(sales_qs, invoices_qs, start, end):
    """start/end are local dates (inclusive). Returns the stats dict for one window."""
    sales_in_range = sales_qs.filter(date__date__gte=start, date__date__lte=end, voided=False)
    sales_agg = sales_in_range.aggregate(count=Count("id"), revenue=Sum("total"))

    invoices_in_range = invoices_qs.filter(date__gte=start, date__lte=end, voided=False)
    invoices_agg = invoices_in_range.aggregate(count=Count("id"), spend=Sum("total_paid"))

    return {
        "sales_count": sales_agg["count"] or 0,
        "sales_revenue": sales_agg["revenue"] or 0,
        "buyins_count": invoices_agg["count"] or 0,
        "buyins_spend": invoices_agg["spend"] or 0,
    }


@api_view(["GET"])
def store_stats(request, store_id):
    """GET /api/pobusa/stores/<store_id>/stats/
    Returns today's and this week's (Monday-to-today) sales and buy-in
    totals for the Main/Landing view. Voided sales/invoices never count."""
    try:
        store = Store.objects.get(pk=store_id)
    except Store.DoesNotExist:
        return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)

    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())  # Monday of this week

    sales_qs = Sale.objects.filter(store=store)
    invoices_qs = Invoice.objects.filter(store=store)

    return Response(
        {
            "store_id": store.id,
            "today": _range_totals(sales_qs, invoices_qs, today, today),
            "this_week": _range_totals(sales_qs, invoices_qs, week_start, today),
        }
    )
