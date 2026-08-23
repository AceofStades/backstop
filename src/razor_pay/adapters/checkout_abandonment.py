"""Leak C: a cart that never became a payment attempt.

Structurally different from the other two: there is no decline to interpret,
because no charge was ever presented. Detection is the *absence* of a capture
after a dwell threshold. The executor is reused unchanged -- the intervention is
a payment link, which is exactly what the other ladders escalate to.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from razor_pay.adapters.base import (
    SeedClient,
    register,
    sample_amount_paise,
    sample_detected_at,
)
from razor_pay.schemas import FailureEvidence, LeakType, RecoveryCase, RootCause

# An order with no capture after this long is treated as abandoned.
DWELL_THRESHOLD_MINUTES = 30


class CheckoutAbandonmentAdapter:
    leak_type = LeakType.CHECKOUT_ABANDONMENT

    def seed(
        self,
        n: int,
        rng: random.Random,
        now: datetime,
        merchant_id: str,
        client: SeedClient,
    ) -> list[RecoveryCase]:
        cases: list[RecoveryCase] = []
        for i in range(n):
            case_id = f"ca_{i:04d}"
            amount = sample_amount_paise(rng)
            order = client.create_order(
                amount, f"seed_{case_id}", {"case_id": case_id, "leak": self.leak_type.value}
            )
            created = sample_detected_at(rng, now)
            dwell = rng.randint(DWELL_THRESHOLD_MINUTES, 240)

            cases.append(
                RecoveryCase(
                    case_id=case_id,
                    leak_type=self.leak_type,
                    merchant_id=merchant_id,
                    customer_ref=f"cust_{rng.randint(1000, 9999)}",
                    amount_at_risk_paise=amount,
                    entity_refs={
                        "order_id": str(order.get("id", "")),
                        "dwell_minutes": str(dwell),
                    },
                    # No error code by construction: nothing was ever attempted.
                    failure_evidence=FailureEvidence(
                        error_description=(
                            f"Order created {dwell} minutes ago with no payment "
                            f"attempt recorded."
                        ),
                        error_step="checkout",
                    ),
                    detected_at=created + timedelta(minutes=dwell),
                    injected_cause=RootCause.ABANDONED_NO_ATTEMPT,
                )
            )
        return cases


register(CheckoutAbandonmentAdapter())
