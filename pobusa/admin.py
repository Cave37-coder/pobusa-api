# PoBuSA admin.py — v1.1.0
# v1.1.0: registered every model for admin visibility/testing, not just
# DailyReportFile. Useful for eyeballing data and creating test records
# directly without going through the API — the "Send selected reports"
# action from v1.0.0 is unchanged.

from django.contrib import admin
from .models import (
    Store, BuyPercentTier, Invoice, CardStockLine, SealedStockItem,
    GeneralInventoryItem, Sale, SaleItem, CreditNote, DailySalesSummary,
    AccountingExportSettings, StoreStaff,
)
from .report_models import DailyReportFile
from .email_service import send_report_file


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ["name", "vat_registered", "sell_percent_default", "created_date"]
    search_fields = ["name"]


@admin.register(BuyPercentTier)
class BuyPercentTierAdmin(admin.ModelAdmin):
    list_display = ["store", "min_value", "max_value", "buy_percent"]
    list_filter = ["store"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["invoice_number", "store", "date", "seller_name", "total_paid", "voided"]
    list_filter = ["store", "voided", "date"]
    search_fields = ["invoice_number", "seller_name"]


class CardStockLineInline(admin.TabularInline):
    model = CardStockLine
    extra = 0


@admin.register(CardStockLine)
class CardStockLineAdmin(admin.ModelAdmin):
    list_display = ["card_id", "invoice", "condition", "market_ref", "buy_price", "sell_price", "quantity", "status"]
    list_filter = ["status", "condition"]
    search_fields = ["card_id"]


@admin.register(SealedStockItem)
class SealedStockItemAdmin(admin.ModelAdmin):
    list_display = ["product_name", "invoice", "cost_price", "rrp", "quantity", "status"]
    list_filter = ["status"]
    search_fields = ["product_name"]


@admin.register(GeneralInventoryItem)
class GeneralInventoryItemAdmin(admin.ModelAdmin):
    list_display = ["name", "store", "category", "cost_price", "sell_price", "quantity"]
    list_filter = ["store", "category"]
    search_fields = ["name", "barcode"]


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ["sale_number", "store", "date", "total", "payment_method", "voided"]
    list_filter = ["store", "payment_method", "voided", "date"]
    search_fields = ["sale_number", "buyer_name"]


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ["sale", "description", "source_type", "quantity", "unit_price", "vat_amount"]
    list_filter = ["source_type"]


@admin.register(CreditNote)
class CreditNoteAdmin(admin.ModelAdmin):
    list_display = ["credit_note_number", "store", "original_sale", "date", "amount", "vat_amount"]
    list_filter = ["store", "date"]
    search_fields = ["credit_note_number"]


@admin.register(DailySalesSummary)
class DailySalesSummaryAdmin(admin.ModelAdmin):
    list_display = ["store", "date", "total_buy_value", "total_sell_value", "transaction_count"]
    list_filter = ["store", "date"]


@admin.register(AccountingExportSettings)
class AccountingExportSettingsAdmin(admin.ModelAdmin):
    list_display = ["store", "export_format", "sales_account_code", "cogs_account_code", "vat_account_code", "vat_rate"]


@admin.register(StoreStaff)
class StoreStaffAdmin(admin.ModelAdmin):
    list_display = ["user", "store", "role"]
    list_filter = ["store", "role"]


@admin.register(DailyReportFile)
class DailyReportFileAdmin(admin.ModelAdmin):
    list_display = ["store", "period_type", "period_start", "period_end", "sent", "sent_at", "generated_at"]
    list_filter = ["store", "period_type", "sent"]
    actions = ["send_selected_reports"]

    @admin.action(description="Send selected reports via email")
    def send_selected_reports(self, request, queryset):
        # Adjust recipient logic here — hardcoded for the GG's Trading Card Store
        # test run; swap for a per-Store report_email field once there's more
        # than one Store to manage.
        recipient = "michaelcaveviljoen@gmail.com"

        sent_count = 0
        for report in queryset:
            if report.sent:
                continue  # skip already-sent reports to avoid double-sending
            send_report_file(report, to_email=recipient)
            sent_count += 1

        self.message_user(request, f"Sent {sent_count} report(s) to {recipient}.")
