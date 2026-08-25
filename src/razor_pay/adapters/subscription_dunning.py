"""Leak B: a recurring auto-debit that bounced.

Shares the executor and the error taxonomy with payment failure, which is the
point -- adding a second leak should cost an adapter, not a second pipeline.

What differs is the decline mix and the decision axis. Mandate presentments skew
hard toward balance failures and registered-account mismatches, and the useful
lever is *when* to re-present rather than how often.
"""

from __future__ import annotations

import random
from datetime import datetime

from razor_pay import taxonomy
from razor_pay.adapters.base import (
    SeedClient,
    register,
    sample_amount_paise,
    sample_customer_ref,
    sample_detected_at,
    weighted_choice,
)
from razor_pay.schemas import FailureEvidence, LeakType, RecoveryCase, RootCause

ERROR_MIX: dict[str, float] = {
    "insufficient_funds": 0.46,
    "bank_technical_error": 0.16,
    "customer_bank_account_mismatch": 0.11,
    "payment_declined": 0.10,
    "gateway_technical_error": 0.06,
    "card_expired": 0.05,
    "invalid_vpa": 0.03,
    "payment_cancelled": 0.03,
}

UNCODED_RATE = 0.05

FREE_TEXT: list[tuple[str, RootCause]] = [
    ("mandate presentment returned without a status from the sponsor bank", RootCause.UNKNOWN),
    ("debit rejected, payer account frozen per sponsor bank note", RootCause.DECLINED_UNSPECIFIED),
    ("insufficient balance reported on presentment", RootCause.INSUFFICIENT_FUNDS),
]


class SubscriptionDunningAdapter:
    leak_type = LeakType.SUBSCRIPTION_DUNNING

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
            case_id = f"sd_{i:04d}"
            # Subscription tickets cluster tighter than one-off carts.
            amount = max(9900, min(sample_amount_paise(rng), 500000))
            order = client.create_order(
                amount, f"seed_{case_id}", {"case_id": case_id, "leak": self.leak_type.value}
            )

            if rng.random() < UNCODED_RATE:
                description, injected = rng.choice(FREE_TEXT)
                evidence = FailureEvidence(
                    error_code=None,
                    error_description=description,
                    error_source="bank",
                    error_step="mandate_presentment",
                    method="upi_autopay",
                )
            else:
                code = weighted_choice(rng, ERROR_MIX)
                profile = taxonomy.lookup(code)
                injected = profile.root_cause if profile else RootCause.UNKNOWN
                evidence = FailureEvidence(
                    error_code=code,
                    error_description=profile.note if profile else None,
                    error_reason=code,
                    error_source="bank",
                    error_step="mandate_presentment",
                    method="upi_autopay" if code != "card_expired" else "card",
                )

            cases.append(
                RecoveryCase(
                    case_id=case_id,
                    leak_type=self.leak_type,
                    merchant_id=merchant_id,
                    customer_ref=sample_customer_ref(rng),
                    amount_at_risk_paise=amount,
                    entity_refs={
                        "order_id": str(order.get("id", "")),
                        "subscription_id": f"sub_sim_{rng.randint(10**9, 10**10 - 1)}",
                        "cycle": str(rng.randint(2, 18)),
                    },
                    failure_evidence=evidence,
                    detected_at=sample_detected_at(rng, now),
                    injected_cause=injected,
                )
            )
        return cases


register(SubscriptionDunningAdapter())
