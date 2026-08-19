# PoBuSA catalog_browse_views.py — v1.6.1
# v1.6.1: browse_tree() now also proxies (via _remote_catalog_browse_tree)
# when CATALOG_SERVICE_BASE is set -- found missing during item 17's real
# two-server verification. Without it, a fresh Client's empty local Game
# table left the Sealed dropdown empty and Singles showing only Pokemon,
# even though sets_list/browse_products underneath were already proxying
# correctly. See that function's own docstring for the full story.
# v1.6.0 (Checklist Phase 4, item 16): sets_list/browse_products now call
# the item 15 proxy helpers when settings.CATALOG_SERVICE_BASE is set.
# Unset (the default) falls straight through to the exact same local-DB
# path every deployment has used since v1.0 -- zero behavior change for
# the live deployment, which doesn't set this env var.
# v1.5.0 (Checklist Phase 4, item 15): added _remote_catalog_sets_list and
# _remote_catalog_browse -- generic proxy helpers for calling another
# PoBuSA deployment's own catalog endpoints (settings.CATALOG_SERVICE_BASE),
# mirroring _pokemon_browse's "raw reference data in, store-specific
# numbers computed locally" split. Also factored the stock lookup shared
# by browse_products and the new proxy into _client_stock_map. NOT wired
# into sets_list/browse_products yet -- that's item 16, still pending.
# v1.4.1: fixed a bug caught from a live screenshot -- Pokemon's
# in_stock_only path was reporting pokemart's raw unfiltered count/
# has_more even after filtering results down to real PoBuSA stock,
# showing e.g. "36,799 results" over an empty grid. Now derived from the
# actually-filtered results instead. See _pokemon_browse's own comment.
# v1.4.0 (Checklist Phase 4, item 12): the TCGCSV-catalog side of
# ?in_stock_only=true now filters/reports stock via ClientCatalogStock
# (Phase 4, item 10) instead of CatalogProduct.stock_quantity, which is on
# its way out (item 13). Pokemon's CardStockLine path is unchanged.
# v1.3.0: browse_products now accepts ?in_stock_only=true. Business rule
# per Michael 2026-08-19: Buy-in and Inventory (admin) must always show
# every product; only the Sell screen restricts to what's actually in
# stock. Backed by CatalogProduct.stock_quantity (a new manual-for-now
# field, see its docstring in models.py) for the TCGCSV catalog. Pokemon
# singles are filtered against PoBuSA's OWN CardStockLine ledger, NEVER
# pokemart-api's stock/in_stock fields -- see the new project-level
# CLAUDE.md hard rule (2026-08-19): pokemart-api is read-only card info
# only, PoBuSA holds all of its own stock, including Pokemon's, in its own
# database. Results now carry stock_quantity so the frontend can show it.
# v1.2.0: Pokemon SEALED product now flows through the normal CatalogProduct
# path (real "pokemon" Game row, synced from TCGCSV via sync_tcgcsv.py's
# --sealed-only, since pokemart-api itself turned out to have no real sealed
# Pokemon data -- only digital "Code Card" inserts). Only Singles still use
# the game=-1 sentinel/pokemart-api proxy below. browse_tree() now excludes
# the real "pokemon" Game row from the Singles dropdown (avoids listing
# Pokemon twice) while keeping it in the Sealed dropdown.
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
from django.conf import settings
from django.db.models import Max, Sum

from .models import Game, CatalogProduct, Store, CardStockLine, ClientCatalogStock

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

# Checklist Phase 4, item 15 -- timeout for calls to another PoBuSA
# deployment's own catalog endpoints (settings.CATALOG_SERVICE_BASE).
CATALOG_SERVICE_TIMEOUT = 8


