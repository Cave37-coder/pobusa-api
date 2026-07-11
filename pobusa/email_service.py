# PoBuSA email_service.py — v1.2.0
# v1.2.0: added send_invoice_email() and send_receipt_email() — transactional
# emails sent immediately when a buy-in invoice or sale is saved with an
# email address attached, so no printing is needed at the counter. This is
# a direct, synchronous send triggered by the user's own save action (same
# risk profile as the working manual "Send" button for reports), not a
# background/scheduled job — those are the ones that failed on Railway.

from django.core.mail import EmailMessage
from django.utils import timezone


def send_report_file(report, to_email: str):
    """Sends an already-generated DailyReportFile. Called from the Django
    admin 'Send selected reports' action — a manual click, not automated."""
    subject = f"{report.store.name} — {report.get_period_type_display()} report ({report.period_start})"
    body = (
        f"{report.get_period_type_display()} report attached for {report.store.name}, "
        f"{report.period_start} to {report.period_end}."
    )

    email = EmailMessage(subject=subject, body=body, to=[to_email])
    report.pdf_file.open("rb")
    email.attach(report.pdf_file.name.split("/")[-1], report.pdf_file.read(), "application/pdf")
    report.pdf_file.close()

    email.send(fail_silently=False)

    report.sent = True
    report.sent_at = timezone.now()
    report.sent_to = to_email
    report.save(update_fields=["sent", "sent_at", "sent_to"])


def send_invoice_email(invoice, card_lines, sealed_lines):
    """Emails a buy-in invoice directly to the seller — no printing needed.
    Called right after invoice creation if seller_email was provided.
    Failures are caught by the caller, never block the invoice from saving."""
    subject = f"{invoice.store.name} — buy-in invoice {invoice.invoice_number}"

    lines = [f"Buy-in invoice {invoice.invoice_number} — {invoice.store.name}", f"Date: {invoice.date}", ""]
    for line in card_lines:
        lines.append(f"  {line.name} ({line.condition}) x{line.quantity} — R{line.buy_price}")
    for line in sealed_lines:
        lines.append(f"  {line.product_name} x{line.quantity} — R{line.cost_price}")
    lines.append("")
    lines.append(f"Total paid: R{invoice.total_paid}")

    email = EmailMessage(subject=subject, body="\n".join(lines), to=[invoice.seller_email])
    email.send(fail_silently=False)


def send_receipt_email(sale, sale_items):
    """Emails a sale receipt directly to the customer — no printing needed.
    Called right after sale creation if buyer_email was provided. Failures
    are caught by the caller, never block the sale from completing."""
    subject = f"{sale.store.name} — receipt {sale.sale_number}"

    lines = [f"Receipt {sale.sale_number} — {sale.store.name}", f"Date: {sale.date}", ""]
    for item in sale_items:
        lines.append(f"  {item.description} x{item.quantity} — R{item.unit_price}")
    lines.append("")
    lines.append(f"Total: R{sale.total}")
    lines.append(f"Payment method: {sale.get_payment_method_display()}")

    email = EmailMessage(subject=subject, body="\n".join(lines), to=[sale.buyer_email])
    email.send(fail_silently=False)
