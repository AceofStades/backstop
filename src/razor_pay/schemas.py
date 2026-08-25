"""Core domain models.

Every leak adapter emits a `RecoveryCase`; every downstream stage consumes one.
That uniformity is what lets a single engine serve multiple leak types without
each leak growing its own bespoke pipeline.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class LeakType(StrEnum):
    PAYMENT_FAILURE = "payment_failure"
    SUBSCRIPTION_DUNNING = "subscription_dunning"
    CHECKOUT_ABANDONMENT = "checkout_abandonment"


class RootCause(StrEnum):
    """Why the money did not arrive. Drives intervention selection."""

    INSUFFICIENT_FUNDS = "insufficient_funds"
    ISSUER_DOWNTIME = "issuer_downtime"
    GATEWAY_ERROR = "gateway_error"
    INSTRUMENT_INVALID = "instrument_invalid"
    CUSTOMER_CANCELLED = "customer_cancelled"
    COLLECT_EXPIRED = "collect_expired"
    ACCOUNT_MISMATCH = "account_mismatch"
    DECLINED_UNSPECIFIED = "declined_unspecified"
    ABANDONED_NO_ATTEMPT = "abandoned_no_attempt"
    UNKNOWN = "unknown"


class InterventionType(StrEnum):
    RETRY_NOW = "retry_now"
    RETRY_BACKOFF = "retry_backoff"
    RETRY_PAYDAY_WINDOW = "retry_payday_window"
    SEND_INSTRUMENT_UPDATE_LINK = "send_instrument_update_link"
    SEND_PAYMENT_LINK = "send_payment_link"
    RE_PRESENT_COLLECT = "re_present_collect"
    SOFT_NUDGE = "soft_nudge"
    STOP = "stop"


class Channel(StrEnum):
    """NONE = a silent rail-side action that does not contact the customer."""

    NONE = "none"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class Arm(StrEnum):
    TREATMENT = "treatment"
    CONTROL = "control"


class RefusalCode(StrEnum):
    """Structured refusals. The gate never raises; it returns one of these."""

    MANDATE_EXPIRED = "mandate_expired"
    MERCHANT_SCOPE_MISMATCH = "merchant_scope_mismatch"
    PER_ACTION_CEILING_EXCEEDED = "per_action_ceiling_exceeded"
    VELOCITY_CAP_EXCEEDED = "velocity_cap_exceeded"
    ATTEMPT_CAP_REACHED = "attempt_cap_reached"
    CHANNEL_NOT_CONSENTED = "channel_not_consented"
    CUSTOMER_CONTACT_BUDGET_EXHAUSTED = "customer_contact_budget_exhausted"
    OUTSIDE_CONTACT_WINDOW = "outside_contact_window"
    CASE_PERMANENTLY_STOPPED = "case_permanently_stopped"


class FailureEvidence(BaseModel):
    """Raw signal from Razorpay, before interpretation."""

    error_code: str | None = None
    error_description: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    error_reason: str | None = None
    method: str | None = None
    raw: dict = Field(default_factory=dict)


class RecoveryCase(BaseModel):
    case_id: str
    leak_type: LeakType
    merchant_id: str
    customer_ref: str
    amount_at_risk_paise: int = Field(gt=0)
    currency: str = "INR"
    entity_refs: dict[str, str] = Field(default_factory=dict)
    failure_evidence: FailureEvidence = Field(default_factory=FailureEvidence)
    detected_at: datetime
    # Assigned by the harness at intake, never by an adapter, so assignment
    # cannot depend on anything the adapter observed.
    arm: Arm = Arm.CONTROL
    # Ground truth we injected when seeding. Used ONLY to score the diagnoser,
    # never read by the diagnoser, policy engine, or executor.
    injected_cause: RootCause | None = None
    attempts_made: int = 0
    stopped: bool = False
    recovered: bool = False
    recovered_at: datetime | None = None


class Diagnosis(BaseModel):
    root_cause: RootCause
    confidence: float = Field(ge=0.0, le=1.0)
    method: str  # "deterministic" | "llm" | "fallback"
    rationale: str


class Intervention(BaseModel):
    type: InterventionType
    channel: Channel = Channel.NONE
    delay_seconds: int = 0
    amount_paise: int = 0
    template_id: str | None = None  # TRAI DLT-registered template


class Decision(BaseModel):
    case_id: str
    intervention: Intervention
    reason: str
    policy_version: str
    is_stop: bool = False


class GateResult(BaseModel):
    allowed: bool
    refusal_code: RefusalCode | None = None
    reason: str = ""
    checks_performed: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    executed: bool
    idempotency_key: str
    razorpay_entity: dict = Field(default_factory=dict)
    error: str | None = None
