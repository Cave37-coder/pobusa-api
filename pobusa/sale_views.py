# PoBuSA sale_views.py — v1.3.0
# v1.3.0: inventory_search now returns market_ref and sell_percent for
# cards too, not just price — needed for the Inventory screen's Cards tab
# to show and let staff edit the current sell % inline.
# v1.2.0: emails the receipt to the customer immediately after the sale
# completes, if buyer_email was provided. Same non-blocking pattern as
# invoice emails — failure never undoes the sale.

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Q

from .models import Sale, SaleItem, CardStockLine, SealedStockItem, GeneralInventoryItem
from .sale_serializers import SaleCreateSerializer
from .email_service import send_receipt_email


@api_view(["GET"])
def inventory_search(request):
    """GET /api/pobusa/inventory/?category=cards&q=charizard
    Powers the search interface for Cards / Cooldrinks / Accessories / Other.
    category is one of: cards, cooldrinks, accessories, other."""
    category = request.query_params.get("category", "cards")
    query = request.query_params.get("q", "")

    if category == "cards":
        results = CardStockLine.objects.filter(status="in_stock").filter(
            Q(name__icontains=query) | Q(card_id__icontains=query)
        )
        data = [
            {
                "id": r.id, "source_type": "card", "name": r.name or r.card_id,
                "condition": r.condition, "price": r.sell_price, "quantity": r.quantity,
                "market_ref": r.market_ref, "sell_percent": r.sell_percent, "buy_price": r.buy_price,
            }
            for r in results
        ]
    elif category == "sealed":
        results = SealedStockItem.objects.filter(status="in_stock", product_name__icontains=query)
        data = [
            {
                "id": r.id, "source_type": "sealed", "name": r.product_name,
                "price": r.rrp, "quantity": r.quantity,
            }
            for r in results
        ]
    else:
        results = GeneralInventoryItem.objects.filter(category=category, name__icontains=query)
        data = [
            {
                "id": r.id, "source_type": "general", "name": r.name,
                "price": r.sell_price, "quantity": r.quantity,
            }
            for r in results
        ]

    # Customer-facing shape: name, condition (if applicable), price, quantity only —
    # no market_ref, no %, no buy price. Matches Section 7 permission tiers.
    return Response(data)


@api_view(["POST"])
def create_sale(request):
    """POST /api/pobusa/sales/
    Completes a sale: validates stock, calculates VAT, decrements inventory
    across whichever source tables the items came from. Emails a receipt
    to buyer_email if provided."""
    serializer = SaleCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    sale = serializer.save()

    items = SaleItem.objects.filter(sale=sale)

    email_sent = False
    email_error = None
    if sale.buyer_email:
        try:
            send_receipt_email(sale, items)
            email_sent = True
        except Exception as e:
            email_error = str(e)  # sale already completed — a failed email never undoes it

    return Response(
        {
            "sale_number": sale.sale_number,
            "total": sale.total,
            "payment_method": sale.payment_method,
            "items": [
                {
                    "description": i.description,
                    "quantity": i.quantity,
                    "unit_price": i.unit_price,
                    "vat_amount": i.vat_amount,
                }
                for i in items
            ],
            "email_sent": email_sent,
            "email_error": email_error,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
def sale_detail(request, sale_number):
    """GET /api/pobusa/sales/<sale_number>/ — for receipt reprint/review."""
    try:
        sale = Sale.objects.get(sale_number=sale_number)
    except Sale.DoesNotExist:
        return Response({"error": "Sale not found"}, status=status.HTTP_404_NOT_FOUND)

    items = SaleItem.objects.filter(sale=sale)
    return Response(
        {
            "sale_number": sale.sale_number,
            "date": sale.date,
            "total": sale.total,
            "payment_method": sale.payment_method,
            "voided": sale.voided,
            "items": [
                {
                    "description": i.description,
                    "quantity": i.quantity,
                    "unit_price": i.unit_price,
                    "vat_amount": i.vat_amount,
                }
                for i in items
            ],
        }
    )
