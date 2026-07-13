# PoBuSA services.py — v1.9.0
# v1.9.0: EXPANDED SCOPE. search_cards() and fetch_card_data() now check
# BOTH sources and merge transparently: pokemart-api for Pokemon (unchanged
# behavior, still the only source for Pokemon), PoBuSA's own CatalogProduct
# table for the other 10 games + accessories synced via sync_tcgcsv.py.
# Neither views.py nor serializers.py needed any changes -- both call
# through these two functions, and the response shape is unchanged, so the
# merge is invisible above this layer. This is also why card_id lookups
# now try the LOCAL catalog first, then fall back to pokemart-api: Pokemon
# SKUs only ever existed on pokemart-api's side, so a local miss falling
# through to the remote call is the correct order, not an arbitrary choice.
#
# Also added: the remote pokemart-api call is now wrapped in a try/except
# for connection failures, not just a status-code check. Before this
# version, if pokemart-api was ever unreachable, search_cards() would
# raise and take down search for every game, not just Pokemon -- now a
# Pokemon-source outage degrades gracefully to "no Pokemon results" while
# every other game keeps working.
#
# v1.8.0: PRODUCTION — switched POKEMART_API_BASE back to the real
# api.pokebulk.co.za domain. The localhost:8000 value was only ever meant
# for local dev testing and is completely unreachable from Railway's
# servers, causing every card-search/card-lookup call to fail with a 500.
# v1.7.0: fetch_card_data() now also returns variant — condition is
# effectively a no-op on this dataset (defaults to NM almost everywhere),
# variant is the real distinguishing field and must be captured everywhere
# a card is looked up, not just in search results.
# v1.6.0: search_cards() now passes through the variant label too, so
# staff can tell apart same-numbered cards that differ only by
# Normal/Reverse Holo/Pokeball etc.
# v1.5.0: search_cards() now returns a dict with results + total_matches,
# matching pokemart-api's updated card_search response — lets the buy-in
# screen tell staff when a search is being truncated (e.g. 'Gastly' alone
# matches 60+ cards, only the first 30 come back).
#
# v1.1.0: pokemart-api already syncs TCGCSV prices into its own product
# database via sync_tcgcsv.py, matched on productId + subTypeName (variant).
# So PoBuSA no longer maintains its own PricingSourceMap or calls TCGCSV
# directly for Pokemon — it just asks pokemart-api's lookup endpoint for
# the card, and the current price comes back in the same response.
# Whatever field name pokemart-api uses internally, it's renamed to
# market_ref immediately here so no TCGCSV-related naming ever appears
# anywhere else in the codebase. For every OTHER game, the equivalent
# rename happens against PoBuSA's own CatalogProduct.market_price instead
# — same principle, same result: nothing downstream of this file ever
# needs to know or care which source a card came from.

import requests
from decimal import Decimal
from django.db.models import Q

from .models import BuyPercentTier, CatalogProduct

POKEMART_API_BASE = "https://api.pokebulk.co.za"  # production — stable custom domain

# How many results each source contributes before merging and re-capping
# at 30 total. Kept generous per-source so a game-specific search (e.g.
# "charizard" typed while only Magic/One Piece/etc are actually relevant)
# doesn't get crowded out by an unrelated source maxing out first.
PER_SOURCE_LIMIT = 30
COMBINED_LIMIT = 30


def _search_pokemon(query: str) -> tuple[list, int]:
    """Searches pokemart-api for Pokemon only. Returns (results, total_matches).
    Network failures degrade to (empty list, 0) rather than raising —
    a Pokemon-source outage should never take down search for every
    other game."""
    try:
        resp = requests.get(f"{POKEMART_API_BASE}/api/cards/search/", params={"q": query}, timeout=5)
    except requests.RequestException:
        return [], 0

    if resp.status_code != 200:
        return [], 0

    data = resp.json()
    results = [
        {
            "card_id": r["sku"],
            "name": r.get("name"),
            "set": r.get("set"),
            "set_code": r.get("set_code"),
            "number": r.get("number"),
            "variant": r.get("variant"),
            "market_ref": Decimal(str(r["price"])),
            "price_available": Decimal(str(r["price"])) > 0,
        }
        for r in data.get("results", [])
    ]
    return results, data.get("total_matches", len(results))


