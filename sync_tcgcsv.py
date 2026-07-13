"""
Syncs CatalogProduct + TCGCSVSource directly from TCGCSV -- no dependency
on pokemart-api at all, per the requirement to keep PoBuSA fully
self-contained and never expose PokeBulk's own infrastructure.

Usage:
    python manage.py sync_tcgcsv                  # sync all active Games + accessories
    python manage.py sync_tcgcsv --game magic      # sync just one game
    python manage.py sync_tcgcsv --accessories-only

Run seed_games.py first (once) to create the Game rows this depends on.

Respects TCGCSV's usage guidelines (custom User-Agent, small delay between
requests) -- see https://tcgcsv.com/docs
"""
import re
import time

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from pobusa.models import Game, CatalogProduct, TCGCSVSource

TCGCSV_BASE = "https://tcgcsv.com/tcgplayer"
USER_AGENT = "PoBuSA-CatalogSync/1.0"
HEADERS = {"User-Agent": USER_AGENT}

# Accessory categories are game-agnostic -- synced with game=None,
# product_type='accessory', regardless of which TCGCSV category they came
# from. Confirmed exact category IDs as of July 2026.
ACCESSORY_CATEGORY_IDS = {
    14: "Supplies",
    31: "Card Sleeves",
    32: "Deck Boxes",
    33: "Card Storage Tins",
    34: "Life Counters",
    35: "Playmats",
    49: "Protective Pages",
    50: "Storage Albums",
    51: "Collectible Storage",
    52: "Supply Bundles",
    82: "TCGplayer Supplies",
}


def slugify_part(value):
    """Lowercase, hyphenated, safe for a SKU segment. 'x' fallback so an
    empty/missing value never produces a trailing or double hyphen."""
    slug = re.sub(r'[^a-z0-9]+', '-', (value or '').lower()).strip('-')
    return slug or 'x'


# Confirmed via tcgcsv_bible.csv analysis -- unambiguous sealed-product
# terms. Deliberately NOT including generic words like "Case", "Box", or
# "Bundle" alone -- those show up in real single-card names too (e.g. a
# Magic card called "Collector's Case" or "Door of Destinies"). Only exact
# phrases confirmed to be product-line naming, not flavor text, belong here.
SEALED_NAME_KEYWORDS = [
    "Booster Box Case", "Booster Box", "Booster Pack", "Booster Bundle",
    "Elite Trainer Box", "Starter Deck", "Structure Deck", "Theme Deck",
    "Premium Pack Set", "Premium Anniversary Box", "Anniversary Box",
    "Collector Booster", "Expansion Deck Box Set",
]