def _remote_catalog_browse_tree():
    """Proxies another PoBuSA deployment's own /catalog/browse-tree/
    endpoint verbatim -- found necessary during item 17's real two-server
    verification (2026-08-19): a fresh Client deployment's own local Game
    table is empty by design (it never runs seed_games/sync_tcgcsv -- that
    data belongs to the catalog authority), so without this, sets_list and
    browse_products would proxy correctly but the Sealed dropdown would
    render completely empty and Singles would show only the Pokemon
    sentinel entry -- everything downstream working, but unreachable from
    the UI. No translation needed: it's the exact same response shape,
    already correctly excludes/includes Pokemon per game/product_type on
    the authority's side. Checklist Phase 4, item 16 addendum."""
    try:
        resp = requests.get(
            f"{settings.CATALOG_SERVICE_BASE}/catalog/browse-tree/", timeout=CATALOG_SERVICE_TIMEOUT
        )
        resp.raise_for_status()
        return Response(resp.json())
    except requests.RequestException:
        return Response({"error": "Catalog service unreachable"}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(["GET"])
def browse_tree(request):
    """GET /api/pobusa/catalog/browse-tree/
    First two levels of the nested dropdown in one small, cacheable payload:
    Product Type -> Games (for single/sealed) or Accessory Types (for
    accessory). The third level (Set) is fetched separately via sets_list
    once a specific game/accessory type is picked -- Set lists can be long,
    no reason to ship all of them up front."""
    if settings.CATALOG_SERVICE_BASE:
        # Checklist Phase 4, item 16 addendum -- see _remote_catalog_browse_tree's
        # own docstring for why this exists. Unset (the default) falls
        # straight through to the local query below, unchanged.
        return _remote_catalog_browse_tree()

    games = [
        {"id": g.id, "code": g.code, "name": g.name}
        for g in Game.objects.filter(is_active=True).order_by("sort_order", "name")
    ]
    # Pokemon goes first -- it's the shop's flagship game, and for Singles
    # it's served via the sentinel/pokemart-api proxy, never CatalogProduct.
    # `games` (above) now also contains a REAL "pokemon" Game DB row
    # (2026-08-17, seed_games.py) -- but that row exists only for Sealed
    # product synced from TCGCSV (see sync_tcgcsv.py's --sealed-only note),
    # so it's excluded here to avoid listing Pokemon twice in the Singles
    # dropdown. It stays in `games` as-is for the Sealed dropdown below.
    singles_games = (
        [{"id": POKEMON_GAME_ID, "code": "pokemon", "name": POKEMON_GAME_NAME}]
        + [g for g in games if g["code"] != "pokemon"]
    )
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
            # Sealed Pokemon never comes through the sentinel/pokemart-api
            # proxy -- it's synced into CatalogProduct under the real
            # "pokemon" Game row instead (sync_tcgcsv.py --sealed-only,
            # 2026-08-17), so the frontend should never actually send
            # game=-1 with product_type=sealed. Empty result rather than an
            # error either way.
            return Response([])
        return _pokemon_sets_list()

    if settings.CATALOG_SERVICE_BASE:
        # Checklist Phase 4, item 16 -- this deployment doesn't hold the
        # catalog itself, proxy out to the one that does. Unset (the
        # default) falls straight through to the local query below,
        # unchanged from every deployment's behavior before this item.
        return _remote_catalog_sets_list(product_type, game_id)

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


def _pokemon_browse(store, product_type, set_name, query, page, page_size, in_stock_only=False):
    """Proxies pokemart-api's /api/products/ into the same browse-result
    shape CatalogProduct rows return, so the frontend's ProductGrid/
    VariantPanel never need to know or care which source answered --
    exactly the same "invisible above this layer" principle services.py's
    search_cards() already established for the buy-in side."""
    empty = {"count": 0, "page": page, "page_size": page_size, "has_more": False, "results": []}
    if product_type != "single":
        # Sealed Pokemon lives in CatalogProduct under the real "pokemon"
        # Game row (synced from TCGCSV, --sealed-only) -- not behind this
        # sentinel/pokemart-api proxy. See sets_list's matching comment.
        return Response(empty)

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
            # Deliberately NOT p.get("stock")/p.get("in_stock") -- see
            # CLAUDE.md hard rule (2026-08-19): pokemart-api is read-only
            # card info only, never a source of truth for a PoBuSA store's
            # own stock. Filled in below from PoBuSA's own CardStockLine
            # ledger instead.
            "stock_quantity": 0,
        })

    # PoBuSA's own stock, never pokemart-api's. CardStockLine.card_id
    # already matches this SKU scheme -- the existing Buy-in flow writes
    # rows here via pokemart-api's card_lookup/search (info only), so this
    # just reads the ledger that's always been this store's real Pokemon
    # stock. See CLAUDE.md hard rule (2026-08-19).
    skus = [r["sku"] for r in results if r["sku"]]
    stock_by_sku = dict(
        CardStockLine.objects
        .filter(invoice__store=store, status="in_stock", card_id__in=skus)
        .values("card_id")
        .annotate(total=Sum("quantity"))
        .values_list("card_id", "total")
    )
    for r in results:
        r["stock_quantity"] = stock_by_sku.get(r["sku"], 0)

    if in_stock_only:
        # Filtered AFTER pokemart's own pagination, since pokemart has no
        # concept of PoBuSA's stock to filter on server-side -- so a page
        # can come back with fewer than page_size items. Report count/
        # has_more off the FILTERED results, not pokemart's raw totals --
        # caught 2026-08-19 from a live screenshot showing "36,799 results"
        # over an empty "no products match" grid with a "Load more" that
        # would only ever load more empty pages. has_more is conservatively
        # False here (there could technically be in-stock items further
        # into pokemart's unfiltered pages, but we'd have to walk them to
        # know) -- acceptable for now since CardStockLine has near-zero
        # real Pokemon stock in it yet anyway; revisit once the real buy-in
        # flow (Checklist Phase 5, item 23) has actually populated it.
        results = [r for r in results if r["stock_quantity"] > 0]
        count = len(results)
        has_more = False
    else:
        count = data.get("count", len(results))
        has_more = data.get("next") is not None

    return Response({
        "count": count,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "results": results,
    })


