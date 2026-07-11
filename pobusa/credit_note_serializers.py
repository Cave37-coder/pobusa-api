# PoBuSA credit_note_serializers.py — v1.0.1
# v1.0.1: fixed a bug — PrimaryKeyRelatedField(queryset=None) fails
# immediately at class-definition time (DRF asserts queryset is set before
# __init__ ever runs), so the deferred-import workaround in v1.0.0 never
# actually worked. There was no real circular import risk here in the first
# place — Store is already safely imported at module level by every other
# serializer in this app (serializers.py, sale_serializers.py, etc.) — so
# this just does the same normal thing they do.
#
# Was a genuine gap — CreditNote existed as a model (Section 5a of the spec:
# SARS requires refunds to reference the original invoice and state a real
# reason) but nothing ever created one. Refunds had no working path at all.
#
# Design: a credit note is issued against a whole Sale, not individual line
# items — simplest model for a first version. Optionally restocks every
# item on that sale (full-sale refund assumption). Partial refunds/returns
# would need a more granular model — flagged as a future improvement, not
# built here.

from decimal import Decimal
from django.db import transaction
from rest_framework import serializers
from .models import CreditNote, Sale, SaleItem, CardStockLine, SealedStockItem, GeneralInventoryItem, Store


class CreditNoteCreateSerializer(serializers.Serializer):
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    credit_note_number = serializers.CharField(max_length=50)
    original_sale_number = serializers.CharField(max_length=50)
    date = serializers.DateField()
    reason = serializers.CharField()  # SARS requires a real explanation, not just "adjustment"
    restock = serializers.BooleanField(default=True)

    def validate_credit_note_number(self, value):
        if CreditNote.objects.filter(credit_note_number=value).exists():
            raise serializers.ValidationError("Credit note number already used — must be unique.")
        return value

    def validate_reason(self, value):
        if len(value.strip()) < 10:
            raise serializers.ValidationError(
                "Reason must be a real explanation (SARS will reject a credit note that just says 'adjustment')."
            )
        return value

    def validate(self, data):
        try:
            sale = Sale.objects.get(sale_number=data["original_sale_number"], store=data["store"])
        except Sale.DoesNotExist:
            raise serializers.ValidationError(
                f"Sale {data['original_sale_number']} not found for this store."
            )
        if CreditNote.objects.filter(original_sale=sale).exists():
            raise serializers.ValidationError(
                f"Sale {sale.sale_number} already has a credit note issued against it."
            )
        data["_sale"] = sale
        return data

    @transaction.atomic
    def create(self, validated_data):
        sale = validated_data["_sale"]
        sale_items = SaleItem.objects.filter(sale=sale)

        amount = sum((item.unit_price * item.quantity for item in sale_items), Decimal("0.00"))
        vat_amount = sum((item.vat_amount * item.quantity for item in sale_items), Decimal("0.00"))

        credit_note = CreditNote.objects.create(
            store=validated_data["store"],
            credit_note_number=validated_data["credit_note_number"],
            original_sale=sale,
            date=validated_data["date"],
            reason=validated_data["reason"],
            amount=amount,
            vat_amount=vat_amount,
        )

        if validated_data.get("restock", True):
            self._restock_items(sale_items)

        return credit_note

    def _restock_items(self, sale_items):
        """Puts stock back for every item on the refunded sale. Full-sale
        assumption — see module docstring for the partial-refund gap."""
        model_map = {
            "card": CardStockLine,
            "sealed": SealedStockItem,
            "general": GeneralInventoryItem,
        }
        for item in sale_items:
            model = model_map.get(item.source_type)
            if not model:
                continue
            try:
                record = model.objects.get(pk=item.source_id)
            except model.DoesNotExist:
                continue  # stock record no longer exists — can't restock, but the credit note itself still stands

            record.quantity += item.quantity
            if hasattr(record, "status") and record.quantity > 0:
                record.status = "in_stock"
            record.save()
