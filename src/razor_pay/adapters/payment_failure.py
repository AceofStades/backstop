"""Leak A: a one-off payment that failed at the last step.

Failure-reason mix reflects the published Indian UPI/card decline pattern, where
balance and issuer-side faults dominate and dead instruments are a small but
expensive tail. Weights are an explicit modelling assumption, stated in the
report and adjustable via `--mix`.
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
    "insufficient_funds": 0.28,
    "bank_technical_error": 0.22,
    "payment_collect_request_expired": 0.12,
    "payment_cancelled": 0.12,
    "payment_declined": 0.10,
    "invalid_vpa": 0.06,
    "gateway_technical_error": 0.04,
    "vpa_resolution_failed": 0.03,
    "customer_bank_account_mismatch": 0.02,
    "card_expired": 0.01,
}

# A slice of cases arrive with no machine-readable code, only operator free text.
# These exist to exercise the LLM fallback and to generate honest exceptions.
UNCODED_RATE = 0.08

FREE_TEXT: list[tuple[str, RootCause]] = [
    ("payer bank returned a generic failure, no reason supplied", RootCause.UNKNOWN),
    ("customer says the app closed before he could approve", RootCause.CUSTOMER_CANCELLED),
    ("balance was short at the time of debit as per payer PSP", RootCause.INSUFFICIENT_FUNDS),
    ("remitter bank unreachable during the attempt", RootCause.ISSUER_DOWNTIME),
    ("handle could not be looked up on the mapper", RootCause.INSTRUMENT_INVALID),
    ("timed out waiting for payer action", RootCause.COLLECT_EXPIRED),
]

METHODS = ["upi", "upi", "upi", "card", "netbanking"]


class PaymentFailureAdapter:
    leak_type = LeakType.PAYMENT_FAILURE

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
            case_id = f"pf_{i:04d}"
            amount = sample_amount_paise(rng)
            order = client.create_order(
                amount, f"seed_{case_id}", {"case_id": case_id, "leak": self.leak_type.value}
            )

            if rng.random() < UNCODED_RATE:
                description, injected = rng.choice(FREE_TEXT)
                evidence = FailureEvidence(
                    error_code=None,
                    error_description=description,
                    error_source="bank",
                    error_step="payment_authorization",
                    method=rng.choice(METHODS),
                )
            else:
                code = weighted_choice(rng, ERROR_MIX)
                profile = taxonomy.lookup(code)
                injected = profile.root_cause if profile else RootCause.UNKNOWN
                evidence = FailureEvidence(
                    error_code=code,
                    error_description=profile.note if profile else None,
                    error_reason=code,
                    error_source="bank" if "bank" in code else "gateway",
                    error_step="payment_authorization",
                    method=rng.choice(METHODS),
                )

            cases.append(
                RecoveryCase(
                    case_id=case_id,
                    leak_type=self.leak_type,
                    merchant_id=merchant_id,
                    customer_ref=sample_customer_ref(rng),
                    amount_at_risk_paise=amount,
                    entity_refs={"order_id": str(order.get("id", ""))},
                    failure_evidence=evidence,
                    detected_at=sample_detected_at(rng, now),
                    injected_cause=injected,
                )
            )
        return cases


register(PaymentFailureAdapter())
