"""Leak adapters. Each emits uniform `RecoveryCase` objects for one loss class."""

from razor_pay.adapters.base import ADAPTERS, SeedClient, get_adapter

# Import for registration side effects.
from razor_pay.adapters import (  # noqa: F401,E402
    checkout_abandonment,
    payment_failure,
    subscription_dunning,
)

__all__ = ["ADAPTERS", "SeedClient", "get_adapter"]
