"""Modelled customer response.

**This is the one part of the pipeline that is not real, and the report says so.**
Razorpay test mode will happily create Orders and Payment Links, but it will not
produce a synthetic human who decides whether to pay. That decision is drawn here
from an explicit parameter set.

Two properties make the resulting numbers defensible rather than decorative:

1. **The control arm has a non-zero baseline.** Real customers fix their own
   failed payments all the time -- they top up and retry, or the issuer recovers.
   A control group that recovers 0% by construction makes any uplift look
   spectacular and means nothing. Baselines below are per root cause.

2. **Common random numbers.** A case's self-recovery draw is seeded from its
   case_id, so the same case would make the same self-recovery decision in either
   arm. Assignment therefore cannot leak into the outcome, and the measured
   difference is attributable to the intervention alone.

A treated case recovers if EITHER it would have self-recovered anyway OR one of
its interventions lands. That construction is what makes "incremental" the
honest word for the headline number.
"""

from __future__ import annotations

import hashlib
import random

from razor_pay.schemas import InterventionType, RootCause

# P(customer resolves it themselves inside the observation window), no contact.
BASELINE_SELF_RECOVERY: dict[RootCause, float] = {
    RootCause.INSUFFICIENT_FUNDS: 0.22,
    RootCause.ISSUER_DOWNTIME: 0.35,
    RootCause.GATEWAY_ERROR: 0.30,
    RootCause.INSTRUMENT_INVALID: 0.05,
    RootCause.CUSTOMER_CANCELLED: 0.08,
    RootCause.COLLECT_EXPIRED: 0.25,
    RootCause.ACCOUNT_MISMATCH: 0.06,
    RootCause.DECLINED_UNSPECIFIED: 0.15,
    RootCause.ABANDONED_NO_ATTEMPT: 0.12,
    RootCause.UNKNOWN: 0.15,
}

# Incremental P(recovery) contributed by a well-matched intervention.
# Deliberately near zero where the intervention cannot physically work -- that
# asymmetry is what the cause-aware policy engine exists to exploit.
UPLIFT: dict[tuple[RootCause, InterventionType], float] = {
    (RootCause.INSUFFICIENT_FUNDS, InterventionType.RETRY_PAYDAY_WINDOW): 0.24,
    (RootCause.INSUFFICIENT_FUNDS, InterventionType.RETRY_BACKOFF): 0.03,
    (RootCause.INSUFFICIENT_FUNDS, InterventionType.RETRY_NOW): 0.02,
    (RootCause.INSUFFICIENT_FUNDS, InterventionType.SEND_PAYMENT_LINK): 0.12,
    (RootCause.ISSUER_DOWNTIME, InterventionType.RETRY_BACKOFF): 0.30,
    (RootCause.ISSUER_DOWNTIME, InterventionType.RETRY_NOW): 0.10,
    (RootCause.GATEWAY_ERROR, InterventionType.RETRY_BACKOFF): 0.28,
    # Structurally impossible: a dead instrument cannot be charged again.
    (RootCause.INSTRUMENT_INVALID, InterventionType.RETRY_BACKOFF): 0.0,
    (RootCause.INSTRUMENT_INVALID, InterventionType.RETRY_NOW): 0.0,
    (RootCause.INSTRUMENT_INVALID, InterventionType.SEND_INSTRUMENT_UPDATE_LINK): 0.26,
    (RootCause.ACCOUNT_MISMATCH, InterventionType.SEND_INSTRUMENT_UPDATE_LINK): 0.20,
    (RootCause.CUSTOMER_CANCELLED, InterventionType.SOFT_NUDGE): 0.07,
    (RootCause.COLLECT_EXPIRED, InterventionType.RE_PRESENT_COLLECT): 0.22,
    (RootCause.COLLECT_EXPIRED, InterventionType.SEND_PAYMENT_LINK): 0.16,
    (RootCause.DECLINED_UNSPECIFIED, InterventionType.RETRY_BACKOFF): 0.08,
    (RootCause.DECLINED_UNSPECIFIED, InterventionType.SEND_PAYMENT_LINK): 0.13,
    (RootCause.ABANDONED_NO_ATTEMPT, InterventionType.SEND_PAYMENT_LINK): 0.18,
}

# Each further attempt on the same case is worth less than the one before.
ATTEMPT_DECAY = 0.6


class ResponseModel:
    def __init__(self, baseline_scale: float = 1.0, seed: int = 0) -> None:
        # `baseline_scale` drives the sensitivity analysis: the headline lift is
        # reported across a range of assumed baselines, not at a single point.
        self.baseline_scale = baseline_scale
        self.seed = seed

    def baseline_for(self, cause: RootCause) -> float:
        raw = BASELINE_SELF_RECOVERY.get(cause, 0.15) * self.baseline_scale
        return max(0.0, min(raw, 0.95))

    def _rng(self, case_id: str, salt: str) -> random.Random:
        digest = hashlib.sha256(f"{self.seed}:{case_id}:{salt}".encode()).hexdigest()
        return random.Random(int(digest[:16], 16))

    def would_self_recover(self, case_id: str, true_cause: RootCause) -> bool:
        """Identical draw in either arm -- see common random numbers, above."""
        return self._rng(case_id, "self").random() < self.baseline_for(true_cause)

    def intervention_lands(
        self,
        case_id: str,
        true_cause: RootCause,
        intervention: InterventionType,
        attempt_index: int,
    ) -> bool:
        base_uplift = UPLIFT.get((true_cause, intervention), 0.01)
        effective = base_uplift * (ATTEMPT_DECAY**attempt_index)
        return self._rng(case_id, f"att{attempt_index}").random() < effective

    def describe(self) -> dict:
        return {
            "baseline_scale": self.baseline_scale,
            "attempt_decay": ATTEMPT_DECAY,
            "seed": self.seed,
            "note": (
                "Customer response is MODELLED, not observed. Razorpay API "
                "artifacts (orders, payment links) are real test-mode entities."
            ),
        }
