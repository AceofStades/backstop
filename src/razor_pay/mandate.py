"""The mandate gate: the only thing standing between a proposed action and money.

Every money-moving or customer-contacting call passes through `check()` first.
The gate never raises and never silently drops an action -- it returns a
`GateResult` carrying a structured `RefusalCode`, so a refusal is an outcome the
caller can act on rather than a 500.

Compliance is enforced *here*, in deterministic code, rather than left to the
model's judgement. The LLM cannot talk its way past a closed contact window.

Regulatory basis:
  - RBI recovery conduct rules bar customer contact outside 08:00-19:00 local
    time, across voice, SMS and instant messaging alike.
  - TRAI TCCCPR requires every A2P message to carry a DLT-registered template id.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from razor_pay.schemas import (
    Channel,
    Decision,
    GateResult,
    InterventionType,
    RecoveryCase,
    RefusalCode,
)

IST = ZoneInfo("Asia/Kolkata")

# RBI permitted customer-contact window.
CONTACT_WINDOW_START = time(8, 0)
CONTACT_WINDOW_END = time(19, 0)

CONTACTING_CHANNELS = {Channel.SMS, Channel.WHATSAPP, Channel.EMAIL}

MONEY_MOVING = {
    InterventionType.RETRY_NOW,
    InterventionType.RETRY_BACKOFF,
    InterventionType.RETRY_PAYDAY_WINDOW,
    InterventionType.RE_PRESENT_COLLECT,
}


class Mandate(BaseModel):
    """A bounded grant of authority to act on a merchant's behalf."""

    mandate_id: str
    merchant_id: str
    issued_at: datetime
    expires_at: datetime
    per_action_cap_paise: int = Field(gt=0)
    velocity_cap_paise: int = Field(gt=0)
    velocity_window_hours: int = 24
    max_attempts_per_case: int = 3
    allowed_channels: set[Channel] = Field(
        default_factory=lambda: {Channel.NONE, Channel.WHATSAPP, Channel.SMS}
    )

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


def in_contact_window(moment: datetime) -> bool:
    local = moment.astimezone(IST)
    return CONTACT_WINDOW_START <= local.time() < CONTACT_WINDOW_END


def next_allowed_contact_time(moment: datetime) -> datetime:
    """Earliest instant at or after `moment` that falls inside the RBI window."""
    local = moment.astimezone(IST)
    if local.time() < CONTACT_WINDOW_START:
        return local.replace(
            hour=CONTACT_WINDOW_START.hour,
            minute=CONTACT_WINDOW_START.minute,
            second=0,
            microsecond=0,
        )
    if local.time() >= CONTACT_WINDOW_END:
        nxt = local + timedelta(days=1)
        return nxt.replace(
            hour=CONTACT_WINDOW_START.hour,
            minute=CONTACT_WINDOW_START.minute,
            second=0,
            microsecond=0,
        )
    return local


def check(
    mandate: Mandate,
    case: RecoveryCase,
    decision: Decision,
    now: datetime,
    spent_in_window_paise: int = 0,
) -> GateResult:
    """Authorise or refuse a single proposed action.

    Checks run in order of severity: scope and validity of the grant first, then
    per-case limits, then spend limits, then channel compliance.
    """
    checks: list[str] = []
    step = decision.intervention
    fires_at = now + timedelta(seconds=step.delay_seconds)

    def refuse(code: RefusalCode, reason: str) -> GateResult:
        return GateResult(
            allowed=False, refusal_code=code, reason=reason, checks_performed=checks
        )

    checks.append("case_not_permanently_stopped")
    if case.stopped:
        return refuse(
            RefusalCode.CASE_PERMANENTLY_STOPPED,
            f"Case {case.case_id} is permanently stopped; no action may fire.",
        )

    checks.append("mandate_expiry")
    if mandate.is_expired(now):
        return refuse(
            RefusalCode.MANDATE_EXPIRED,
            f"Mandate {mandate.mandate_id} expired at "
            f"{mandate.expires_at.isoformat()}; refusing at {now.isoformat()}.",
        )

    checks.append("merchant_scope")
    if mandate.merchant_id != case.merchant_id:
        return refuse(
            RefusalCode.MERCHANT_SCOPE_MISMATCH,
            f"Mandate {mandate.mandate_id} is scoped to merchant "
            f"{mandate.merchant_id}, but case belongs to {case.merchant_id}.",
        )

    checks.append("attempt_cap")
    if case.attempts_made >= mandate.max_attempts_per_case:
        return refuse(
            RefusalCode.ATTEMPT_CAP_REACHED,
            f"Case {case.case_id} has used {case.attempts_made} of "
            f"{mandate.max_attempts_per_case} permitted attempts.",
        )

    if step.type in MONEY_MOVING:
        checks.append("per_action_ceiling")
        if step.amount_paise > mandate.per_action_cap_paise:
            return refuse(
                RefusalCode.PER_ACTION_CEILING_EXCEEDED,
                f"Action amount {step.amount_paise} paise exceeds the per-action "
                f"ceiling of {mandate.per_action_cap_paise} paise.",
            )

        checks.append("velocity_cap")
        projected = spent_in_window_paise + step.amount_paise
        if projected > mandate.velocity_cap_paise:
            return refuse(
                RefusalCode.VELOCITY_CAP_EXCEEDED,
                f"Action would bring {mandate.velocity_window_hours}h volume to "
                f"{projected} paise, over the {mandate.velocity_cap_paise} paise cap.",
            )

    if step.channel in CONTACTING_CHANNELS:
        checks.append("channel_consent")
        if step.channel not in mandate.allowed_channels:
            return refuse(
                RefusalCode.CHANNEL_NOT_CONSENTED,
                f"Channel '{step.channel}' is not in the mandate's consented set "
                f"{sorted(mandate.allowed_channels)}.",
            )

        checks.append("dlt_template_registered")
        if not step.template_id:
            return refuse(
                RefusalCode.CHANNEL_NOT_CONSENTED,
                "TRAI TCCCPR requires a DLT-registered template id for A2P "
                "messaging; none was supplied.",
            )

        checks.append("rbi_contact_window")
        if not in_contact_window(fires_at):
            deferred_to = next_allowed_contact_time(fires_at)
            return refuse(
                RefusalCode.OUTSIDE_CONTACT_WINDOW,
                f"Contact would fire at {fires_at.astimezone(IST).isoformat()}, "
                f"outside the RBI 08:00-19:00 window. Defer to "
                f"{deferred_to.isoformat()}.",
            )

    return GateResult(allowed=True, checks_performed=checks)
