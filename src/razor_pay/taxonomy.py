"""Razorpay failure-reason taxonomy.

Source: Razorpay's published UPI error codes and payment error-reason list.
This map is the backbone of the engine: the *reason* a payment failed determines
which intervention can possibly work. Blind retry loops ignore this, which is
exactly the naive baseline the harness measures against.

`retryable_same_instrument` answers: can re-presenting the identical charge on the
identical instrument ever succeed? For INSTRUMENT_INVALID it never can, so a retry
is pure cost with zero expected recovery.
"""

from __future__ import annotations

from dataclasses import dataclass

from razor_pay.schemas import RootCause


@dataclass(frozen=True)
class CauseProfile:
    root_cause: RootCause
    retryable_same_instrument: bool
    transient: bool
    note: str


# Razorpay error_code / error_reason -> cause profile.
ERROR_CODE_MAP: dict[str, CauseProfile] = {
    "insufficient_funds": CauseProfile(
        RootCause.INSUFFICIENT_FUNDS,
        retryable_same_instrument=True,
        transient=True,
        note="Balance may replenish; timing matters more than repetition.",
    ),
    "bank_technical_error": CauseProfile(
        RootCause.ISSUER_DOWNTIME,
        retryable_same_instrument=True,
        transient=True,
        note="Issuer-side downtime; backoff then retry.",
    ),
    "partner_bank_downtime": CauseProfile(
        RootCause.ISSUER_DOWNTIME,
        retryable_same_instrument=True,
        transient=True,
        note="Partner bank unavailable; alternate rail if available.",
    ),
    "gateway_technical_error": CauseProfile(
        RootCause.GATEWAY_ERROR,
        retryable_same_instrument=True,
        transient=True,
        note="Gateway-side fault; short backoff.",
    ),
    "server_error": CauseProfile(
        RootCause.GATEWAY_ERROR,
        retryable_same_instrument=True,
        transient=True,
        note="Generic upstream fault.",
    ),
    "invalid_vpa": CauseProfile(
        RootCause.INSTRUMENT_INVALID,
        retryable_same_instrument=False,
        transient=False,
        note="Customer is not a valid UPI user. Retrying can never succeed.",
    ),
    "vpa_resolution_failed": CauseProfile(
        RootCause.INSTRUMENT_INVALID,
        retryable_same_instrument=False,
        transient=False,
        note="UPI ID cannot be resolved. Needs a new instrument.",
    ),
    "card_expired": CauseProfile(
        RootCause.INSTRUMENT_INVALID,
        retryable_same_instrument=False,
        transient=False,
        note="Instrument is dead. Needs a new instrument.",
    ),
    "invalid_card": CauseProfile(
        RootCause.INSTRUMENT_INVALID,
        retryable_same_instrument=False,
        transient=False,
        note="Instrument unusable.",
    ),
    "payment_cancelled": CauseProfile(
        RootCause.CUSTOMER_CANCELLED,
        retryable_same_instrument=True,
        transient=False,
        note="Customer aborted. May signal genuine intent not to buy.",
    ),
    "payment_collect_request_expired": CauseProfile(
        RootCause.COLLECT_EXPIRED,
        retryable_same_instrument=True,
        transient=True,
        note="Customer did not act inside the collect window.",
    ),
    "payment_declined": CauseProfile(
        RootCause.DECLINED_UNSPECIFIED,
        retryable_same_instrument=True,
        transient=False,
        note="Debit refused without a specific reason.",
    ),
    "credit_failed": CauseProfile(
        RootCause.DECLINED_UNSPECIFIED,
        retryable_same_instrument=True,
        transient=False,
        note="Credit leg failed.",
    ),
    "customer_bank_account_mismatch": CauseProfile(
        RootCause.ACCOUNT_MISMATCH,
        retryable_same_instrument=False,
        transient=False,
        note="Different account than the one registered at mandate time.",
    ),
}

# First profile wins per cause, so the most specific note survives.
CAUSE_PROFILES: dict[RootCause, CauseProfile] = {}
for _profile in ERROR_CODE_MAP.values():
    CAUSE_PROFILES.setdefault(_profile.root_cause, _profile)
CAUSE_PROFILES[RootCause.ABANDONED_NO_ATTEMPT] = CauseProfile(
    RootCause.ABANDONED_NO_ATTEMPT,
    retryable_same_instrument=False,
    transient=False,
    note="No charge was ever attempted; there is nothing to retry.",
)
CAUSE_PROFILES[RootCause.UNKNOWN] = CauseProfile(
    RootCause.UNKNOWN,
    retryable_same_instrument=False,
    transient=False,
    note="Unclassified. Routes to the exception list rather than to an action.",
)


def lookup(error_code: str | None) -> CauseProfile | None:
    if not error_code:
        return None
    return ERROR_CODE_MAP.get(error_code.strip().lower())


def profile_for(cause: RootCause) -> CauseProfile:
    return CAUSE_PROFILES[cause]


def known_error_codes() -> list[str]:
    return sorted(ERROR_CODE_MAP)
