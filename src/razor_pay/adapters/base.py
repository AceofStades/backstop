"""Adapter plumbing shared by every leak type.

## What is real and what is injected

Seeding creates a **genuine Razorpay test-mode Order** per case, so every case
carries a real Razorpay entity id that a reviewer can look up in the dashboard.

The **failure reason** attached to that case is injected from a documented
distribution, because test mode cannot be made to produce a specific issuer
decline on demand. That injected value is stored as `injected_cause` and is used
for exactly one purpose: scoring the diagnoser afterwards. No component under
test ever reads it.

This split is stated plainly in the report rather than blurred.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Callable, Protocol

from razor_pay.schemas import LeakType, RecoveryCase


class Adapter(Protocol):
    leak_type: LeakType

    def seed(
        self,
        n: int,
        rng: random.Random,
        now: datetime,
        merchant_id: str,
        client: "SeedClient",
        token: str = "",
    ) -> list[RecoveryCase]: ...


def case_id(prefix: str, token: str, index: int) -> str:
    """Build a globally unique case id.

    Case ids must be unique across batches, not merely within one: the ledger
    joins on `case_id` alone, so a repeated id would make one batch's contacts
    count against another's customer budget. Without the token, a second
    `seed` collides on the primary key -- which is exactly what `run` tells you
    to do when it refuses to re-run a batch.
    """
    return f"{prefix}_{token}_{index:04d}" if token else f"{prefix}_{index:04d}"


class SeedClient:
    """Creates the real Razorpay artifact backing each seeded case."""

    def __init__(self, razorpay_client=None) -> None:
        self.client = razorpay_client
        self.real_calls = 0

    @property
    def is_real(self) -> bool:
        return self.client is not None

    def create_order(self, amount_paise: int, receipt: str, notes: dict) -> dict:
        if self.client is None:
            return {"id": f"sim_order_{abs(hash(receipt)) % 10**12:012d}", "simulated": True}
        try:
            self.real_calls += 1
            return dict(
                self.client.order.create(
                    {
                        "amount": amount_paise,
                        "currency": "INR",
                        "receipt": receipt[:40],
                        "notes": notes,
                    }
                )
            )
        except Exception as exc:
            return {"id": f"err_order_{abs(hash(receipt)) % 10**12:012d}", "error": str(exc)}


def sample_amount_paise(rng: random.Random) -> int:
    """Right-skewed ticket sizes, roughly Rs 150 - Rs 20,000."""
    rupees = min(20000, max(150, int(rng.lognormvariate(6.9, 0.85))))
    return rupees * 100


def sample_detected_at(rng: random.Random, now: datetime) -> datetime:
    """Spread detection across the previous 72 hours, including out-of-window hours.

    Deliberately includes night-time detections so the RBI contact-window gate is
    exercised by the batch rather than only by the scripted refusal demo.
    """
    return now - timedelta(minutes=rng.randint(0, 72 * 60))


# Real merchants have repeat customers, and a customer whose payment failed once
# is disproportionately likely to fail again -- same thin balance, same dead
# instrument, same flaky issuer. Drawing customer refs uniformly from a wide pool
# would make repeats vanish and would quietly hide the per-customer contact
# budget, which only bites when one person owns several failing cases.
CUSTOMER_POOL_SIZE = 120
REPEAT_CUSTOMER_SHARE = 0.35


def sample_customer_ref(rng: random.Random) -> str:
    """Draw a customer, with a heavy-tailed share of repeat offenders."""
    if rng.random() < REPEAT_CUSTOMER_SHARE:
        # The troubled tail: a small group generating a large share of failures.
        return f"cust_{rng.randint(1, CUSTOMER_POOL_SIZE // 6):04d}"
    return f"cust_{rng.randint(1, CUSTOMER_POOL_SIZE):04d}"


def weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


ADAPTERS: dict[LeakType, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    ADAPTERS[adapter.leak_type] = adapter
    return adapter


def get_adapter(leak_type: LeakType) -> Adapter:
    if leak_type not in ADAPTERS:
        raise KeyError(f"No adapter registered for {leak_type}")
    return ADAPTERS[leak_type]
