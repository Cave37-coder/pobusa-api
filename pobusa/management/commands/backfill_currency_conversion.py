"""
ONE-TIME fix for the 2026-08-10 USD-as-ZAR bug -- every CatalogProduct row
synced before sync_tcgcsv.py v1.2 has market_price sitting in raw USD
(TCGCSV's marketPrice, never converted). This multiplies every existing
non-null market_price by sync_tcgcsv's USD_TO_ZAR_RATE, once, so the
already-fetched data becomes correct without re-hitting TCGCSV for
299,728 rows (a multi-hour network run) just to fix a units bug.

Do NOT run this a second time against data that's already been converted
-- it will double-convert (USD -> ZAR -> "ZAR-squared"). It's safe to run
multiple times only while you're still confirming the fix looks right
(dry-run by default; nothing is written until --confirm is passed).

If USD_TO_ZAR_RATE changes in the future (e.g. next month, R16.20 -> R17.00),
the right move is to re-run sync_tcgcsv (it re-fetches real USD prices from
TCGCSV and converts at whatever rate is current then) -- not this script
again, which only knows how to do the one-time raw-USD -> ZAR conversion.

Usage:
    python manage.py backfill_currency_conversion            # dry run, shows what would change
    python manage.py backfill_currency_conversion --confirm  # actually writes
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from pobusa.models import CatalogProduct
from pobusa.management.commands.sync_tcgcsv import USD_TO_ZAR_RATE

TWO_DP = Decimal("0.01")


class Command(BaseCommand):
    help = "ONE-TIME: multiplies existing CatalogProduct.market_price by USD_TO_ZAR_RATE to fix the 2026-08-10 USD-as-ZAR bug. Dry-run unless --confirm."

    def add_arguments(self, parser):
        parser.add_argument('--confirm', action='store_true', help='Actually write the change (default is dry-run)')

    def handle(self, *args, **options):
        confirm = options['confirm']

        qs = CatalogProduct.objects.exclude(market_price=None)
        total = qs.count()
        self.stdout.write(f"USD_TO_ZAR_RATE = {USD_TO_ZAR_RATE}")
        self.stdout.write(f"{total} rows have a market_price to convert.")

        sample = list(qs.order_by('-market_price')[:5])
        self.stdout.write("\nSample of the highest-priced rows (before -> after):")
        for p in sample:
            after = (p.market_price * USD_TO_ZAR_RATE).quantize(TWO_DP)
            self.stdout.write(f"  {p.sku:30s} {p.name[:40]:40s} R{p.market_price} -> R{after}")

        if not confirm:
            self.stdout.write(self.style.WARNING(
                "\nDry run only -- nothing written. Re-run with --confirm to apply this to all "
                f"{total} rows."
            ))
            return

        self.stdout.write(f"\nApplying to all {total} rows...")
        updated = 0
        batch = []
        BATCH_SIZE = 2000
        with transaction.atomic():
            for p in qs.iterator(chunk_size=2000):
                p.market_price = (p.market_price * USD_TO_ZAR_RATE).quantize(TWO_DP)
                batch.append(p)
                if len(batch) >= BATCH_SIZE:
                    CatalogProduct.objects.bulk_update(batch, ['market_price'])
                    updated += len(batch)
                    self.stdout.write(f"  ...{updated}/{total}")
                    batch = []
            if batch:
                CatalogProduct.objects.bulk_update(batch, ['market_price'])
                updated += len(batch)

        self.stdout.write(self.style.SUCCESS(f"\nDone -- {updated} rows converted from USD to ZAR."))
