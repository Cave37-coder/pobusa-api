"""
PoBuSA Checklist Phase 4, item 18 -- one-time setup for a brand new Client
deployment. Run this ONCE, right after `migrate`, on a fresh Client
deployment that has CATALOG_SERVICE_BASE pointed at the real catalog
authority (see PoBuSA_Client_Provisioning_Runbook.md for the full
sequence -- this command is only the "seed a Store row + a default
BuyPercentTier" step in that runbook).

Deliberately does NOT touch Game/CatalogProduct/TCGCSVSource -- a Client
deployment never runs seed_games or sync_tcgcsv (see CLAUDE.md and this
phase's own checklist side notes: catalog data belongs to the catalog
authority, never to any one Client).

Creates exactly one Store row and one BuyPercentTier covering every price
(min 0, no upper bound) at the given buy percent -- a sensible single
starting band, not a guess at any real Client's actual tiered buy-percent
policy. Split it into more bands via Django admin once you know what
they want.

Idempotent on the Store itself (get_or_create by name, so re-running with
the same --name is safe and won't duplicate it), but WILL add another
BuyPercentTier if run twice against a Store that already exists -- pass
--skip-tier on a re-run if you're only confirming the Store exists.

Usage:
    python manage.py provision_client_store \
        --name "Card Kingdom PTA" \
        --address "123 Main Rd, Pretoria" \
        --sell-percent 130 \
        --buy-percent 70
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from pobusa.models import Store, BuyPercentTier


class Command(BaseCommand):
    help = "ONE-TIME: seeds a new Client's Store row + a default BuyPercentTier. Never touches Game/CatalogProduct."

    def add_arguments(self, parser):
        parser.add_argument('--name', required=True, help='Store/Client display name')
        parser.add_argument('--address', required=True, help='Full address, for tax invoice headers')
        parser.add_argument('--sell-percent', required=True, help='Default sell %% over market reference (e.g. 130 = sell at 130%% of market)')
        parser.add_argument('--buy-percent', required=True, help='Single starting buy %% band, covers R0 and up -- refine into real tiers via admin later')
        parser.add_argument('--vat-registered', action='store_true', help='Set if this Client is VAT registered')
        parser.add_argument('--vat-number', default='', help='VAT number, if --vat-registered')
        parser.add_argument('--registration-number', default='', help='Company registration number, optional')
        parser.add_argument('--skip-tier', action='store_true', help="Don't create a BuyPercentTier (e.g. re-running against an existing Store)")

    def handle(self, *args, **options):
        try:
            sell_percent = Decimal(options['sell_percent'])
            buy_percent = Decimal(options['buy_percent'])
        except InvalidOperation:
            raise CommandError("--sell-percent and --buy-percent must be plain numbers, e.g. 130 or 70.5")

        store, created = Store.objects.get_or_create(
            name=options['name'],
            defaults={
                'address': options['address'],
                'sell_percent_default': sell_percent,
                'vat_registered': options['vat_registered'],
                'vat_number': options['vat_number'],
                'registration_number': options['registration_number'],
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Store: {store} (id={store.id})"))
        else:
            self.stdout.write(self.style.WARNING(f"Store '{store}' already existed (id={store.id}) -- not modified."))

        if options['skip_tier']:
            self.stdout.write("Skipped BuyPercentTier (--skip-tier).")
            return

        tier = BuyPercentTier.objects.create(
            store=store, min_value=Decimal('0'), max_value=None, buy_percent=buy_percent,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Created BuyPercentTier: R0+ -> {buy_percent}% (id={tier.id}). "
            "Split into more bands via Django admin once you know this Client's real tiered policy."
        ))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Next: point this Client's frontend build at this backend's URL, "
            f"with STORE_ID={store.id} in lib/api.ts."
        ))
