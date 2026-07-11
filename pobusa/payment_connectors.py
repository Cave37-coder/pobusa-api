# PoBuSA payment_connectors.py — v1.0.0
# Abstract interface for payment schemes. Each real provider (PayFast,
# Yoco, etc.) implements this against the CLIENT's own gateway credentials
# — PoBuSA orchestrates the call, but the money moves directly between the
# Customer and the Client's own merchant account. PoBuSA is never in that
# money path, never holds funds, never sees them settle.
#
# Nothing here is wired up yet — this is the scaffold. Implementing a real
# provider means writing one subclass and registering it below; no other
# code in the app needs to change.

from abc import ABC, abstractmethod
from decimal import Decimal


class PaymentInitiationResult:
    def __init__(self, success: bool, redirect_url: str = None, reference: str = None, error: str = None):
        self.success = success
        self.redirect_url = redirect_url  # where to send the Customer to pay, if applicable
        self.reference = reference        # provider's transaction reference
        self.error = error


class PaymentConnector(ABC):
    """Base class every real payment scheme integration implements.
    Constructed with a PaymentProvider instance (the Client's own connected
    account) — never with any of Mike's/PokeBulk SA's own credentials."""

    def __init__(self, provider):
        self.provider = provider  # a PaymentProvider instance, scoped to one Client's Store

    @abstractmethod
    def initiate_payment(self, amount: Decimal, reference: str) -> PaymentInitiationResult:
        """Starts a payment for the given amount, tied to a Sale/reference.
        Returns a redirect URL (for hosted checkout flows) or reference for
        polling, depending on the provider's flow."""
        raise NotImplementedError

    @abstractmethod
    def verify_payment(self, reference: str) -> bool:
        """Confirms whether a given transaction reference actually settled.
        Called from a webhook handler or manual confirmation step."""
        raise NotImplementedError


class UnimplementedConnector(PaymentConnector):
    """Placeholder returned for any provider_type without a real
    implementation yet. Fails loudly rather than pretending to work."""

    def initiate_payment(self, amount, reference):
        return PaymentInitiationResult(success=False, error=f"{self.provider.provider_type} not yet implemented")

    def verify_payment(self, reference):
        raise NotImplementedError(f"{self.provider.provider_type} not yet implemented")


# Registry: provider_type -> connector class. Add a real entry here once a
# provider is actually built, e.g.:
#   from .connectors.payfast_connector import PayFastConnector
#   CONNECTOR_REGISTRY["payfast"] = PayFastConnector
CONNECTOR_REGISTRY = {}


def get_connector(provider) -> PaymentConnector:
    """Returns the right connector instance for a given PaymentProvider row.
    Falls back to UnimplementedConnector if that scheme isn't built yet."""
    connector_class = CONNECTOR_REGISTRY.get(provider.provider_type, UnimplementedConnector)
    return connector_class(provider)
