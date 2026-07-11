# PoBuSA branding_views.py — v1.2.0
# v1.2.0: fixed logo URL bug — the serializer wasn't given request context,
# so DRF returned the logo as a relative path (/media/store_logos/...)
# instead of a full URL. The browser then tried loading it from the
# frontend's own origin (localhost:3000) instead of the API's
# (localhost:8001), where the file actually lives — always failed.
# v1.1.0: TEMPORARILY removed the IsStoreMember permission gate for local
# testing — no login flow/frontend auth exists yet, so every request was
# being rejected before it could even use has_store_access(). This makes
# the endpoint open to anyone who can reach the API, which is fine while
# it's just you testing locally, but MUST be re-secured (proper auth login
# flow + re-add IsStoreMember) before this is ever exposed to a real Client
# or the public internet.

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Store
from .branding_serializers import StoreBrandingSerializer


@api_view(["GET", "PATCH"])
def store_branding(request, store_id):
    """GET /api/pobusa/stores/<store_id>/branding/  — view current branding
    PATCH /api/pobusa/stores/<store_id>/branding/ — update name/logo

    SECURITY NOTE: no permission check right now — see module docstring."""
    try:
        store = Store.objects.get(pk=store_id)
    except Store.DoesNotExist:
        return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(StoreBrandingSerializer(store, context={"request": request}).data)

    serializer = StoreBrandingSerializer(store, data=request.data, partial=True, context={"request": request})
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)
