# PoBuSA branding_serializers.py — v1.0.0

from rest_framework import serializers
from .models import Store


class StoreBrandingSerializer(serializers.ModelSerializer):
    """Only what a Client is allowed to configure about their own store's
    identity — never touches pricing, tiers, or anything from Section 6/7
    of the spec that's owner-only."""
    class Meta:
        model = Store
        fields = ["id", "name", "logo"]
        read_only_fields = ["id"]
