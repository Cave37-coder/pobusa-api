# PoBuSA report_builder.py — v1.1.0
# Shared aggregation + PDF logic for daily and monthly reports.
# Uses reportlab (pure Python, no system dependencies) since this runs on Railway.
# v1.1.0: generation now saves a DailyReportFile record instead of returning
# bytes for immediate send — sending is a separate, manual step (see admin.py).

from decimal import Decimal
from io import BytesIO
from django.core.files.base import ContentFile
from collections import Counter

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from .models import Sale, SaleItem, Invoice, DailySalesSummary
from .report_models import DailyReportFile


def aggregate_period(store, start_date, end_date):
    """Pulls sales + buy-ins for a date range and returns the summary numbers
    used by both the PDF and the DailySalesSummary rollup."""
    sales = Sale.objects.filter(store=store, date__date__gte=start_date, date__date__lte=end_date, voided=False)
    invoices = Invoice.objects.filter(store=store, date__gte=start_date, date__lte=end_date, voided=False)

    total_sell_value = sum((s.total for s in sales), Decimal("0.00"))
    total_buy_value = sum((i.total_paid for i in invoices), Decimal("0.00"))
    transaction_count = sales.count()

    # Top items by quantity sold in the period
    item_counter = Counter()
    for item in SaleItem.objects.filter(sale__in=sales):
        item_counter[item.description] += item.quantity
    top_items = item_counter.most_common(10)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_sell_value": total_sell_value,
        "total_buy_value": total_buy_value,
        "margin": total_sell_value - total_buy_value,
        "transaction_count": transaction_count,
        "invoice_count": invoices.count(),
        "top_items": top_items,
    }


def update_daily_summary(store, date):
    """Called nightly — rolls today's numbers into DailySalesSummary.
    This is the aggregated-only telemetry hook (Section 4 of the spec)."""
    data = aggregate_period(store, date, date)
    DailySalesSummary.objects.update_or_create(
        store=store,
        date=date,
        defaults={
            "total_buy_value": data["total_buy_value"],
            "total_sell_value": data["total_sell_value"],
            "transaction_count": data["transaction_count"],
        },
    )
    return data


def build_report_pdf(store, data, period_label):
    """Builds a PDF report using the trading-ledger palette (Section 9 of the spec).
    Returns raw PDF bytes, ready to attach to an email."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()

    ink = colors.HexColor("#1E2A24")
    buy_green = colors.HexColor("#2F6B4F")
    value_gold = colors.HexColor("#C98A2E")
    hairline = colors.HexColor("#DCD9CC")

    elements = []

    title_style = styles["Title"]
    title_style.textColor = ink
    elements.append(Paragraph(f"{store.name} — {period_label} report", title_style))
    elements.append(Paragraph(f"{data['start_date']} to {data['end_date']}", styles["Normal"]))
    elements.append(Spacer(1, 10 * mm))

    summary_table = Table(
        [
            ["Total buy-in", f"R{data['total_buy_value']:.2f}"],
            ["Total sales", f"R{data['total_sell_value']:.2f}"],
            ["Margin", f"R{data['margin']:.2f}"],
            ["Sales transactions", str(data["transaction_count"])],
            ["Buy-in invoices", str(data["invoice_count"])],
        ],
        colWidths=[80 * mm, 60 * mm],
    )
    summary_table.setStyle(TableStyle([
        ("TEXTCOLOR", (0, 0), (-1, -1), ink),
        ("GRID", (0, 0), (-1, -1), 0.5, hairline),
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#E9F1EC")),
        ("BACKGROUND", (0, 1), (0, 1), colors.HexColor("#FBF0DD")),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 10 * mm))

    if data["top_items"]:
        elements.append(Paragraph("Top items sold", styles["Heading2"]))
        top_rows = [["Item", "Quantity"]] + [[name, str(qty)] for name, qty in data["top_items"]]
        top_table = Table(top_rows, colWidths=[100 * mm, 40 * mm])
        top_table.setStyle(TableStyle([
            ("TEXTCOLOR", (0, 0), (-1, -1), ink),
            ("GRID", (0, 0), (-1, -1), 0.5, hairline),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F5F6F1")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]))
        elements.append(top_table)

    doc.build(elements)
    return buffer.getvalue()


def generate_and_save_report(store, start_date, end_date, period_type, period_label):
    """Full pipeline: aggregate -> build PDF -> save as DailyReportFile.
    Does NOT send anything — that's a separate manual step via Django admin.
    Called by the nightly/monthly management commands."""
    data = aggregate_period(store, start_date, end_date)
    pdf_bytes = build_report_pdf(store, data, period_label=period_label)

    filename = f"{store.name.replace(' ', '_')}_{period_type}_{start_date}.pdf"
    report = DailyReportFile.objects.create(
        store=store,
        period_type=period_type,
        period_start=start_date,
        period_end=end_date,
    )
    report.pdf_file.save(filename, ContentFile(pdf_bytes), save=True)
    return report