def _search_local_catalog(query: str) -> tuple[list, int]:
    """Searches PoBuSA's own CatalogProduct table -- every game except
    Pokemon, plus accessories (see models.py catalog module docstring).
    Same word-splitting, order-independent match as the Pokemon side, so
    results from both sources behave identically to staff regardless of
    which one actually matched."""
    words = query.split()
    queryset = CatalogProduct.objects.select_related("game").filter(is_active=True)

    for word in words:
        queryset = queryset.filter(
            Q(name__icontains=word)
            | Q(set_name__icontains=word)
            | Q(card_number__iexact=word)
        )

    total = queryset.count()
    products = queryset.order_by("name")[:PER_SOURCE_LIMIT]

    results = [
        {
            "card_id": p.sku,
            "name": p.name,
            "set": p.set_name,
            "set_code": None,  # not currently stored separately on CatalogProduct
            "number": p.card_number,
            "variant": p.variant or None,
            "market_ref": p.market_price if p.market_price is not None else Decimal("0.00"),
            "price_available": p.market_price is not None and p.market_price > 0,
        }
        for p in products
    ]
    return results, total


def search_cards(query: str) -> dict:
    """Searches BOTH sources and merges: pokemart-api for Pokemon, PoBuSA's
    own CatalogProduct for every other game + accessories. Same response
    shape as before this version -- {"results": [...], "total_matches": N}
    -- so views.py and the frontend need no changes. total_matches is the
    sum across both sources, so staff still get an accurate "there are
    more than this, narrow your search" signal even when both sources
    have partial matches."""
    if not query.strip():
        return {"results": [], "total_matches": 0}

    pokemon_results, pokemon_total = _search_pokemon(query)
    local_results, local_total = _search_local_catalog(query)

    combined = pokemon_results + local_results
    return {
        "results": combined[:COMBINED_LIMIT],
        "total_matches": pokemon_total + local_total,
    }


def fetch_card_data(card_id: str) -> dict:
    """Looks up a card by its SKU. Tries PoBuSA's own CatalogProduct first
    -- covers every game except Pokemon -- and only falls back to
    pokemart-api if not found locally, since Pokemon SKUs only ever
    existed on that side. Raises ValueError if not found in either place,
    same as before this version."""
    try:
        product = CatalogProduct.objects.select_related("game").get(sku=card_id, is_active=True)
    except CatalogProduct.DoesNotExist:
        pass
    else:
        if product.market_price is None or product.market_price == 0:
            # Fails loud on purpose. Confirmed via check_zero_prices.py that
            # ~1,722 sealed products (mostly niche case/bundle packaging
            # variants) have no synced price -- silently returning 0 here
            # would mean buy_price = 0 x buy_percent = 0, offering a seller
            # R0 for a potentially genuinely valuable item with no warning.
            # Raising instead forces the buy-in screen to prompt staff for
            # a manual price, same as it already does for true off-catalog
            # items -- a missing price is treated the same as "not found",
            # not treated as "worth nothing".
            raise ValueError(
                f"{card_id} was found but has no synced market price. "
                f"Enter the price manually for this line."
            )
        return {
            "card_id": card_id,
            "name": product.name,
            "set": product.set_name,
            "number": product.card_number,
            "variant": product.variant or None,
            "market_ref": product.market_price,
        }

    try:
        resp = requests.get(f"{POKEMART_API_BASE}/api/cards/lookup/", params={"sku": card_id}, timeout=5)
    except requests.RequestException:
        raise ValueError(f"Card {card_id} not found in catalog (and Pokemon source unreachable)")

    if resp.status_code != 200:
        raise ValueError(f"Card {card_id} not found in catalog")

    data = resp.json()
    raw_price = data.get("price")
    if raw_price is None or Decimal(str(raw_price)) == 0:
        raise ValueError(f"No synced market price available for {card_id}. Enter the price manually for this line.")

    return {
        "card_id": card_id,
        "name": data.get("name"),
        "set": data.get("set"),
        "number": data.get("number"),
        "variant": data.get("variant"),
        "market_ref": Decimal(str(raw_price)),
    }


def get_buy_percent(store_id: int, market_ref: Decimal) -> Decimal:
    """Finds the matching BuyPercentTier for a card's value. Returns the
    default % — the buy-in screen lets staff override this per line."""
    tiers = BuyPercentTier.objects.filter(store_id=store_id).order_by("min_value")
    for tier in tiers:
        if tier.max_value is None:
            if market_ref >= tier.min_value:
                return tier.buy_percent
        elif tier.min_value <= market_ref <= tier.max_value:
            return tier.buy_percent
    raise ValueError(f"No buy tier configured for store {store_id} covering R{market_ref}")


def calculate_buy_price(market_ref: Decimal, buy_percent: Decimal) -> Decimal:
    return (market_ref * buy_percent / Decimal("100")).quantize(Decimal("0.01"))


def calculate_sell_price(market_ref: Decimal, sell_percent: Decimal) -> Decimal:
    return (market_ref * sell_percent / Decimal("100")).quantize(Decimal("0.01"))
