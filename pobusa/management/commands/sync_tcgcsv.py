"""
Syncs CatalogProduct + TCGCSVSource directly from TCGCSV -- no dependency
on pokemart-api at all, per the requirement to keep PoBuSA fully
self-contained and never expose PokeBulk's own infrastructure.

v1.1 (PoBuSA Checklist Phase 1, item 2): now also populates
CatalogProduct.release_date from TCGCSV's group.publishedOn on every
upsert. Existing rows synced before this change stay null until either a
full re-sync touches them, or backfill_release_dates is run (much
cheaper -- hits only /groups, not /products + /prices).

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
from datetime import datetime

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from pobusa.models import Game, CatalogProduct, TCGCSVSource


def parse_release_date(published_on):
    """TCGCSV's group.publishedOn is an ISO-ish datetime string, sometimes
    with a 'Z' suffix and >6 fractional digits that predate-3.11 datetime.fromisoformat
    can't handle. We only need the date part for the Set picker's sort, so
    just slice the first 10 chars (YYYY-MM-DD) rather than fight the full
    format. Returns None on anything unparseable -- never crash a sync over
    a display-only field."""
    if not published_on:
        return None
    try:
        return datetime.strptime(published_on[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

TCGCSV_BASE = "https://tcgcsv.com/tcgplayer"
USER_AGENT = "PoBuSA-CatalogSync/1.0"
HEADERS = {"User-Agent": USER_AGENT}

# A full sync can run for hours (Magic alone is 450 sets). A single
# transient network blip -- Wi-Fi hiccup, laptop sleep, ISP routing
# glitch -- shouldn't kill the entire run this deep into it. Each request
# gets a few retries with a short backoff before actually giving up.
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 3


def get_json_with_retry(url, command=None):
    """GETs a URL and returns parsed JSON, retrying on connection failures
    (not on real HTTP error responses like 404/500 -- those are TCGCSV
    telling us something, not a network blip, and retrying won't fix
    them). Raises the last exception if every retry is exhausted."""
    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exception = e
            if command:
                command.stderr.write(
                    f"    Network error on attempt {attempt}/{MAX_RETRIES} for {url}: {e}"
                )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)  # 3s, 6s, 9s...
    raise last_exception

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
        groups_resp = get_json_with_retry(f"{TCGCSV_BASE}/{category_id}/groups", command=self)
        groups = groups_resp.get("results", [])

        product_count = 0
        for i, group in enumerate(groups, 1):
            price_by_product = self.fetch_prices(category_id, group['groupId'])

            products_resp = get_json_with_retry(
                f"{TCGCSV_BASE}/{category_id}/{group['groupId']}/products", command=self
            )
            for p in products_resp.get("results", []):
                # A single TCGCSV productId can have MULTIPLE priced
                # variants (Normal, Foil, Showcase, etc) -- each becomes
                # its own CatalogProduct row. A product with no price data
                # at all still gets one row, as an unpriced "Normal" entry,
                # so it's still findable via search even without a price.
                variant_rows = price_by_product.get(p['productId']) or [
                    {"subtype": "Normal", "market_price": None}
                ]
                for row in variant_rows:
                    self.upsert_product_variant(p, game, forced_type, group, row['subtype'], row['market_price'])
                    product_count += 1
            time.sleep(0.1)  # be a good neighbor, per TCGCSV's docs

            # Progress marker per set, not just per game -- a long game
            # like Magic (450 sets) or Yu-Gi-Oh (653 sets) can otherwise
            # look frozen for several minutes between the "Syncing X..."
            # line and the final summary.
            self.stdout.write(f"    [{i}/{len(groups)}] {group['name']} ({product_count} products so far)")

        self.stdout.write(self.style.SUCCESS(f"  {label}: {len(groups)} groups, {product_count} products synced\n"))

    def fetch_prices(self, category_id, group_id):
        """Returns {productId: [{"subtype": name, "market_price": price}, ...]}
        -- every priced variant for every product, not just the first one
        seen. TCGCSV's /prices endpoint returns one row per (productId,
        subTypeName) pair; a card available as both Normal and Foil shows
        up as two separate rows here, each with its own real price. Losing
        that distinction was the root cause of collapsed/wrong prices in
        the original version of this command."""
        resp = get_json_with_retry(f"{TCGCSV_BASE}/{category_id}/{group_id}/prices", command=self)
        by_product = {}
        for pr in resp.get("results", []):
            pid = pr['productId']
            by_product.setdefault(pid, []).append({
                "subtype": pr.get('subTypeName') or 'Normal',
                "market_price": pr.get('marketPrice'),
            })
        return by_product

    def upsert_product_variant(self, p, game, forced_type, group, subtype, market_price):
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
            source = TCGCSVSource.objects.select_related('product').get(
                tcgcsv_product_id=p['productId'], tcgcsv_subtype_name=subtype
            )
            product = source.product
        except TCGCSVSource.DoesNotExist:
            product = CatalogProduct(product_type=inferred_type, game=game)
            source = TCGCSVSource(tcgcsv_product_id=p['productId'], tcgcsv_subtype_name=subtype)

        product.product_type = inferred_type
        product.game = game
        # Defensively truncated to each field's actual max_length -- this is
        # what fixed the "value too long for type character varying(50)"
        # crash on Magic (some products' subtype data ran longer than
        # expected). Truncating here means a future oversized value on ANY
        # of these fields degrades to a shortened-but-saved product, rather
        # than crashing the entire sync run partway through again.
        product.name = p['name'][:255]
        product.set_name = group['name'][:255]
        product.card_number = card_number[:20]
        product.variant = subtype[:100]
        product.market_price = market_price
        product.image_url = p.get('imageUrl', '')
        product.release_date = parse_release_date(group.get('publishedOn'))
        product.is_active = True
        product.last_synced = timezone.now()

        if not product.sku:
            game_code = game.code if game else 'acc'
            base_sku = f"{game_code}-{slugify_part(group.get('abbreviation') or group['name'])}"
            if card_number:
                base_sku += f"-{slugify_part(card_number)}"
            # Variant goes in the SKU too, per the confirmed scheme
            # (poke-sv09-074-rev). "Normal" is the default/base printing,
            # so it's omitted to keep the common case's SKU clean --
            # only non-default variants (Foil, Showcase, etc) get suffixed.
            if subtype and subtype.lower() != 'normal':
                base_sku += f"-{slugify_part(subtype)}"
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
