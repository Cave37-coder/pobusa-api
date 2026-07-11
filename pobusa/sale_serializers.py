# PoBuSA sale_serializers.py — v1.4.0
# v1.4.0: matches PokeBulk's manual invoice POS field set — discount %/
# amount, shipping, buyer_phone, delivery_note, payment_received checkbox,
# and a 'custom' source_type for off-site/not-in-catalog items (arbitrary
# description + price + qty, no stock record, no decrement).

from decimal import Decimal
from rest_framework import serializers
from django.db import transaction
from .models import Sale, SaleItem, CardStockLine, SealedStockItem, GeneralInventoryItem, Store

FULL_TAX_INVOICE_THRESHOLD = Decimal("5000.00")


class SaleItemWriteSerializer(serializers.Serializer):
    """A line being sold. source_type/source_id/quantity identify the stock
    record; unit_price_override is optional — if omitted, the stock
    record's stored sell_price/rrp is used as-is.

    source_type='custom' is the off-site/not-in-catalog case: no stock
    record exists, source_id is omitted, and description/unit_price are
    exactly what staff typed in — trusted directly since there's nothing
    to validate against, matching PokeBulk's off-site item pattern."""
    source_type = serializers.ChoiceField(choices=SaleItem.SOURCE_CHOICES)
    source_id = serializers.IntegerField(required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1, default=1)
    unit_price_override = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True, min_value=Decimal("0")
    )
    custom_description = serializers.CharField(max_length=255, required=False, allow_blank=True)


