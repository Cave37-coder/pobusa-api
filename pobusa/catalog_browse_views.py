# PoBuSA catalog_browse_views.py — v1.1.0
# v1.1.0: Pokemon added to the Singles browse, proxied live from
# pokemart-api instead of CatalogProduct (Pokemon was always deliberately
# excluded from sync_tcgcsv/CatalogProduct -- see models.py's module note).
# Same merge philosophy already established in pobusa/services.py's
# search_cards(): PoBuSA's own catalog handles every other game, pokemart-api
# handles Pokemon, and callers never need to know which source answered.
# Requires NO changes to pokemart-api itself -- it already exposes
# /api/sets/ and a fully filterable/paginated /api/products/ with real R2
# image URLs and ZAR prices; this just consumes what's already there.
# Scoped to product_type='single' only for now (pokemart-api's Pokemon
# accessories/sealed live in a different, not-yet-explored part of that
# codebase) -- flagged as a side note for later if wanted.
#
# Powers Phase 2, View 2 (Unified Browse/Sell screen): the nested category
# dropdown (Product Type -> Game/Accessory Type -> Set) and the product
# grid's search/browse against the real ~300k-row CatalogProduct table.
#
# SECURITY NOTE: browse_tree and sets_list are store-agnostic reference data
# (safe, no pricing) and stay open. browse_products takes a store_id because
# it computes a store-specific estimated sell price -- same no-auth-yet
# stance as branding_views.py/stats_views.py, same re-securing TODO.
#
# PRICING NOTE: the price shown here is an ESTIMATE (market_price x the
# store's sell_percent_default) for browsing purposes only -- it is NOT a
# stock record's real sell_price. Per spec section 5b, staff can always
# override it per-line in the cart before finalising a sale. Accessories
# carry no market_price on CatalogProduct at all (that only exists once an
# accessory is bought into GeneralInventoryItem), so their estimated price
# is always null -- the frontend shows "set at sale" and requires a manual
# price for those. Pokemon's pokemart-api price is treated as the same kind
# of market reference (same role as CatalogProduct.market_price) -- it's
# what services.py already calls market_ref for the buy-in side, so the
# sell-side estimate is computed the exact same way for consistency.

from decimal import Decimal, InvalidOperation

import requests
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Max

from .models import Game, CatalogProduct, Store