def _client_stock_map(store, skus):
    """Batched ClientCatalogStock lookup for a list of skus -- PoBuSA's own
    real per-Client stock, never a remote source's. Shared by browse_products
    (local catalog) and _remote_catalog_browse (Checklist Phase 4, item 15)
    so both compute stock the exact same way."""
    if not skus:
        return {}
    return dict(
        ClientCatalogStock.objects.filter(store=store, sku__in=skus).values_list("sku", "quantity")
    )


def _remote_catalog_sets_list(product_type, game_id):
    """Proxies another PoBuSA deployment's own /catalog/sets/ endpoint.
    That endpoint is already store-agnostic reference data (see sets_list's
    own docstring/SECURITY NOTE above), so the response is used exactly
    as-is -- no translation needed, unlike the Pokemon/pokemart-api case.
    Checklist Phase 4, item 15. Not wired into sets_list yet -- that's
    item 16."""
    try:
        resp = requests.get(
            f"{settings.CATALOG_SERVICE_BASE}/catalog/sets/",
            params={"product_type": product_type, "game": game_id},
            timeout=CATALOG_SERVICE_TIMEOUT,
        )
        resp.raise_for_status()
        return Response(resp.json())
    except requests.RequestException:
        return Response({"error": "Catalog service unreachable"}, status=status.HTTP_502_BAD_GATEWAY)


