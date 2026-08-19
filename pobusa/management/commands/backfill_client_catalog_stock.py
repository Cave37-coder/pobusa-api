"""
ONE-TIME bridge command for PoBuSA Checklist Phase 4, item 11 -- copies
any values already hand-set in CatalogProduct.stock_quantity (the Phase 3,
item 9 manual bridge) into the new, real ClientCatalogStock table (Phase 4,
item 10), for a given store, so nothing Mike entered via Django admin over
the last few days gets lost once item 12 repoints the Sell screen's
in-stock filter at ClientCatalogStock instead.

Safe to run more than once -- it's an upsert (update_or_create), not an
additive increment, so re-running just re-syncs ClientCatalogStock to
whatever CatalogProduct.stock_quantity currently says. Once item 13 lands
(CatalogProduct.stock_quantity fully retired) this command becomes a
no-op / historical leftover from the cutover -- fine to leave in place,
nothing depends on removing it.

Usage:
    python manage.py backfill_client_catalog_stock --store 1            # dry run
    python manage.py backfill_client_catalog_stock --store 1 --confirm  # actually writes
    python manage.py backfill_client_catalog_stock --confirm            # only if exactly one Store exists
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from pobusa.models import CatalogProduct, ClientCatalogStock, Store


class Command(BaseCommand):
    help = "ONE-TIME: copies CatalogProduct.stock_quantity into ClientCatalogStock for a store. Dry-run unless --confirm."

    def add_arguments(self, parser):
        parser.add_argument('--store', type=int, help='Store id to backfill (required unless exactly one Store exists)')
        parser.add_argument('--confirm', action='store_true', help='Actually write the change (default is dry-run)')

    def handle(self, *args, **options):
        confirm = options['confirm']
        store_id = options.get('store')

        if store_id:
            try:
                store = Store.objects.get(pk=store_id)
            except Store.DoesNotExist:
                raise CommandError(f"No Store with id {store_id}.")
        else:
            stores = Store.objects.all()
            if stores.count() != 1:
                raise CommandError(
                    f"{stores.count()} Store rows exist -- pass --store <id> explicitly rather than guessing which one."
                )
            store = stores.first()
            self.stdout.write(f"No --store given, defaulting to the only Store: {store} (id={store.id})")

        # PositiveIntegerField, default 0, never null -- "not zero" is the
        # whole filter, no None-handling needed (unlike market_price).
        qs = CatalogProduct.objects.exclude(stock_quantity=0)
        total = qs.count()
        self.stdout.write(f"Store: {store} (id={store.id})")
        self.stdout.write(f"{total} CatalogProduct rows have a non-zero stock_quantity to copy.")

        sample = list(qs.order_by('-stock_quantity')[:5])
        if sample:
            self.stdout.write("\nSample (sku, name, stock_quantity):")
            for p in sample:
                self.stdout.write(f"  {p.sku:30s} {p.name[:40]:40s} {p.stock_quantity}")

        if not confirm:
            self.stdout.write(self.style.WARNING(
                f"\nDry run only -- nothing written. Re-run with --confirm to copy all {total} rows "
                f"into ClientCatalogStock for {store}."
            ))
            return

        self.stdout.write(f"\nApplying to all {total} rows...")
        updated = 0
        with transaction.atomic():
            for p in qs.iterator(chunk_size=2000):
                ClientCatalogStock.objects.update_or_create(
                    store=store, sku=p.sku, defaults={"quantity": p.stock_quantity},
                )
                updated += 1
                if updated % 500 == 0:
                    self.stdout.write(f"  ...{updated}/{total}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone -- {updated} ClientCatalogStock rows created/updated for {store}."
        ))
