# PoBuSA export_views.py — v1.0.0

from datetime import datetime
from django.http import HttpResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Store
from .export_service import build_sales_export, build_purchases_export


def _parse_date_params(request):
    start = request.query_params.get("start")
    end = request.query_params.get("end")
    if not start or not end:
        return None, None, "start and end query params are required (YYYY-MM-DD)"
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return None, None, "start and end must be in YYYY-MM-DD format"
    return start_date, end_date, None


@api_view(["GET"])
def export_sales_csv(request, store_id):
    """GET /api/pobusa/exports/sales/<store_id>/?start=2026-07-01&end=2026-07-31
    Downloads a CSV of all sales in the date range, formatted for accounting import."""
    start_date, end_date, error = _parse_date_params(request)
    if error:
        return Response({"error": error}, status=400)

    try:
        store = Store.objects.get(pk=store_id)
        csv_content = build_sales_export(store, start_date, end_date)
    except (Store.DoesNotExist, ValueError) as e:
        return Response({"error": str(e)}, status=404)

    response = HttpResponse(csv_content, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{store.name}_sales_{start_date}_{end_date}.csv"'
    return response


@api_view(["GET"])
def export_purchases_csv(request, store_id):
    """GET /api/pobusa/exports/purchases/<store_id>/?start=2026-07-01&end=2026-07-31
    Downloads a CSV of all buy-in invoices in the date range."""
    start_date, end_date, error = _parse_date_params(request)
    if error:
        return Response({"error": error}, status=400)

    try:
        store = Store.objects.get(pk=store_id)
        csv_content = build_purchases_export(store, start_date, end_date)
    except (Store.DoesNotExist, ValueError) as e:
        return Response({"error": str(e)}, status=404)

    response = HttpResponse(csv_content, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{store.name}_purchases_{start_date}_{end_date}.csv"'
    return response
