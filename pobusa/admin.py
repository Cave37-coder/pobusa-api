# PoBuSA admin.py — v1.4.0
# v1.4.0: registered ClientCatalogStock (PoBuSA Checklist Phase 4, item
# 10) -- purely additive, nothing reads from it yet, see the model's own
# docstring for why it's SKU-string-keyed rather than a real FK.
# v1.3.0: registered StoreCustomProduct (PoBuSA Checklist Phase 1, item 4).
# sku is readonly same as CatalogProduct's -- it's auto-generated in
# save(), never staff-typed.
# v1.2.0: registered Game, CatalogProduct, TCGCSVSource (PoBuSA Checklist
# Phase 1, item 1). TCGCSVSource stays admin-only, same rule as models.py —
# no serializer anywhere touches it; this registration is purely for Mike's
# superuser access to eyeball/debug syncs, never exposed to Client staff.
# v1.1.0: registered every model for admin visibility/testing, not just
# DailyReportFile. Useful for eyeballing data and creating test records
# directly without going through the API — the "Send selected reports"
# action from v1.0.0 is unchanged.

from django.contrib import admin
from .models import (
    Store, BuyPercentTier, Invoice, CardStockLine, SealedStockItem,
    GeneralInventoryItem, Sale, SaleItem, CreditNote, DailySalesSummary,
    AccountingExportSettings, StoreStaff, Game, CatalogProduct, TCGCSVSource,
    StoreCustomProduct, ClientCatalogStock,
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


@admin.register(ClientCatalogStock)
class ClientCatalogStockAdmin(admin.ModelAdmin):
    # PoBuSA Checklist Phase 4, item 10 -- purely additive for now, nothing
    # reads from this table yet (see model docstring). Registered here so
    # Mike can eyeball/hand-set entries once item 12 wires the Sell screen
    # up to it. quantity is list_editable for the same quick-bulk-edit
    # reason CatalogProduct.stock_quantity's admin entry has it.
    list_display = ["store", "sku", "quantity", "last_updated"]
    list_filter = ["store"]
    list_editable = ["quantity"]
    search_fields = ["sku"]


@admin.register(StoreCustomProduct)
class StoreCustomProductAdmin(admin.ModelAdmin):
    list_display = ["sku", "name", "store", "product_type", "sub_category", "is_active", "created_date"]
    list_filter = ["store", "product_type", "is_active"]
    search_fields = ["sku", "name", "sub_category"]
    readonly_fields = ["sku"]  # auto-generated in save(), never staff-typed


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


# -----------------------------------------------------------------------
# Catalog — Game, CatalogProduct, TCGCSVSource (see models.py module note
# above CatalogProduct for how these three relate).
# -----------------------------------------------------------------------

@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "tcgcsv_category_id", "is_active", "sort_order"]
    list_editable = ["is_active", "sort_order"]  # lets Mike stage/reorder games inline, no per-row edit needed
    list_filter = ["is_active"]
    search_fields = ["name", "code"]
    ordering = ["sort_order", "name"]


class TCGCSVSourceInline(admin.StackedInline):
    """Read-alongside view of the sync linkage on a product's own admin
    page. extra=0 — this table is populated by sync_tcgcsv, not hand-typed,
    but left editable in case Mike needs to manually fix a mis-synced row."""
    model = TCGCSVSource
    extra = 0
    can_delete = True


@admin.register(CatalogProduct)
class CatalogProductAdmin(admin.ModelAdmin):
    # stock_quantity is list_editable -- manual bridge until the real
    # buy-in flow (Checklist Phase 3, item 12) exists for every synced
    # game; see the field's own docstring in models.py. Lets Mike set
    # counts inline, no per-row edit page needed for the common case.
    list_display = ["sku", "name", "product_type", "game", "set_name", "stock_quantity", "market_price", "barcode", "thumbnail_url", "is_active", "last_synced"]
    list_filter = ["product_type", "game", "is_active"]
    list_editable = ["stock_quantity"]
    search_fields = ["sku", "name", "set_name", "barcode"]  # barcode-scan lookup for Sealed/Accessories
    list_select_related = ["game"]  # avoids N+1 on the game column across ~236k rows
    readonly_fields = ["sku", "last_synced"]  # sku is editable=False on the model; last_synced is sync-only
    date_hierarchy = "last_synced"
    list_per_page = 50
    inlines = [TCGCSVSourceInline]


@admin.register(TCGCSVSource)
class TCGCSVSourceAdmin(admin.ModelAdmin):
    """Standalone view for debugging syncs directly by TCGCSV ID, separate
    from browsing via a specific product's inline above."""
    list_display = ["product", "tcgcsv_product_id", "tcgcsv_subtype_name", "tcgcsv_group_name", "tcgcsv_category_id"]
    list_filter = ["tcgcsv_subtype_name"]
    search_fields = ["product__sku", "product__name", "tcgcsv_product_id", "tcgcsv_group_name"]
    list_select_related = ["product"]


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
