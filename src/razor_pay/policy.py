"""Policy engine: root cause + case history -> intervention, or an explicit stop.

Two properties matter for the architecture review:

1. Every root cause maps to an *escalation ladder*, not a single action. The
   ladder is indexed by attempts already made, so the Nth attempt on a case is a
   different action from the 1st. Running off the end of a ladder is a STOP.
2. There is no default "retry". A cause with no ladder stops and lands on the
   exception list. Honest failure beats a guess.

The LLM never invents an action; it only produces a Diagnosis. Action selection
is this deterministic table.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from razor_pay import taxonomy
from razor_pay.schemas import (
    Channel,
    Decision,
    Diagnosis,
    Intervention,
    InterventionType,
    RecoveryCase,
    RootCause,
)

POLICY_VERSION = "2026.08.24-v1"

# Below this, we refuse to act on the diagnosis at all.
CONFIDENCE_THRESHOLD = 0.60

# TRAI DLT-registered template ids. Every customer-facing message must carry one;
# the gate rejects a contact action without it.
TEMPLATE_INSTRUMENT_UPDATE = "DLT_RZP_RCVR_INSTR_001"
TEMPLATE_PAYMENT_LINK = "DLT_RZP_RCVR_LINK_002"
TEMPLATE_SOFT_NUDGE = "DLT_RZP_RCVR_NUDGE_003"
TEMPLATE_COLLECT_REPRESENT = "DLT_RZP_RCVR_COLLECT_004"

_MIN = 60
_HOUR = 3600

# Escalation ladders, indexed by attempts_made.
PLAYBOOK: dict[RootCause, list[Intervention]] = {
    # Balance replenishes on a schedule. Repetition does not help; timing does.
    RootCause.INSUFFICIENT_FUNDS: [
        Intervention(type=InterventionType.RETRY_PAYDAY_WINDOW, channel=Channel.NONE),
        Intervention(type=InterventionType.RETRY_PAYDAY_WINDOW, channel=Channel.NONE),
        Intervention(
            type=InterventionType.SEND_PAYMENT_LINK,
            channel=Channel.WHATSAPP,
            template_id=TEMPLATE_PAYMENT_LINK,
        ),
    ],
    # Transient issuer fault: back off, widen the gap each time.
    RootCause.ISSUER_DOWNTIME: [
        Intervention(type=InterventionType.RETRY_BACKOFF, delay_seconds=20 * _MIN),
        Intervention(type=InterventionType.RETRY_BACKOFF, delay_seconds=1 * _HOUR),
        Intervention(type=InterventionType.RETRY_BACKOFF, delay_seconds=3 * _HOUR),
    ],
    RootCause.GATEWAY_ERROR: [
        Intervention(type=InterventionType.RETRY_BACKOFF, delay_seconds=5 * _MIN),
        Intervention(type=InterventionType.RETRY_BACKOFF, delay_seconds=15 * _MIN),
    ],
    # Retrying a dead instrument can never succeed. Do not spend an attempt on it.
    RootCause.INSTRUMENT_INVALID: [
        Intervention(
            type=InterventionType.SEND_INSTRUMENT_UPDATE_LINK,
            channel=Channel.WHATSAPP,
            template_id=TEMPLATE_INSTRUMENT_UPDATE,
        ),
        Intervention(
            type=InterventionType.SEND_INSTRUMENT_UPDATE_LINK,
            channel=Channel.SMS,
            delay_seconds=24 * _HOUR,
            template_id=TEMPLATE_INSTRUMENT_UPDATE,
        ),
    ],
    RootCause.ACCOUNT_MISMATCH: [
        Intervention(
            type=InterventionType.SEND_INSTRUMENT_UPDATE_LINK,
            channel=Channel.WHATSAPP,
            template_id=TEMPLATE_INSTRUMENT_UPDATE,
        ),
    ],
    # Possible genuine intent not to buy. Exactly one soft touch, then never again.
    RootCause.CUSTOMER_CANCELLED: [
        Intervention(
            type=InterventionType.SOFT_NUDGE,
            channel=Channel.WHATSAPP,
            delay_seconds=6 * _HOUR,
            template_id=TEMPLATE_SOFT_NUDGE,
        ),
    ],
    RootCause.COLLECT_EXPIRED: [
        Intervention(
            type=InterventionType.RE_PRESENT_COLLECT,
            channel=Channel.NONE,
            delay_seconds=30 * _MIN,
            template_id=TEMPLATE_COLLECT_REPRESENT,
        ),
        Intervention(
            type=InterventionType.SEND_PAYMENT_LINK,
            channel=Channel.WHATSAPP,
            template_id=TEMPLATE_PAYMENT_LINK,
        ),
    ],
    RootCause.DECLINED_UNSPECIFIED: [
        Intervention(type=InterventionType.RETRY_BACKOFF, delay_seconds=2 * _HOUR),
        Intervention(
            type=InterventionType.SEND_PAYMENT_LINK,
            channel=Channel.WHATSAPP,
            template_id=TEMPLATE_PAYMENT_LINK,
        ),
    ],
    # Nothing was ever charged, so there is nothing to retry: give them a way to pay.
    RootCause.ABANDONED_NO_ATTEMPT: [
        Intervention(
            type=InterventionType.SEND_PAYMENT_LINK,
            channel=Channel.WHATSAPP,
            delay_seconds=30 * _MIN,
            template_id=TEMPLATE_PAYMENT_LINK,
        ),
        Intervention(
            type=InterventionType.SEND_PAYMENT_LINK,
            channel=Channel.EMAIL,
            delay_seconds=24 * _HOUR,
            template_id=TEMPLATE_PAYMENT_LINK,
        ),
    ],
    # Deliberately absent: RootCause.UNKNOWN. No ladder means stop.
}

# Salary credit in India clusters at month start and mid-month. Retrying
# insufficient_funds at an arbitrary moment is close to worthless; retrying
# inside these windows is the whole intervention.
PAYDAY_DAYS = (1, 15)
PAYDAY_HOUR_IST = 10


# A retry must clear this much time before it counts as a *different* payday
# attempt. Without it, a clock sitting just before a payday hour would schedule
# two "payday" retries seconds apart, which is a blind retry wearing a label.
MIN_PAYDAY_GAP_SECONDS = 12 * 3600


def seconds_to_next_payday(now: datetime, min_gap_seconds: int = MIN_PAYDAY_GAP_SECONDS) -> int:
    """Delay until the next payday window at least `min_gap_seconds` away."""
    earliest = now + timedelta(seconds=min_gap_seconds)
    candidates: list[datetime] = []

    for month_offset in range(0, 4):
        year = now.year + (now.month - 1 + month_offset) // 12
        month = (now.month - 1 + month_offset) % 12 + 1
        for day in PAYDAY_DAYS:
            try:
                candidate = now.replace(
                    year=year,
                    month=month,
                    day=day,
                    hour=PAYDAY_HOUR_IST,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            except ValueError:
                continue
            if candidate >= earliest:
                candidates.append(candidate)

    if not candidates:
        return min_gap_seconds
    return int((min(candidates) - now).total_seconds())


def decide(case: RecoveryCase, diagnosis: Diagnosis, now: datetime) -> Decision:
    """Select the next intervention for a case, or stop."""

    def stop(reason: str) -> Decision:
        return Decision(
            case_id=case.case_id,
            intervention=Intervention(type=InterventionType.STOP),
            reason=reason,
            policy_version=POLICY_VERSION,
            is_stop=True,
        )

    if case.stopped:
        return stop("Case already stopped; no further action is permitted.")

    if case.recovered:
        return stop("Case already recovered; nothing left to collect.")

    if diagnosis.confidence < CONFIDENCE_THRESHOLD:
        return stop(
            f"Diagnosis confidence {diagnosis.confidence:.2f} is below the "
            f"{CONFIDENCE_THRESHOLD:.2f} threshold. Routed to the exception list "
            f"rather than acted on."
        )

    ladder = PLAYBOOK.get(diagnosis.root_cause)
    if not ladder:
        return stop(
            f"No playbook defined for root cause '{diagnosis.root_cause}'. "
            f"Routed to the exception list."
        )

    if case.attempts_made >= len(ladder):
        return stop(
            f"Escalation ladder for '{diagnosis.root_cause}' exhausted after "
            f"{len(ladder)} attempt(s). Stopping rule reached."
        )

    step = ladder[case.attempts_made].model_copy(deep=True)
    step.amount_paise = case.amount_at_risk_paise

    if step.type is InterventionType.RETRY_PAYDAY_WINDOW:
        step.delay_seconds = seconds_to_next_payday(now)

    profile = taxonomy.profile_for(diagnosis.root_cause)
    is_retry = step.type in {
        InterventionType.RETRY_NOW,
        InterventionType.RETRY_BACKOFF,
        InterventionType.RETRY_PAYDAY_WINDOW,
    }
    # Guard against a ladder that contradicts the taxonomy.
    if is_retry and not profile.retryable_same_instrument:
        return stop(
            f"'{diagnosis.root_cause}' can never succeed on the same instrument; "
            f"refusing to spend an attempt on a retry."
        )

    fires_at = now + timedelta(seconds=step.delay_seconds)
    return Decision(
        case_id=case.case_id,
        intervention=step,
        reason=(
            f"Cause '{diagnosis.root_cause}' ({profile.note}) -> step "
            f"{case.attempts_made + 1}/{len(ladder)}: {step.type} via {step.channel}, "
            f"firing {fires_at.isoformat()}."
        ),
        policy_version=POLICY_VERSION,
    )
