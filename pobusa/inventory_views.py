# PoBuSA inventory_views.py — v1.1.0
# v1.1.0: added update_card_price — lets staff adjust a card's sell
# price/percent from the Inventory screen directly.

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from decimal import Decimal
from .models import GeneralInventoryItem, CardStockLine
from .inventory_serializers import GeneralInventoryItemSerializer, RestockSerializer, CardPriceUpdateSerializer


@api_view(["GET", "POST"])
def general_inventory_list(request):
    """GET /api/pobusa/general-inventory/  — list items, optional ?category= filter
    POST /api/pobusa/general-inventory/ — add a brand new item (new SKU/barcode)"""
    if request.method == "GET":
        items = GeneralInventoryItem.objects.all()
        category = request.query_params.get("category")
        if category:
            items = items.filter(category=category)
        return Response(GeneralInventoryItemSerializer(items, many=True).data)

    serializer = GeneralInventoryItemSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    item = serializer.save()
    return Response(GeneralInventoryItemSerializer(item).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def restock_item(request, item_id):
    """POST /api/pobusa/general-inventory/<item_id>/restock/
    Tops up quantity on an existing item — e.g. a new delivery of cooldrinks —
    without creating a duplicate row. Optionally updates cost/sell price if
    the new delivery came in at a different price."""
    try:
        item = GeneralInventoryItem.objects.get(pk=item_id)
    except GeneralInventoryItem.DoesNotExist:
        return Response({"error": "Item not found"}, status=status.HTTP_404_NOT_FOUND)

    serializer = RestockSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    item.quantity += data["quantity_added"]
    if "new_cost_price" in data:
        item.cost_price = data["new_cost_price"]
    if "new_sell_price" in data:
        item.sell_price = data["new_sell_price"]
    item.save()

    return Response(GeneralInventoryItemSerializer(item).data)


@api_view(["PATCH"])
def update_card_price(request, line_id):
    """PATCH /api/pobusa/card-stock/<line_id>/price/
    Adjusts a card's sell price/percent directly from the Inventory
    screen. Buy-side fields (buy_price, buy_percent) are never touched
    here — this is sell-side pricing only."""
    try:
        line = CardStockLine.objects.get(pk=line_id)
    except CardStockLine.DoesNotExist:
        return Response({"error": "Card stock line not found"}, status=status.HTTP_404_NOT_FOUND)

    serializer = CardPriceUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    if "sell_percent" in data:
        line.sell_percent = data["sell_percent"]
        line.sell_price = (line.market_ref * line.sell_percent / Decimal("100")).quantize(Decimal("0.01"))
    elif "sell_price" in data:
        line.sell_price = data["sell_price"]
        if line.market_ref:
            line.sell_percent = (line.sell_price / line.market_ref * Decimal("100")).quantize(Decimal("0.01"))

    line.save(update_fields=["sell_percent", "sell_price"])

    return Response({
        "id": line.id, "name": line.name, "market_ref": line.market_ref,
        "sell_percent": line.sell_percent, "sell_price": line.sell_price,
    })
