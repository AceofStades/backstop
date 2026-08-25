"""Action costs and the engine's *believed* uplift, for expected-value stopping.

## Why this table is separate from the harness's

`harness/response_model.py` holds the UPLIFT table that decides whether an
intervention actually lands. That table is the simulated world's truth.

This module holds what the *engine believes* about the same question. The two are
deliberately separate objects:

  - If the engine read the harness's table it would have perfect knowledge of
    customer behaviour, and any expected-value stopping rule built on it would be
    measuring the simulation rather than the policy.
  - A real engine only ever has a prior, estimated from historical data and wrong
    at the edges. Keeping a separate table models that honestly, and lets the two
    disagree.

`test_engine_belief_is_not_harness_truth` asserts they remain distinct objects, so
a future refactor cannot collapse them into an import.

## Costs

Costs are in paise and represent what firing an action *costs the merchant*, not
what Razorpay charges. A silent retry is nearly free. A WhatsApp message costs a
fraction of a rupee to send and a quantity of customer patience that is real but
not denominated in rupees -- the contact costs below are deliberately larger than
the send price to stand in for that.
"""

from __future__ import annotations

from razor_pay.schemas import Channel, InterventionType, RootCause

# Cost of firing one action, in paise.
CHANNEL_COST_PAISE: dict[Channel, int] = {
    Channel.NONE: 50,        # a rail-side retry: an API call, no customer touched
    Channel.EMAIL: 200,
    Channel.SMS: 1500,
    Channel.WHATSAPP: 2500,  # highest, because an unwanted message costs goodwill
}

# What the engine believes about P(recovery | cause, intervention).
# An estimate, not a measurement. Intentionally coarser than the harness's truth.
BELIEVED_UPLIFT: dict[tuple[RootCause, InterventionType], float] = {
    (RootCause.INSUFFICIENT_FUNDS, InterventionType.RETRY_PAYDAY_WINDOW): 0.20,
    (RootCause.INSUFFICIENT_FUNDS, InterventionType.RETRY_BACKOFF): 0.04,
    (RootCause.INSUFFICIENT_FUNDS, InterventionType.SEND_PAYMENT_LINK): 0.10,
    (RootCause.ISSUER_DOWNTIME, InterventionType.RETRY_BACKOFF): 0.25,
    (RootCause.GATEWAY_ERROR, InterventionType.RETRY_BACKOFF): 0.25,
    (RootCause.INSTRUMENT_INVALID, InterventionType.SEND_INSTRUMENT_UPDATE_LINK): 0.22,
    (RootCause.ACCOUNT_MISMATCH, InterventionType.SEND_INSTRUMENT_UPDATE_LINK): 0.18,
    (RootCause.CUSTOMER_CANCELLED, InterventionType.SOFT_NUDGE): 0.06,
    (RootCause.COLLECT_EXPIRED, InterventionType.RE_PRESENT_COLLECT): 0.20,
    (RootCause.COLLECT_EXPIRED, InterventionType.SEND_PAYMENT_LINK): 0.14,
    (RootCause.DECLINED_UNSPECIFIED, InterventionType.RETRY_BACKOFF): 0.07,
    (RootCause.DECLINED_UNSPECIFIED, InterventionType.SEND_PAYMENT_LINK): 0.11,
    (RootCause.ABANDONED_NO_ATTEMPT, InterventionType.SEND_PAYMENT_LINK): 0.15,
}

# Belief about how much a repeat attempt is worth versus the first.
BELIEVED_ATTEMPT_DECAY = 0.55

# Fire an action only where expected recovery at least covers its own cost.
#
# This is deliberately set at break-even (1.0) rather than at a "clearly
# worthwhile" margin. An earlier version used 2.0, on the intuition that a
# stricter filter would improve efficiency. It did improve the actions-per-
# recovery ratio, from 6.60 to 6.31 -- and it destroyed Rs 2,522 of net value,
# because at these cost levels a forgone recovery is worth far more than the
# action it saves. See docs/05-worklog.md.
#
# The lesson generalises: a ratio improves when you cut its denominator, which
# is not the same as making money. The threshold belongs at break-even, where it
# encodes a principle (never fire an action that loses money in expectation)
# rather than a tuning parameter.
MIN_EXPECTED_VALUE_RATIO = 1.0

# A ticket floor for contacting customers was tested and removed: it cost net
# value at every level tried and never paid for itself. Kept at zero rather
# than deleted so the sweep in docs/05-worklog.md remains reproducible.
MIN_TICKET_FOR_CONTACT_PAISE = 0


def action_cost_paise(channel: Channel) -> int:
    return CHANNEL_COST_PAISE.get(channel, 100)


def believed_uplift(
    cause: RootCause, intervention: InterventionType, attempt_index: int
) -> float:
    """Engine's estimate of P(this action recovers the money)."""
    base = BELIEVED_UPLIFT.get((cause, intervention), 0.01)
    return base * (BELIEVED_ATTEMPT_DECAY**attempt_index)


def expected_value_paise(
    amount_at_risk_paise: int,
    cause: RootCause,
    intervention: InterventionType,
    channel: Channel,
    attempt_index: int,
) -> tuple[int, int, float]:
    """Return (expected_gain, cost, ratio) for one candidate action."""
    uplift = believed_uplift(cause, intervention, attempt_index)
    gain = int(amount_at_risk_paise * uplift)
    cost = action_cost_paise(channel)
    ratio = gain / cost if cost else float("inf")
    return gain, cost, ratio


def is_worth_firing(
    amount_at_risk_paise: int,
    cause: RootCause,
    intervention: InterventionType,
    channel: Channel,
    attempt_index: int,
) -> tuple[bool, str]:
    """Decide whether a candidate action clears its own cost.

    Returns (worth_it, reason). The reason is recorded in the ledger either way,
    so a case retired on economics is as auditable as one retired on policy.
    """
    gain, cost, ratio = expected_value_paise(
        amount_at_risk_paise, cause, intervention, channel, attempt_index
    )

    if channel is not Channel.NONE and amount_at_risk_paise < MIN_TICKET_FOR_CONTACT_PAISE:
        return False, (
            f"Ticket of {amount_at_risk_paise} paise is below the "
            f"{MIN_TICKET_FOR_CONTACT_PAISE} paise floor for contacting a customer; "
            f"the goodwill cost outweighs the recoverable amount."
        )

    if ratio < MIN_EXPECTED_VALUE_RATIO:
        return False, (
            f"Expected gain {gain} paise against {cost} paise cost is a ratio of "
            f"{ratio:.1f}, below the {MIN_EXPECTED_VALUE_RATIO:.1f} threshold. "
            f"Not worth firing."
        )

    return True, (
        f"Expected gain {gain} paise against {cost} paise cost (ratio {ratio:.1f})."
    )
