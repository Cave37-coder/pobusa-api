# PoBuSA export_service.py — v1.0.0
# Builds CSV rows for the accounting export (Section 10a of the spec).
# Row structure: Date, Reference, Description, Account Code, Debit, Credit,
# Tax Code, Tax Amount — close enough to Pastel/Sage/Xero generic imports
# that changing export_format mostly just changes headers/date formatting.

import csv
import io

from .models import Sale, SaleItem, Invoice, CardStockLine, SealedStockItem, AccountingExportSettings

CSV_HEADERS = ["Date", "Reference", "Description", "Account Code", "Debit", "Credit", "Tax Code", "Tax Amount"]


def _get_settings(store):
    try:
        return store.accounting_settings
    except AccountingExportSettings.DoesNotExist:
        raise ValueError(f"No AccountingExportSettings configured for {store.name} — set up GL codes first.")


def build_sales_export(store, start_date, end_date) -> str:
    """One Sale -> two rows: revenue line (credit) + VAT line (credit),
    matching double-entry convention. Returns CSV as a string."""
    settings = _get_settings(store)
    sales = Sale.objects.filter(store=store, date__date__gte=start_date, date__date__lte=end_date, voided=False)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADERS)

    for sale in sales:
        date_str = sale.date.strftime(settings.date_format)
        vat_total = sum((item.vat_amount * item.quantity for item in SaleItem.objects.filter(sale=sale)), 0)
        revenue = sale.total - vat_total

        writer.writerow([date_str, sale.sale_number, f"Sale {sale.sale_number}",
                          settings.sales_account_code, "", f"{revenue:.2f}", "", ""])
        if vat_total:
            writer.writerow([date_str, sale.sale_number, f"VAT on sale {sale.sale_number}",
                              settings.vat_account_code, "", f"{vat_total:.2f}", "VAT", f"{vat_total:.2f}"])

    return buffer.getvalue()


def build_purchases_export(store, start_date, end_date) -> str:
    """One Invoice (buy-in) -> two rows: stock/COGS line (debit) + VAT line
    (debit, if applicable). Returns CSV as a string."""
    settings = _get_settings(store)
    invoices = Invoice.objects.filter(store=store, date__gte=start_date, date__lte=end_date, voided=False)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADERS)

    for invoice in invoices:
        date_str = invoice.date.strftime(settings.date_format)
        # Buy-ins from individuals typically aren't VAT invoices themselves —
        # no input VAT claimed here unless your actual purchase process differs.
        writer.writerow([date_str, invoice.invoice_number, f"Buy-in {invoice.invoice_number}",
                          settings.cogs_account_code, f"{invoice.total_paid:.2f}", "", "", ""])

    return buffer.getvalue()