def _remote_catalog_browse(store, product_type, game_id, set_name, accessory_type, query, page, page_size, in_stock_only=False):
    """Proxies another PoBuSA deployment's own /stores/<id>/catalog/browse/
    endpoint to fetch the shared reference catalog (name, set, image,
    market_price) -- then discards THAT deployment's own store-specific
    estimated_sell_price/stock_quantity entirely and recomputes both using
    THIS (calling) store's own sell_percent_default and ClientCatalogStock.
    Exactly the same "raw reference data in, store-specific numbers
    computed locally" split _pokemon_browse already uses for pokemart-api.
    settings.CATALOG_SERVICE_STORE_ID is only ever used to satisfy that
    remote endpoint's URL shape -- see its own docstring in settings.py for
    why the specific value doesn't matter. Checklist Phase 4, item 15. Not
    wired into browse_products yet -- that's item 16."""
    empty = {"count": 0, "page": page, "page_size": page_size, "has_more": False, "results": []}

    params = {"product_type": product_type, "page": page, "page_size": page_size}
    if game_id:
        params["game"] = game_id
    if set_name:
        params["set"] = set_name
    if accessory_type:
        params["accessory_type"] = accessory_type
    if query:
        params["q"] = query
    # Deliberately NOT in_stock_only -- the catalog authority's own stock
    # is meaningless to us. Always fetch the full unfiltered page and
    # filter locally, same reasoning as CATALOG_SERVICE_STORE_ID above.

    try:
        resp = requests.get(
            f"{settings.CATALOG_SERVICE_BASE}/stores/{settings.CATALOG_SERVICE_STORE_ID}/catalog/browse/",
            params=params, timeout=CATALOG_SERVICE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return Response({**empty, "error": "Catalog service unreachable"})

    sell_percent = store.sell_percent_default
    remote_results = data.get("results", [])
    stock_by_sku = _client_stock_map(store, [r["sku"] for r in remote_results if r.get("sku")])

    results = [
        {
            **r,
            "estimated_sell_price": _estimate_from_market_ref(r.get("market_price"), sell_percent),
            "stock_quantity": stock_by_sku.get(r.get("sku"), 0),
        }
        for r in remote_results
    ]

    if in_stock_only:
        # Same caveat as _pokemon_browse's in_stock_only handling -- filtered
        # after the remote's own pagination, so count/has_more reflect the
        # filtered page, not the remote's unfiltered totals.
        results = [r for r in results if r["stock_quantity"] > 0]
        count = len(results)
        has_more = False
    else:
        count = data.get("count", len(results))
        has_more = data.get("has_more", False)

    return Response({
        "count": count,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
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

    # Business rule, per Michael 2026-08-19: Buy-in and Inventory (admin)
    # always show every product; only the Sell screen restricts to what's
    # actually in stock. Left opt-in (default False) rather than baked
    # into this endpoint's default behaviour, since Buy-in's own browse UI
    # (Checklist Phase 5, item 23, not built yet) will very likely reuse
    # this same endpoint and must keep seeing everything.
    in_stock_only = request.query_params.get("in_stock_only") == "true"

    if request.query_params.get("game") == str(POKEMON_GAME_ID):
        return _pokemon_browse(
            store, product_type,
            request.query_params.get("set"),
            request.query_params.get("q", "").strip(),
            page, page_size,
            in_stock_only=in_stock_only,
        )

    if settings.CATALOG_SERVICE_BASE:
        # Checklist Phase 4, item 16 -- this deployment doesn't hold the
        # catalog itself, proxy out to the one that does instead of
        # querying our own (deliberately empty) local CatalogProduct
        # table. Unset (the default) falls straight through to the local
        # path below, unchanged from every deployment's behavior before
        # this item.
        return _remote_catalog_browse(
            store, product_type,
            request.query_params.get("game"),
            request.query_params.get("set"),
            request.query_params.get("accessory_type"),
            request.query_params.get("q", "").strip(),
            page, page_size,
            in_stock_only=in_stock_only,
        )

    qs = CatalogProduct.objects.filter(product_type=product_type, is_active=True)
    if in_stock_only:
        # Checklist Phase 4, item 12: filters against the real per-Client
        # ClientCatalogStock table now, not the deprecated
        # CatalogProduct.stock_quantity bridge (Phase 3 item 9 / Phase 4
        # item 13). Subquery, not a Python-side set -- stays a single SQL
        # query no matter how big the catalog or the stock table gets. See
        # ClientCatalogStock's own docstring for why this is a sku-string
        # join rather than a real FK (survives the catalog split too).
        in_stock_skus = ClientCatalogStock.objects.filter(store=store, quantity__gt=0).values("sku")
        qs = qs.filter(sku__in=in_stock_skus)

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

    # Real per-Client stock for just this page's rows, from
    # ClientCatalogStock (Checklist Phase 4, item 12) -- not
    # CatalogProduct.stock_quantity, which is on its way out (item 13).
    stock_by_sku = _client_stock_map(store, [p.sku for p in page_rows])

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
            "stock_quantity": stock_by_sku.get(p.sku, 0),
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