class SaleCreateSerializer(serializers.Serializer):
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    sale_number = serializers.CharField(max_length=50, required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(choices=Sale.PAYMENT_CHOICES)
    items = SaleItemWriteSerializer(many=True)

    buyer_phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    delivery_note = serializers.CharField(required=False, allow_blank=True)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    discount_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=Decimal("0"))
    shipping = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=Decimal("0"))
    payment_received = serializers.BooleanField(required=False, default=True)

    # Only required once the running total crosses R5,000 (Section 5a threshold rule)
    buyer_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    buyer_email = serializers.EmailField(required=False, allow_blank=True)
    buyer_address = serializers.CharField(required=False, allow_blank=True)
    buyer_vat_number = serializers.CharField(max_length=50, required=False, allow_blank=True)

    def validate_sale_number(self, value):
        if value and Sale.objects.filter(sale_number=value).exists():
            raise serializers.ValidationError("Sale number already used — numbers must be sequential and unique.")
        return value

    def _generate_sale_number(self, store):
        """Auto-assigns the next sequential sale number for this store,
        e.g. SALE-0001. Same pattern as invoice auto-numbering — staff
        never think about it."""
        last = Sale.objects.filter(store=store, sale_number__startswith="SALE-").order_by("-id").first()
        if last:
            try:
                next_num = int(last.sale_number.split("-")[-1]) + 1
            except ValueError:
                next_num = Sale.objects.filter(store=store).count() + 1
        else:
            next_num = 1
        return f"SALE-{next_num:04d}"

    def _get_stock_record(self, source_type, source_id):
        if source_type == "custom":
            return None  # off-site item — no stock record at all
        model_map = {
            "card": CardStockLine,
            "sealed": SealedStockItem,
            "general": GeneralInventoryItem,
        }
        model = model_map[source_type]
        try:
            return model.objects.select_for_update().get(pk=source_id)
        except model.DoesNotExist:
            raise serializers.ValidationError(f"{source_type} item {source_id} not found")

    def _get_price_and_description(self, source_type, record, custom_description=None, custom_price=None):
        if source_type == "custom":
            return custom_price, (custom_description or "Off-site item")
        if source_type == "card":
            display_name = record.name or record.card_id
            return record.sell_price, f"{display_name} ({record.condition})"
        elif source_type == "sealed":
            return record.rrp, record.product_name
        else:  # general
            return record.sell_price, record.name

    @transaction.atomic
    def create(self, validated_data):
        store = validated_data["store"]
        items_data = validated_data.pop("items")
        sale_number = validated_data.get("sale_number") or self._generate_sale_number(store)

        # First pass: resolve prices and validate stock before committing anything
        resolved = []
        running_total = Decimal("0.00")

        for item in items_data:
            source_type = item["source_type"]

            if source_type == "custom":
                record = None
                if item.get("unit_price_override") is None:
                    raise serializers.ValidationError("Off-site items require unit_price_override.")
                default_price, description = self._get_price_and_description(
                    source_type, None,
                    custom_description=item.get("custom_description"),
                    custom_price=item["unit_price_override"],
                )
            else:
                record = self._get_stock_record(source_type, item["source_id"])
                if record.quantity < item["quantity"]:
                    raise serializers.ValidationError(
                        f"Insufficient stock for {source_type} {item['source_id']} "
                        f"(have {record.quantity}, need {item['quantity']})"
                    )
                default_price, description = self._get_price_and_description(source_type, record)

            unit_price = item.get("unit_price_override")
            if unit_price is None:
                unit_price = default_price

            vat_amount = Decimal("0.00")
            if store.vat_registered:
                # Prices are VAT-inclusive at the till — back out the VAT portion (15%)
                vat_amount = (unit_price * Decimal("15") / Decimal("115")).quantize(Decimal("0.01"))

            line_total = unit_price * item["quantity"]
            running_total += line_total

            resolved.append({
                "record": record,
                "source_type": source_type,
                "source_id": item.get("source_id"),
                "description": description,
                "quantity": item["quantity"],
                "unit_price": unit_price,
                "vat_amount": vat_amount,
            })

        # Apply discount and shipping to get the real final total
        subtotal = running_total
        discount_amount = validated_data.get("discount_amount") or Decimal("0")
        discount_percent = validated_data.get("discount_percent") or Decimal("0")
        if not discount_amount and discount_percent:
            discount_amount = (subtotal * discount_percent / Decimal("100")).quantize(Decimal("0.01"))
        shipping = validated_data.get("shipping") or Decimal("0")
        running_total = subtotal - discount_amount + shipping

        # Full tax invoice threshold check (Section 5a) — checked against the real final total
        if running_total > FULL_TAX_INVOICE_THRESHOLD and not validated_data.get("buyer_name"):
            raise serializers.ValidationError(
                f"Sale total R{running_total} exceeds R{FULL_TAX_INVOICE_THRESHOLD} — "
                "buyer name, address, and VAT number (if applicable) are required for a full tax invoice."
            )

        sale = Sale.objects.create(
            store=store,
            sale_number=sale_number,
            subtotal=subtotal,
            discount_percent=discount_percent,
            discount_amount=discount_amount,
            shipping=shipping,
            total=running_total,
            payment_method=validated_data["payment_method"],
            payment_received=validated_data.get("payment_received", True),
            buyer_name=validated_data.get("buyer_name", ""),
            buyer_email=validated_data.get("buyer_email", ""),
            buyer_phone=validated_data.get("buyer_phone", ""),
            buyer_address=validated_data.get("buyer_address", ""),
            buyer_vat_number=validated_data.get("buyer_vat_number", ""),
            delivery_note=validated_data.get("delivery_note", ""),
        )

        # Second pass: create SaleItems and decrement stock (custom items have no record to decrement)
        for line in resolved:
            SaleItem.objects.create(
                sale=sale,
                source_type=line["source_type"],
                source_id=line["source_id"],
                description=line["description"],
                quantity=line["quantity"],
                unit_price=line["unit_price"],
                vat_amount=line["vat_amount"],
            )

            record = line["record"]
            if record is None:
                continue  # custom/off-site item — nothing to decrement
            record.quantity -= line["quantity"]
            if hasattr(record, "status") and record.quantity <= 0:
                record.status = "sold"
                if hasattr(record, "date_sold"):
                    from django.utils import timezone
                    record.date_sold = timezone.now().date()
            record.save()

        return sale
