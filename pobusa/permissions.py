# PoBuSA permissions.py — v1.0.0
# Enforces Section 7 of the spec: a Client's staff can only ever touch their
# own Store's data. Mike (superuser) bypasses this via Django admin, which
# isn't gated by this permission class at all — this only applies to the
# API views, which is what a Client's own front-end would call.

from rest_framework.permissions import BasePermission
from .models import StoreStaff


class IsStoreMember(BasePermission):
    """Requires request.user to be linked to a Store via StoreStaff.
    Use alongside a store_id in the URL or request data — the view is
    responsible for checking has_store_access() against that specific store."""
    message = "You don't have access to this store's data."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return StoreStaff.objects.filter(user=request.user).exists()


def get_staff_store(request):
    """Returns the Store the current user is linked to, or None.
    Call this in a view to scope querysets, e.g.:
        store = get_staff_store(request)
        CardStockLine.objects.filter(invoice__store=store)"""
    try:
        return request.user.pobusa_staff.store
    except (AttributeError, StoreStaff.DoesNotExist):
        return None


def has_store_access(request, store_id) -> bool:
    """Confirms the current user's linked store matches the store_id being
    requested — call this at the top of any view that takes a store_id,
    even if IsStoreMember already ran, since that only confirms SOME store
    access, not access to this specific one."""
    store = get_staff_store(request)
    return store is not None and store.id == int(store_id)
