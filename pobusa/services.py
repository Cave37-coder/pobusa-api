# PoBuSA services.py — v1.7.0
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
# directly — it just asks pokemart-api's lookup endpoint for the card, and
# the current price comes back in the same response. Whatever field name
# pokemart-api uses internally, it's renamed to market_ref immediately here
# so no TCGCSV-related naming ever appears anywhere else in the codebase.
#
# v1.2.0: LOCAL DEV — pointed at pokemart-api's local dev server (port 8000)
# instead of the production custom domain. Swap back to
# "https://api.pokebulk.co.za" before deploying PoBuSA to Railway for real.

import requests
from decimal import Decimal
from .models import BuyPercentTier

POKEMART_API_BASE = "http://127.0.0.1:8000"  # LOCAL DEV ONLY — see note above


def search_cards(query: str) -> dict:
    """Searches pokemart-api for NM cards matching every word in the query
    against name, set name, set code, and card number together. Returns
    {"results": [...], "total_matches": N} — total_matches lets the
    frontend tell staff when a search is being truncated."""
    if not query.strip():
        return {"results": [], "total_matches": 0}

    resp = requests.get(f"{POKEMART_API_BASE}/api/cards/search/", params={"q": query}, timeout=5)
    if resp.status_code != 200:
        return {"results": [], "total_matches": 0}

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
        }
        for r in data.get("results", [])
    ]
    return {"results": results, "total_matches": data.get("total_matches", len(results))}


def fetch_card_data(card_id: str) -> dict:
    """Looks up a card by its PokeBulk SKU and returns both structural info
    (name/set/number) and the current price, in one call. pokemart-api
    already has the TCGCSV-synced price on the product/variant — this
    function just asks for it and relabels it market_ref.
    Raises ValueError if the card isn't found."""
    resp = requests.get(f"{POKEMART_API_BASE}/api/cards/lookup/", params={"sku": card_id}, timeout=5)
    if resp.status_code != 200:
        raise ValueError(f"Card {card_id} not found in catalog")
    data = resp.json()

    # Adjust the source field name below once the actual pokemart-api response
    # shape is confirmed — placeholder assumes a "price" field on the response.
    raw_price = data.get("price")
    if raw_price is None:
        raise ValueError(f"No synced price available for {card_id}")

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
