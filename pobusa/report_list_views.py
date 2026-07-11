# PoBuSA report_list_views.py — v1.0.0
# Lets the frontend list generated DailyReportFile records and trigger a
# send, without needing to go into Django admin. Uses the same
# email_service.send_report_file() as the admin action, so both paths stay
# in sync — sending is still fundamentally a manual click, just from either
# the admin or the frontend now.

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .report_models import DailyReportFile
from .email_service import send_report_file


@api_view(["GET"])
def list_reports(request, store_id):
    """GET /api/pobusa/reports/<store_id>/?sent=false
    Optional ?sent=true/false filter."""
    reports = DailyReportFile.objects.filter(store_id=store_id).order_by("-generated_at")

    sent_param = request.query_params.get("sent")
    if sent_param is not None:
        reports = reports.filter(sent=(sent_param.lower() == "true"))

    data = [
        {
            "id": r.id,
            "period_type": r.period_type,
            "period_start": r.period_start,
            "period_end": r.period_end,
            "generated_at": r.generated_at,
            "sent": r.sent,
            "sent_at": r.sent_at,
            "sent_to": r.sent_to,
            "pdf_url": r.pdf_file.url if r.pdf_file else None,
        }
        for r in reports
    ]
    return Response(data)


@api_view(["POST"])
def send_report(request, report_id):
    """POST /api/pobusa/reports/<report_id>/send/
    Body: {"to_email": "someone@example.com"} — same manual-send action as
    the Django admin "Send selected reports via email" button."""
    try:
        report = DailyReportFile.objects.get(pk=report_id)
    except DailyReportFile.DoesNotExist:
        return Response({"error": "Report not found"}, status=status.HTTP_404_NOT_FOUND)

    if report.sent:
        return Response({"error": "Report already sent"}, status=status.HTTP_400_BAD_REQUEST)

    to_email = request.data.get("to_email")
    if not to_email:
        return Response({"error": "to_email is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        send_report_file(report, to_email=to_email)
    except Exception as e:
        return Response({"error": f"Send failed: {str(e)}"}, status=status.HTTP_502_BAD_GATEWAY)

    return Response({"sent": True, "sent_to": to_email})
