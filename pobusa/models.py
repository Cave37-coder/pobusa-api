# PoBuSA models.py — v1.6.0
# v1.6.0: added variant snapshot to CardStockLine, alongside name/card_set.
# condition is effectively a no-op across pokemart-api's dataset (defaults
# to NM almost everywhere); variant (Normal/Reverse Holo/Pokeball/etc) is
# the real distinguishing field for a card and must be captured at buy-in
# time so records are accurate, same as name/set already are.
# v1.5.0: expanded Invoice and Sale to match PokeBulk SA's manual invoice
# POS reference layout — discount %/amount, phone, delivery/collection
# notes, payment method as a pill-select (EFT/Cash/Card) + a received/made
# checkbox, separate from the earlier simpler versions. Same trading-ledger
# palette stays; this is a structural/field match only, not a visual one.

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator


class Store(models.Model):
    """A Client's shop instance. Single row today (Store #1); designed to scale
    to multiple Clients if PoBuSA is licensed out later."""
    name = models.CharField(max_length=255)
    logo = models.ImageField(upload_to="store_logos/", blank=True, null=True)

    # Compliance / invoice header fields
    registration_number = models.CharField(max_length=100, blank=True)
    address = models.TextField()
    vat_registered = models.BooleanField(default=False)
    vat_number = models.CharField(max_length=50, blank=True)

    # Pricing defaults — always overridable per line, see CardStockLine/SaleItem
    sell_percent_default = models.DecimalField(max_digits=5, decimal_places=2)

    created_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class BuyPercentTier(models.Model):
    """Configurable buy-in % bands per store, e.g. R0-999.99 -> 80%, R1000+ -> 70%.
    Acts as the default shown on the buy-in screen; always editable per card."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="buy_tiers")
    min_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)  # null = no upper bound
    buy_percent = models.DecimalField(max_digits=5, decimal_places=2,
                                       validators=[MinValueValidator(0), MaxValueValidator(100)])

    class Meta:
        ordering = ["min_value"]

    def __str__(self):
        upper = self.max_value if self.max_value is not None else "+"
        return f"{self.store} | R{self.min_value}-{upper} -> {self.buy_percent}%"


class Invoice(models.Model):
    """One buy-in batch — the admin-reducing wrapper around many card/sealed lines."""

    PAYMENT_METHOD_CHOICES = [
        ("eft", "EFT"),
        ("cash", "Cash"),
        ("card", "Card"),
    ]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="invoices")
    invoice_number = models.CharField(max_length=50, unique=True)  # sequential, never reused
    date = models.DateField()
    seller_name = models.CharField(max_length=255, blank=True)  # walk-in seller, not a registered contact
    seller_email = models.EmailField(blank=True)  # optional — invoice emailed here if provided, no printing needed
    seller_phone = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)  # collection/delivery note equivalent for a buy-in

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, blank=True)
    payment_made = models.BooleanField(default=False)  # matches PokeBulk's "Payment received" pattern

    voided = models.BooleanField(default=False)  # never hard-delete; keeps sequence intact

    def __str__(self):
        return self.invoice_number


class CardStockLine(models.Model):
    """A single card bought in under an Invoice. Prices are snapshotted at time of
    purchase so historical invoices stay accurate even if store defaults change later."""

    CONDITION_CHOICES = [
        ("NM", "Near Mint"),
        ("LP", "Lightly Played"),
        ("MP", "Moderately Played"),
        ("HP", "Heavily Played"),
        ("DMG", "Damaged"),
    ]
    STATUS_CHOICES = [
        ("in_stock", "In stock"),
        ("sold", "Sold"),
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="card_lines")

    # card_id uses the existing PokeBulk SKU format, e.g. sv09-074-rev
    # Structural only — safe to expose to Clients. The TCGCSV mapping lives
    # in a separate internal-only table (PricingSourceMap), never joined here.
    card_id = models.CharField(max_length=50)

    # Snapshotted from pokemart-api at buy-in time so inventory search can
    # match on name, and the record stays self-contained (see v1.3.0 note).
    name = models.CharField(max_length=200, blank=True)
    card_set = models.CharField(max_length=200, blank=True)
    variant = models.CharField(max_length=50, blank=True)  # e.g. Normal, Reverse Holo, Pokeball

    condition = models.CharField(max_length=3, choices=CONDITION_CHOICES, default="NM")

    market_ref = models.DecimalField(max_digits=10, decimal_places=2)  # TCG value snapshot at purchase

    buy_percent = models.DecimalField(max_digits=5, decimal_places=2)  # tier default, overridable per line
    buy_price = models.DecimalField(max_digits=10, decimal_places=2)  # calculated, stored

    sell_percent = models.DecimalField(max_digits=5, decimal_places=2)  # store default, overridable per line
    sell_price = models.DecimalField(max_digits=10, decimal_places=2)  # calculated, stored — the "set price"

    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="in_stock")
    date_sold = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.card_id} ({self.condition})"


class SealedStockItem(models.Model):
    """Sealed product — cost/RRP/market_ref are three separate numbers.
    Sell price defaults to RRP, not a % of market_ref."""
    STATUS_CHOICES = [
        ("in_stock", "In stock"),
        ("sold", "Sold"),
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="sealed_lines")
    product_name = models.CharField(max_length=255)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)  # what the shop paid
    rrp = models.DecimalField(max_digits=10, decimal_places=2)  # what it sells for
    market_ref = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)  # reference only

    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="in_stock")

    def __str__(self):
        return self.product_name


class GeneralInventoryItem(models.Model):
    """Anything non-card: cooldrinks, accessories, etc. Same category tab
    interface as cards/sealed on the front end."""
    CATEGORY_CHOICES = [
        ("cooldrinks", "Cooldrinks"),
        ("accessories", "Accessories"),
        ("other", "Other"),
    ]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="general_items")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    name = models.CharField(max_length=255)
    barcode = models.CharField(max_length=50, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    sell_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=0)
    date_added = models.DateField(auto_now_add=True)

    def __str__(self):
        return self.name


class Sale(models.Model):
    """A completed sale — a Customer transaction, may mix cards, sealed, and
    general items. Decrements stock on whichever source table each item came from."""
    PAYMENT_CHOICES = [
        ("cash", "Cash"),
        ("card", "Card"),
        ("eft", "EFT"),
        ("other", "Other"),
    ]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="sales")
    sale_number = models.CharField(max_length=50, unique=True)  # sequential, never reused
    date = models.DateTimeField(auto_now_add=True)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)

    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    payment_received = models.BooleanField(default=True)  # matches PokeBulk's checkbox pattern
    voided = models.BooleanField(default=False)  # mark voided, never delete — keeps sequence intact

    # Buyer details — name/address/VAT only required once a sale crosses R5,000
    # (full tax invoice threshold). Email is separate — always optional,
    # captured whenever the Customer wants a receipt emailed, any sale size.
    buyer_name = models.CharField(max_length=255, blank=True)
    buyer_email = models.EmailField(blank=True)
    buyer_phone = models.CharField(max_length=50, blank=True)
    buyer_address = models.TextField(blank=True)
    buyer_vat_number = models.CharField(max_length=50, blank=True)
    delivery_note = models.TextField(blank=True)

    def __str__(self):
        return self.sale_number


class SaleItem(models.Model):
    """One line on a Sale — polymorphic-ish link to whichever stock table it sold from.
    source_type='custom' is the off-site/not-in-catalog case — no backing
    stock record, source_id is null, description/unit_price are exactly
    what staff typed in (matches PokeBulk's off-site item pattern)."""
    SOURCE_CHOICES = [
        ("card", "Card stock"),
        ("sealed", "Sealed stock"),
        ("general", "General inventory"),
        ("custom", "Off-site / not in catalog"),
    ]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    source_type = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    source_id = models.PositiveIntegerField(null=True, blank=True)  # null for source_type='custom'

    description = models.CharField(max_length=255)  # snapshot for the receipt, in case source item changes later
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)


