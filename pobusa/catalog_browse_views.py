# PoBuSA catalog_browse_views.py — v1.0.0
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
# price for those.

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
    accessory_types = [
        {"category_id": cat_id, "label": label}
        for cat_id, label in sorted(ACCESSORY_CATEGORY_LABELS.items(), key=lambda kv: kv[1])
    ]

    return Response({
        "product_types": [
            {"code": "single", "label": PRODUCT_TYPE_LABELS["single"], "games": games},
            {"code": "sealed", "label": PRODUCT_TYPE_LABELS["sealed"], "games": games},
            {"code": "accessory", "label": PRODUCT_TYPE_LABELS["accessory"], "accessory_types": accessory_types},
        ]
    })


@api_view(["GET"])
def sets_list(request):
    """GET /api/pobusa/catalog/sets/?product_type=single&game=3
    Distinct Set names for a given product_type + game, newest-first (per
    the release_date field's own docstring — this is exactly what it's
    for). Not applicable to accessories (they have no set_name)."""
    product_type = request.query_params.get("product_type")
    game_id = request.query_params.get("game")

    if product_type not in ("single", "sealed"):
        return Response({"error": "product_type must be 'single' or 'sealed' — accessories have no sets."}, status=status.HTTP_400_BAD_REQUEST)
    if not game_id:
        return Response({"error": "game is required"}, status=status.HTTP_400_BAD_REQUEST)

    rows = (
        CatalogProduct.objects
        .filter(product_type=product_type, game_id=game_id, is_active=True)
        .exclude(set_name="")
        .values("set_name")
        .annotate(latest_release=Max("release_date"))
        .order_by("-latest_release", "set_name")
    )
    return Response([{"set_name": r["set_name"], "release_date": r["latest_release"]} for r in rows])


@api_view(["GET"])
def browse_products(request, store_id):
    """GET /api/pobusa/stores/<store_id>/catalog/browse/
        ?product_type=single&game=3&set=Scarlet+%26+Violet&accessory_type=31&q=charizard&page=1&page_size=40

    Paginated product search/browse for the grid. Results are ordered by
    name/card_number/id so that different print variants of the same card
    (separate CatalogProduct rows — see TCGCSVSource's docstring) land
    adjacent to each other on the same page; the frontend groups those into
    a single tile and expands them in the variant panel on select."""
    try:
        store = Store.objects.get(pk=store_id)
    except Store.DoesNotExist:
        return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)

    product_type = request.query_params.get("product_type")
    if product_type not in ("single", "sealed", "accessory"):
        return Response({"error": "product_type must be one of: single, sealed, accessory"}, status=status.HTTP_400_BAD_REQUEST)

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

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = min(MAX_PAGE_SIZE, max(1, int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE

    qs = qs.select_related("game").order_by("name", "card_number", "id")
    total = qs.count()
    start = (page - 1) * page_size
    page_rows = qs[start:start + page_size]

    sell_percent = store.sell_percent_default

    def estimated_price(product):
        if product.market_price is None:
            return None
        return round(product.market_price * sell_percent / 100, 2)

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
            "estimated_sell_price": estimated_price(p),
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
