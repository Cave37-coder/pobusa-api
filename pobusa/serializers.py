# PoBuSA serializers.py — v1.7.0
# v1.7.0: saves variant on CardStockLine at buy-in time, and exposes it
# on the read serializer — see models.py v1.6.0 note.
# v1.6.0: matches PokeBulk's manual invoice POS field set — discount %/
# amount, seller_phone, notes, payment_method (EFT/Cash/Card pill-select),
# payment_made checkbox, and off-site items (sealed_lines already covers
# this — an arbitrary name/price/qty line with no catalog lookup, exactly
# what "off-site stock, not in catalog" means).

from decimal import Decimal
from rest_framework import serializers
from .models import Invoice, CardStockLine, SealedStockItem, Store
from . import services


class CardStockLineWriteSerializer(serializers.Serializer):
    """One line on the buy-in ticket, as entered at the counter.
    buy_percent is optional — if omitted, the store's tier default is used.
    Everything else (market_ref, buy_price, sell_price) is calculated server-side."""
    card_id = serializers.CharField(max_length=50)
    condition = serializers.ChoiceField(choices=CardStockLine.CONDITION_CHOICES, default="NM")
    quantity = serializers.IntegerField(min_value=1, default=1)
    buy_percent_override = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )


class CardStockLineReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = CardStockLine
        fields = [
            "id", "card_id", "name", "card_set", "variant", "condition", "market_ref",
            "buy_percent", "buy_price", "sell_percent", "sell_price",
            "quantity", "status", "date_sold",
        ]
        # market_ref, buy_percent, sell_percent are shown here because this is a
        # Client-facing/internal staff view, not a Customer-facing receipt.
        # The Customer receipt serializer (separate) exposes only name/condition/price.


class SealedLineWriteSerializer(serializers.Serializer):
    """One sealed product line on the buy-in ticket. Unlike cards, price is
    entered directly by staff — cost_price is what was paid, rrp is what it
    will sell for. market_ref is optional reference info only (e.g. staff
    manually noting the TCG value for their own reference), never used to
    calculate price automatically."""
    product_name = serializers.CharField(max_length=255)
    cost_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    rrp = serializers.DecimalField(max_digits=10, decimal_places=2)
    market_ref = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1, default=1)


class SealedLineReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = SealedStockItem
        fields = ["id", "product_name", "cost_price", "rrp", "market_ref", "quantity", "status"]


class InvoiceCreateSerializer(serializers.Serializer):
    """Buy-in ticket creation — takes raw card lines, resolves pricing server-side,
    and writes the Invoice + CardStockLine rows in one transaction."""
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    invoice_number = serializers.CharField(max_length=50, required=False, allow_blank=True)
    date = serializers.DateField()
    seller_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    seller_email = serializers.EmailField(required=False, allow_blank=True)
    seller_phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    discount_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=Decimal("0"))
    payment_method = serializers.ChoiceField(choices=Invoice.PAYMENT_METHOD_CHOICES, required=False, allow_blank=True)
    payment_made = serializers.BooleanField(required=False, default=False)
    card_lines = CardStockLineWriteSerializer(many=True, required=False, default=list)
    sealed_lines = SealedLineWriteSerializer(many=True, required=False, default=list)

    def validate(self, data):
        if not data.get("card_lines") and not data.get("sealed_lines"):
            raise serializers.ValidationError("Invoice must include at least one card or sealed product line.")
        return data

    def validate_invoice_number(self, value):
        if value and Invoice.objects.filter(invoice_number=value).exists():
            raise serializers.ValidationError("Invoice number already used — numbers must be sequential and unique.")
        return value

    def _generate_invoice_number(self, store):
        """Auto-assigns the next sequential invoice number for this store,
        e.g. INV-0001, INV-0002. No gaps or reuse — matches the SARS
        sequential numbering rule from the spec. Staff never need to think
        about this; it just happens."""
        last = Invoice.objects.filter(store=store, invoice_number__startswith="INV-").order_by("-id").first()
        if last:
            try:
                next_num = int(last.invoice_number.split("-")[-1]) + 1
            except ValueError:
                next_num = Invoice.objects.filter(store=store).count() + 1
        else:
            next_num = 1
        return f"INV-{next_num:04d}"

    def create(self, validated_data):
        store = validated_data["store"]
        card_lines_data = validated_data.pop("card_lines", [])
        sealed_lines_data = validated_data.pop("sealed_lines", [])

        invoice_number = validated_data.get("invoice_number") or self._generate_invoice_number(store)

        invoice = Invoice.objects.create(
            store=store,
            invoice_number=invoice_number,
            date=validated_data["date"],
            seller_name=validated_data.get("seller_name", ""),
            seller_email=validated_data.get("seller_email", ""),
            seller_phone=validated_data.get("seller_phone", ""),
            notes=validated_data.get("notes", ""),
            discount_percent=validated_data.get("discount_percent", Decimal("0")),
            payment_method=validated_data.get("payment_method", ""),
            payment_made=validated_data.get("payment_made", False),
        )

        total_paid = Decimal("0.00")
        created_card_lines = []
        created_sealed_lines = []

        for line in card_lines_data:
            card_id = line["card_id"]
            card_data = services.fetch_card_data(card_id)
            market_ref = card_data["market_ref"]

            buy_percent = line.get("buy_percent_override") or services.get_buy_percent(store.id, market_ref)
            buy_price = services.calculate_buy_price(market_ref, buy_percent)

            sell_percent = store.sell_percent_default
            sell_price = services.calculate_sell_price(market_ref, sell_percent)

            stock_line = CardStockLine.objects.create(
                invoice=invoice,
                card_id=card_id,
                name=card_data.get("name", ""),
                card_set=card_data.get("set", ""),
                variant=card_data.get("variant") or "",
                condition=line.get("condition", "NM"),
                market_ref=market_ref,
                buy_percent=buy_percent,
                buy_price=buy_price,
                sell_percent=sell_percent,
                sell_price=sell_price,
                quantity=line.get("quantity", 1),
            )
            created_card_lines.append(stock_line)
            total_paid += buy_price * stock_line.quantity

        for line in sealed_lines_data:
            sealed_item = SealedStockItem.objects.create(
                invoice=invoice,
                product_name=line["product_name"],
                cost_price=line["cost_price"],
                rrp=line["rrp"],
                market_ref=line.get("market_ref"),
                quantity=line.get("quantity", 1),
            )
            created_sealed_lines.append(sealed_item)
            total_paid += line["cost_price"] * line.get("quantity", 1)

        subtotal = total_paid
        discount_amount = validated_data.get("discount_amount") or Decimal("0")
        if not discount_amount and invoice.discount_percent:
            discount_amount = (subtotal * invoice.discount_percent / Decimal("100")).quantize(Decimal("0.01"))

        invoice.subtotal = subtotal
        invoice.discount_amount = discount_amount
        invoice.total_paid = subtotal - discount_amount
        invoice.save(update_fields=["subtotal", "discount_amount", "total_paid"])

        self._created_card_lines = created_card_lines
        self._created_sealed_lines = created_sealed_lines
        return invoice