class CreditNote(models.Model):
    """Refunds — always linked back to the original sale, never just a stock reversal."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="credit_notes")
    credit_note_number = models.CharField(max_length=50, unique=True)
    original_sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="credit_notes")
    date = models.DateField()
    reason = models.TextField()  # SARS requires a real explanation, not just "adjustment"
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return self.credit_note_number


class StoreStaff(models.Model):
    """Links a Django User to a Store, scoping their access to that Store
    only (Section 7 permission tiers). Mike (superuser) bypasses this
    entirely via Django admin — this model is what scopes Client staff."""
    ROLE_CHOICES = [
        ("owner", "Store owner"),   # full access to their own store's data
        ("staff", "Staff"),          # day-to-day POS use, same data access as owner for now
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pobusa_staff")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="staff_members")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="staff")

    def __str__(self):
        return f"{self.user} @ {self.store.name} ({self.role})"


class DailySalesSummary(models.Model):
    """Nightly aggregation — feeds both the PDF report and the CSV export.
    Also the 'telemetry hook' for a possible future turnover-based pricing model."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="daily_summaries")
    date = models.DateField()
    total_buy_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_sell_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transaction_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ["store", "date"]


class AccountingExportSettings(models.Model):
    """Per-store GL account mapping for the accounting CSV export (Pastel/Sage/Xero/generic)."""
    FORMAT_CHOICES = [
        ("pastel", "Sage Pastel"),
        ("sage", "Sage Business Cloud"),
        ("xero", "Xero"),
        ("generic", "Generic"),
    ]

    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="accounting_settings")
    sales_account_code = models.CharField(max_length=50)
    cogs_account_code = models.CharField(max_length=50)
    vat_account_code = models.CharField(max_length=50)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=15.00)
    export_format = models.CharField(max_length=10, choices=FORMAT_CHOICES, default="generic")
    date_format = models.CharField(max_length=20, default="%Y-%m-%d")
