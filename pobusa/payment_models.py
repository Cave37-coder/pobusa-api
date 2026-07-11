# PoBuSA payment_models.py — v1.0.0
# Separate file, merge into models.py when ready. Tracks which payment
# scheme each Client has connected — PoBuSA never stores raw funds or acts
# as a payment intermediary. Credentials here point to the Client's OWN
# gateway account; nothing routes through Mike or PokeBulk SA's PayFast.

from django.db import models
from .models import Store


class PaymentProvider(models.Model):
    """One row per Client per connected payment scheme. A Client with no
    row here is just using cash/EFT recorded as metadata (current default —
    see Sale.payment_method). Adding a row here is what "connects" a real
    gateway later, entirely optional and entirely the Client's own account."""

    PROVIDER_CHOICES = [
        ("payfast", "PayFast"),
        ("yoco", "Yoco"),
        ("peach_payments", "Peach Payments"),
        ("ozow", "Ozow"),
        ("other", "Other"),
    ]

    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="payment_provider")
    provider_type = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    is_active = models.BooleanField(default=False)
    connected_at = models.DateTimeField(null=True, blank=True)

    # Credentials belong to the CLIENT's own gateway account, never Mike's.
    # Store these encrypted (e.g. via django-cryptography or a secrets
    # manager) — this plain CharField is a placeholder for the scaffold,
    # not how this should actually be stored once implemented for real.
    merchant_id = models.CharField(max_length=255, blank=True)
    api_key_encrypted = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return f"{self.store.name} — {self.get_provider_type_display()} ({'active' if self.is_active else 'inactive'})"
