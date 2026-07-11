# PoBuSA inventory_serializers.py — v1.1.0
# v1.1.0: added CardPriceUpdateSerializer — lets staff adjust a card's
# sell price/percent directly from the Inventory screen, before a
# customer ever reaches checkout, not just live during a sale.

from rest_framework import serializers
from .models import GeneralInventoryItem


class GeneralInventoryItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeneralInventoryItem
        fields = ["id", "store", "category", "name", "barcode", "cost_price", "sell_price", "quantity", "date_added"]
        read_only_fields = ["id", "date_added"]


class RestockSerializer(serializers.Serializer):
    """For topping up quantity on an existing item — e.g. a new cooldrink
    delivery — without creating a duplicate row."""
    quantity_added = serializers.IntegerField(min_value=1)
    new_cost_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    new_sell_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)


class CardPriceUpdateSerializer(serializers.Serializer):
    """Updates a CardStockLine's sell price/percent directly from the
    Inventory screen. Pass sell_percent to recalculate sell_price from
    market_ref, or sell_price directly to set it (and back-derive the
    effective percent) — either works, whichever staff finds easier."""
    sell_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    sell_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