# Mirrors sync_tcgcsv.py's ACCESSORY_CATEGORY_IDS -- kept as its own copy
# here rather than importing a management command module into request-path
# code. If accessory categories ever change, update both places.
ACCESSORY_CATEGORY_LABELS = {
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

PRODUCT_TYPE_LABELS = {
    "single": "Singles",
    "sealed": "Sealed Product",
    "accessory": "Accessories",
}

DEFAULT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 60

# Pokemon isn't a row in PoBuSA's own Game table (by design -- see
# models.py). This sentinel ID routes browse-tree/sets/browse requests to
# pokemart-api instead of CatalogProduct. -1 can never collide with a real
# Game autoincrement PK. Matches services.py's POKEMART_API_BASE exactly.
POKEMON_GAME_ID = -1
POKEMON_GAME_NAME = "Pokémon"
POKEMART_API_BASE = "https://api.pokebulk.co.za"
POKEMART_TIMEOUT = 8


@api_view(["GET"])
def browse_tree(request):
    """GET /api/pobusa/catalog/browse-tree/
    First two levels of the nested dropdown in one small, cacheable payload:
    Product Type -> Games (for single/sealed) or Accessory Types (for
    accessory). The third level (Set) is fetched separately via sets_list
    once a specific game/accessory type is picked -- Set lists can be long,
    no reason to ship all of them up front."""
    games = [
        {"id": g.id, "code": g.code, "name": g.name}
        for g in Game.objects.filter(is_active=True).order_by("sort_order", "name")
    ]
    # Pokemon goes first -- it's the shop's flagship game, and it's the
    # only one not sourced from PoBuSA's own Game table.
    singles_games = [{"id": POKEMON_GAME_ID, "code": "pokemon", "name": POKEMON_GAME_NAME}] + games
    accessory_types = [
        {"category_id": cat_id, "label": label}
        for cat_id, label in sorted(ACCESSORY_CATEGORY_LABELS.items(), key=lambda kv: kv[1])
    ]

    return Response({
        "product_types": [
            {"code": "single", "label": PRODUCT_TYPE_LABELS["single"], "games": singles_games},
            {"code": "sealed", "label": PRODUCT_TYPE_LABELS["sealed"], "games": games},
            {"code": "accessory", "label": PRODUCT_TYPE_LABELS["accessory"], "accessory_types": accessory_types},
        ]
    })


def _pokemon_sets_list():
    """Proxies pokemart-api's /api/sets/ into the same {set_name,
    release_date} shape our own sets_list returns, newest-first. Blank
    release_date (older sets pokemart hasn't backfilled a date for) sorts
    last rather than crashing the sort."""
    try:
        resp = requests.get(f"{POKEMART_API_BASE}/api/sets/", timeout=POKEMART_TIMEOUT)
        resp.raise_for_status()
        sets = resp.json().get("results", [])
    except requests.RequestException:
        return Response([])

    sets_sorted = sorted(sets, key=lambda s: s.get("release_date") or "", reverse=True)
    return Response([
        {"set_name": s["name"], "release_date": s.get("release_date") or None}
        for s in sets_sorted
    ])


@api_view(["GET"])
def sets_list(request):
    """GET /api/pobusa/catalog/sets/?product_type=single&game=3
    Distinct Set names for a given product_type + game, newest-first (per
    the release_date field's own docstring — this is exactly what it's
    for). Not applicable to accessories (they have no set_name). game=-1
    means Pokemon -- proxied from pokemart-api instead of CatalogProduct."""
    product_type = request.query_params.get("product_type")
    game_id = request.query_params.get("game")

    if product_type not in ("single", "sealed"):
        return Response({"error": "product_type must be 'single' or 'sealed' — accessories have no sets."}, status=status.HTTP_400_BAD_REQUEST)
    if not game_id:
        return Response({"error": "game is required"}, status=status.HTTP_400_BAD_REQUEST)

    if game_id == str(POKEMON_GAME_ID):
        if product_type != "single":
            return Response([])  # Pokemon sealed isn't wired up yet -- see module note
        return _pokemon_sets_list()

    rows = (
        CatalogProduct.objects
        .filter(product_type=product_type, game_id=game_id, is_active=True)
        .exclude(set_name="")
        .values("set_name")
        .annotate(latest_release=Max("release_date"))
        .order_by("-latest_release", "set_name")
    )
    return Response([{"set_name": r["set_name"], "release_date": r["latest_release"]} for r in rows])


def _resolve_pokemon_set_code(set_name):
    """The Set dropdown round-trips the human-readable name (same UX as
    every other game), but pokemart-api's /api/products/ only filters by
    Set CODE (?card_set=DET), not name. Re-fetches the small /api/sets/
    list to resolve name -> code. Not cached -- this list is a few hundred
    rows and only refetched when a Pokemon set filter is actually active,
    not on every keystroke."""
    try:
        resp = requests.get(f"{POKEMART_API_BASE}/api/sets/", timeout=POKEMART_TIMEOUT)
        resp.raise_for_status()
        for s in resp.json().get("results", []):
            if s["name"] == set_name:
                return s["code"]
    except requests.RequestException:
        pass
    return None


def _estimate_from_market_ref(market_ref, sell_percent):
    if market_ref is None:
        return None
    try:
        market_ref = Decimal(str(market_ref))
    except (InvalidOperation, TypeError):
        return None
    return round(market_ref * sell_percent / 100, 2)


def _pokemon_browse(store, product_type, set_name, query, page, page_size):
    """Proxies pokemart-api's /api/products/ into the same browse-result
    shape CatalogProduct rows return, so the frontend's ProductGrid/
    VariantPanel never need to know or care which source answered --
    exactly the same "invisible above this layer" principle services.py's
    search_cards() already established for the buy-in side."""
    empty = {"count": 0, "page": page, "page_size": page_size, "has_more": False, "results": []}
    if product_type != "single":
        return Response(empty)  # Pokemon sealed isn't wired up yet -- see module note

    params = {"page": page, "page_size": page_size, "ordering": "card_number,variant_sort", "is_active": "true"}
    if query:
        params["search"] = query
    if set_name:
        code = _resolve_pokemon_set_code(set_name)
        if not code:
            return Response(empty)  # unknown/renamed set -- nothing to show rather than erroring
        params["card_set"] = code

    try:
        resp = requests.get(f"{POKEMART_API_BASE}/api/products/", params=params, timeout=POKEMART_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return Response({**empty, "error": "Pokemon source unreachable"})

    sell_percent = store.sell_percent_default
    results = []
    for p in data.get("results", []):
        card_set = p.get("card_set") or {}
        results.append({
            "id": p["id"],
            "sku": p.get("sku") or p.get("pb_id") or "",
            "name": p.get("name", ""),
            "set_name": card_set.get("name", ""),
            "card_number": str(p.get("card_number") or ""),
            "variant": p.get("variant_override") or "",
            "game": {"id": POKEMON_GAME_ID, "name": POKEMON_GAME_NAME},
            "thumbnail_url": p.get("image_small_url") or p.get("image_url") or None,
            "image_url": p.get("image_url") or None,
            "market_price": p.get("price"),
            "estimated_sell_price": _estimate_from_market_ref(p.get("price"), sell_percent),
        })

    return Response({
        "count": data.get("count", len(results)),
        "page": page,
        "page_size": page_size,
        "has_more": data.get("next") is not None,
        "results": results,
    })


@api_view(["GET"])
def browse_products(request, store_id):
    """GET /api/pobusa/stores/<store_id>/catalog/browse/
        ?product_type=single&game=3&set=Scarlet+%26+Violet&accessory_type=31&q=charizard&page=1&page_size=40

    Paginated product search/browse for the grid. Results are ordered by
    name/card_number/id so that different print variants of the same card
    (separate CatalogProduct rows — see TCGCSVSource's docstring) land
    adjacent to each other on the same page; the frontend groups those into
    a single tile and expands them in the variant panel on select.
    game=-1 means Pokemon -- proxied live from pokemart-api."""
    try:
        store = Store.objects.get(pk=store_id)
    except Store.DoesNotExist:
        return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)

    product_type = request.query_params.get("product_type")
    if product_type not in ("single", "sealed", "accessory"):
        return Response({"error": "product_type must be one of: single, sealed, accessory"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = min(MAX_PAGE_SIZE, max(1, int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE

    if request.query_params.get("game") == str(POKEMON_GAME_ID):
        return _pokemon_browse(
            store, product_type,
            request.query_params.get("set"),
            request.query_params.get("q", "").strip(),
            page, page_size,
        )

    qs = CatalogProduct.objects.filter(product_type=product_type, is_active=True)

    if product_type in ("single", "sealed"):
        game_id = request.query_params.get("game")
        if game_id:
            qs = qs.filter(game_id=game_id)
        set_name = request.query_params.get("set")
        if set_name:
            qs = qs.filter(set_name=set_name)
    else:  # accessory
        accessory_type = request.query_params.get("accessory_type")
        if accessory_type:
            qs = qs.filter(tcgcsv_source__tcgcsv_category_id=accessory_type)

    query = request.query_params.get("q", "").strip()
    if query:
        qs = qs.filter(name__icontains=query)

    qs = qs.select_related("game").order_by("name", "card_number", "id")
    total = qs.count()
    start = (page - 1) * page_size
    page_rows = qs[start:start + page_size]

    sell_percent = store.sell_percent_default

    results = [
        {
            "id": p.id,
            "sku": p.sku,
            "name": p.name,
            "set_name": p.set_name,
            "card_number": p.card_number,
            "variant": p.variant,
            "game": {"id": p.game.id, "name": p.game.name} if p.game else None,
            "thumbnail_url": p.thumbnail_url or p.image_url or None,
            "image_url": p.image_url or None,
            "market_price": p.market_price,
            "estimated_sell_price": _estimate_from_market_ref(p.market_price, sell_percent),
        }
        for p in page_rows
    ]

    return Response({
        "count": total,
        "page": page,
        "page_size": page_size,
        "has_more": start + page_size < total,
        "results": results,
    })
