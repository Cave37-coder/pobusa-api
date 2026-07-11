# PoBuSA urls.py — v1.8.0

from django.urls import path
from . import views
from . import sale_views
from . import inventory_views
from . import export_views
from . import branding_views
from . import report_list_views
from . import credit_note_views

urlpatterns = [
    # Buy-in flow (now includes sealed product — see serializers.py v1.3.0)
    path("card-search/", views.card_search, name="pobusa-card-search"),
    path("card-lookup/", views.card_lookup, name="pobusa-card-lookup"),
    path("invoices/", views.create_buy_in_invoice, name="pobusa-create-invoice"),
    path("invoices/<str:invoice_number>/", views.invoice_detail, name="pobusa-invoice-detail"),

    # Sell-out flow
    path("inventory/", sale_views.inventory_search, name="pobusa-inventory-search"),
    path("sales/", sale_views.create_sale, name="pobusa-create-sale"),
    path("sales/<str:sale_number>/", sale_views.sale_detail, name="pobusa-sale-detail"),

    # General inventory (cooldrinks, accessories, other)
    path("general-inventory/", inventory_views.general_inventory_list, name="pobusa-general-inventory"),
    path("general-inventory/<int:item_id>/restock/", inventory_views.restock_item, name="pobusa-restock-item"),
    path("card-stock/<int:line_id>/price/", inventory_views.update_card_price, name="pobusa-update-card-price"),

    # Accounting CSV export
    path("exports/sales/<int:store_id>/", export_views.export_sales_csv, name="pobusa-export-sales"),
    path("exports/purchases/<int:store_id>/", export_views.export_purchases_csv, name="pobusa-export-purchases"),

    # Client onboarding / branding
    path("stores/<int:store_id>/branding/", branding_views.store_branding, name="pobusa-store-branding"),

    # Reports — list + manual send
    path("reports/<int:store_id>/", report_list_views.list_reports, name="pobusa-list-reports"),
    path("reports/<int:report_id>/send/", report_list_views.send_report, name="pobusa-send-report"),

    # Refunds
    path("credit-notes/", credit_note_views.create_credit_note, name="pobusa-create-credit-note"),
    path("credit-notes/<str:credit_note_number>/", credit_note_views.credit_note_detail, name="pobusa-credit-note-detail"),
]