class Command(BaseCommand):
    help = "Syncs CatalogProduct + TCGCSVSource from TCGCSV for active Games and/or accessory categories."

    def add_arguments(self, parser):
        parser.add_argument('--game', type=str, help='Sync only this Game code (e.g. "magic")')
        parser.add_argument('--accessories-only', action='store_true', help='Sync only accessory categories')

    def name_looks_sealed(self, name):
        return any(keyword.lower() in (name or '').lower() for keyword in SEALED_NAME_KEYWORDS)

    def handle(self, *args, **options):
        if options.get('accessories_only'):
            for cat_id, label in ACCESSORY_CATEGORY_IDS.items():
                self.sync_category(cat_id, game=None, forced_type='accessory', label=label)
            return

        games = Game.objects.filter(is_active=True)
        if options.get('game'):
            games = games.filter(code=options['game'])
            if not games.exists():
                self.stderr.write(self.style.ERROR(
                    f"No active Game with code '{options['game']}'. Run seed_games first, or check the code."
                ))
                return

        for game in games:
            self.sync_category(game.tcgcsv_category_id, game=game, forced_type=None, label=game.name)

        if not options.get('game'):
            self.stdout.write("\nSyncing accessory categories...")
            for cat_id, label in ACCESSORY_CATEGORY_IDS.items():
                self.sync_category(cat_id, game=None, forced_type='accessory', label=label)

    def sync_category(self, category_id, game, forced_type, label):
        self.stdout.write(f"Syncing {label} (TCGCSV category {category_id})...")
        groups_resp = requests.get(f"{TCGCSV_BASE}/{category_id}/groups", headers=HEADERS).json()
        groups = groups_resp.get("results", [])

        for group in groups:
            price_by_product = self.fetch_prices(category_id, group['groupId'])

            products_resp = requests.get(
                f"{TCGCSV_BASE}/{category_id}/{group['groupId']}/products", headers=HEADERS
            ).json()
            for p in products_resp.get("results", []):
                self.upsert_product(p, game, forced_type, group, price_by_product)
            time.sleep(0.1)  # be a good neighbor, per TCGCSV's docs

        self.stdout.write(self.style.SUCCESS(f"  {label}: {len(groups)} groups synced"))

    def fetch_prices(self, category_id, group_id):
        resp = requests.get(f"{TCGCSV_BASE}/{category_id}/{group_id}/prices", headers=HEADERS).json()
        by_product = {}
        for pr in resp.get("results", []):
            pid = pr['productId']
            # Keep the first market price seen per product as the catalog
            # reference value -- individual variant pricing (Holofoil vs
            # Normal etc.) is a finer grain than this catalog needs; buy-in
            # staff always confirm/override the actual price per line anyway.
            if pid not in by_product and pr.get('marketPrice') is not None:
                by_product[pid] = pr['marketPrice']
        return by_product

    def upsert_product(self, p, game, forced_type, group, price_by_product):
        extended = {e['name']: e['value'] for e in p.get('extendedData', [])}
        card_number = extended.get('Number', '') or extended.get('CardNumber', '')

        if forced_type:
            inferred_type = forced_type
        elif self.name_looks_sealed(p['name']):
            # Checked BEFORE the Number/Rarity heuristic. Discovered via
            # tcgcsv_bible.csv: Dragon Ball Super: Masters tags some sealed
            # products (Booster Box, Booster Box Case, Display, Starter
            # Deck) inconsistently enough that the field-presence heuristic
            # alone let 198 real sealed products slip through as 'single'.
            # A name match on an unambiguous sealed keyword is a more
            # reliable signal than field presence, so it takes priority.
            inferred_type = 'sealed'
        else:
            inferred_type = 'single' if ('Number' in extended or 'Rarity' in extended) else 'sealed'

        try:
            source = TCGCSVSource.objects.select_related('product').get(tcgcsv_product_id=p['productId'])
            product = source.product
        except TCGCSVSource.DoesNotExist:
            product = CatalogProduct(product_type=inferred_type, game=game)
            source = TCGCSVSource(tcgcsv_product_id=p['productId'])

        product.product_type = inferred_type
        product.game = game
        product.name = p['name']
        product.set_name = group['name']
        product.card_number = card_number
        product.variant = extended.get('Printing', '') or extended.get('SubType', '')
        product.market_price = price_by_product.get(p['productId'])
        product.image_url = p.get('imageUrl', '')
        product.is_active = True
        product.last_synced = timezone.now()

        if not product.sku:
            game_code = game.code if game else 'acc'
            base_sku = f"{game_code}-{slugify_part(group.get('abbreviation') or group['name'])}"
            if card_number:
                base_sku += f"-{slugify_part(card_number)}"
            sku = base_sku
            suffix = 2
            while CatalogProduct.objects.filter(sku=sku).exclude(pk=product.pk if product.pk else -1).exists():
                sku = f"{base_sku}-{suffix}"
                suffix += 1
            product.sku = sku

        product.save()

        source.product = product
        source.tcgcsv_category_id = p['categoryId']
        source.tcgcsv_group_id = p['groupId']
        source.tcgcsv_group_name = group['name']
        source.save()
