"""
One-off backfill for CatalogProduct.release_date on rows synced before that
field existed (PoBuSA Checklist Phase 1, item 2).

Deliberately NOT a full sync_tcgcsv re-run -- that hits /products and
/prices per group too (hours for Magic/Yu-Gi-Oh alone). This only calls
each active Game's + each accessory category's /groups endpoint (one
cheap call per category, ~21 total), then bulk-updates every matching
CatalogProduct row's release_date by (category, set_name). Safe to
re-run any time; going forward sync_tcgcsv populates this field itself
on every normal sync, so this command should rarely be needed again.

Usage:
    python manage.py backfill_release_dates                  # all active games + accessories
    python manage.py backfill_release_dates --game magic      # just one game
    python manage.py backfill_release_dates --accessories-only
"""
import time
from datetime import datetime

import requests
from django.core.management.base import BaseCommand

from pobusa.models import Game, CatalogProduct
from pobusa.management.commands.sync_tcgcsv import (
    TCGCSV_BASE, HEADERS, ACCESSORY_CATEGORY_IDS, get_json_with_retry,
)


def parse_release_date(published_on):
    """Same rule as sync_tcgcsv.parse_release_date -- just the date part,
    None on anything unparseable. Duplicated rather than imported since
    sync_tcgcsv's own copy is the canonical one; this stays a thin,
    standalone script per this repo's convention (see
    download_images_serebii_limitless.py for the same pattern)."""
    if not published_on:
        return None
    try:
        return datetime.strptime(published_on[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class Command(BaseCommand):
    help = "Backfills CatalogProduct.release_date from TCGCSV's group.publishedOn, without a full re-sync."

    def add_arguments(self, parser):
        parser.add_argument('--game', type=str, help='Backfill only this Game code (e.g. "magic")')
        parser.add_argument('--accessories-only', action='store_true', help='Backfill only accessory categories')

    def handle(self, *args, **options):
        total_updated = 0

        if not options.get('accessories_only'):
            games = Game.objects.filter(is_active=True)
            if options.get('game'):
                games = games.filter(code=options['game'])
                if not games.exists():
                    self.stderr.write(self.style.ERROR(
                        f"No active Game with code '{options['game']}'."
                    ))
                    return
            for game in games:
                total_updated += self.backfill_category(
                    game.tcgcsv_category_id, label=game.name,
                    row_filter={'game': game},
                )

        if not options.get('game'):
            self.stdout.write("\nBackfilling accessory categories...")
            for cat_id, label in ACCESSORY_CATEGORY_IDS.items():
                total_updated += self.backfill_category(
                    cat_id, label=label,
                    row_filter={'game': None, 'product_type': 'accessory'},
                )

        self.stdout.write(self.style.SUCCESS(f"\nDone. {total_updated} CatalogProduct row(s) updated."))

    def backfill_category(self, category_id, label, row_filter):
        self.stdout.write(f"Backfilling {label} (TCGCSV category {category_id})...")
        groups_resp = get_json_with_retry(f"{TCGCSV_BASE}/{category_id}/groups", command=self)
        groups = groups_resp.get("results", [])

        updated = 0
        for group in groups:
            release_date = parse_release_date(group.get('publishedOn'))
            if release_date is None:
                continue
            # Scoped by category via tcgcsv_source, same key sync_tcgcsv uses,
            # so a set-name collision across two different games/categories
            # can't cross-contaminate the update.
            count = CatalogProduct.objects.filter(
                tcgcsv_source__tcgcsv_category_id=category_id,
                set_name=group['name'][:255],
                **row_filter,
            ).update(release_date=release_date)
            updated += count

        time.sleep(0.1)  # be a good neighbor, per TCGCSV's docs -- same as sync_tcgcsv
        self.stdout.write(f"  {label}: {len(groups)} groups checked, {updated} product row(s) updated")
        return updated
