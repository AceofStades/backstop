"""The gate. Each refusal must be structured, reasoned, and non-raising."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from razor_pay import policy
from razor_pay.mandate import (
    IST,
    Mandate,
    check,
    in_contact_window,
    next_allowed_contact_time,
)
from razor_pay.schemas import (
    Channel,
    Decision,
    FailureEvidence,
    Intervention,
    InterventionType,
    LeakType,
    RecoveryCase,
    RefusalCode,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=IST)


def make_case(**kw) -> RecoveryCase:
    defaults = dict(
        case_id="g1",
        leak_type=LeakType.PAYMENT_FAILURE,
        merchant_id="m1",
        customer_ref="c1",
        amount_at_risk_paise=100_000,
        failure_evidence=FailureEvidence(error_code="insufficient_funds"),
        detected_at=NOW,
    )
    defaults.update(kw)
    return RecoveryCase(**defaults)


def make_mandate(**kw) -> Mandate:
    defaults = dict(
        mandate_id="m",
        merchant_id="m1",
        issued_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        per_action_cap_paise=500_000,
        velocity_cap_paise=5_000_000,
        max_attempts_per_case=3,
    )
    defaults.update(kw)
    return Mandate(**defaults)


def make_decision(case, itype, channel=Channel.NONE, delay=0, template=None) -> Decision:
    return Decision(
        case_id=case.case_id,
        intervention=Intervention(
            type=itype,
            channel=channel,
            amount_paise=case.amount_at_risk_paise,
            delay_seconds=delay,
            template_id=template,
        ),
        reason="test",
        policy_version=policy.POLICY_VERSION,
    )


def test_happy_path_allows_and_records_its_checks():
    case = make_case()
    result = check(make_mandate(), case, make_decision(case, InterventionType.RETRY_BACKOFF), NOW)
    assert result.allowed
    assert result.refusal_code is None
    assert "per_action_ceiling" in result.checks_performed
    assert "velocity_cap" in result.checks_performed


def test_refuses_above_per_action_ceiling():
    case = make_case(amount_at_risk_paise=900_000)
    result = check(make_mandate(), case, make_decision(case, InterventionType.RETRY_BACKOFF), NOW)
    assert not result.allowed
    assert result.refusal_code is RefusalCode.PER_ACTION_CEILING_EXCEEDED


def test_refuses_when_velocity_cap_would_be_breached():
    case = make_case(amount_at_risk_paise=400_000)
    result = check(
        make_mandate(),
        case,
        make_decision(case, InterventionType.RETRY_BACKOFF),
        NOW,
        spent_in_window_paise=4_800_000,
    )
    assert not result.allowed
    assert result.refusal_code is RefusalCode.VELOCITY_CAP_EXCEEDED


def test_refuses_at_attempt_cap():
    case = make_case(attempts_made=3)
    result = check(make_mandate(), case, make_decision(case, InterventionType.RETRY_BACKOFF), NOW)
    assert not result.allowed
    assert result.refusal_code is RefusalCode.ATTEMPT_CAP_REACHED


def test_refuses_expired_mandate():
    case = make_case()
    result = check(
        make_mandate(expires_at=NOW - timedelta(hours=1)),
        case,
        make_decision(case, InterventionType.RETRY_BACKOFF),
        NOW,
    )
    assert not result.allowed
    assert result.refusal_code is RefusalCode.MANDATE_EXPIRED


def test_refuses_when_mandate_covers_a_different_merchant():
    case = make_case(merchant_id="someone_else")
    result = check(make_mandate(), case, make_decision(case, InterventionType.RETRY_BACKOFF), NOW)
    assert not result.allowed
    assert result.refusal_code is RefusalCode.MERCHANT_SCOPE_MISMATCH


def test_refuses_contact_outside_the_rbi_window():
    case = make_case()
    decision = make_decision(
        case,
        InterventionType.SEND_PAYMENT_LINK,
        Channel.WHATSAPP,
        delay=11 * 3600,  # fires 23:00 IST
        template=policy.TEMPLATE_PAYMENT_LINK,
    )
    result = check(make_mandate(), case, decision, NOW)
    assert not result.allowed
    assert result.refusal_code is RefusalCode.OUTSIDE_CONTACT_WINDOW


def test_refuses_contact_without_a_dlt_template():
    case = make_case()
    decision = make_decision(case, InterventionType.SEND_PAYMENT_LINK, Channel.WHATSAPP)
    result = check(make_mandate(), case, decision, NOW)
    assert not result.allowed
    assert result.refusal_code is RefusalCode.CHANNEL_NOT_CONSENTED


def test_refuses_unconsented_channel():
    case = make_case()
    decision = make_decision(
        case,
        InterventionType.SEND_PAYMENT_LINK,
        Channel.EMAIL,
        template=policy.TEMPLATE_PAYMENT_LINK,
    )
    result = check(
        make_mandate(allowed_channels={Channel.NONE, Channel.WHATSAPP}), case, decision, NOW
    )
    assert not result.allowed
    assert result.refusal_code is RefusalCode.CHANNEL_NOT_CONSENTED


def test_stopped_case_can_never_fire():
    case = make_case(stopped=True)
    result = check(make_mandate(), case, make_decision(case, InterventionType.RETRY_BACKOFF), NOW)
    assert not result.allowed
    assert result.refusal_code is RefusalCode.CASE_PERMANENTLY_STOPPED


@pytest.mark.parametrize(
    "hour,expected", [(7, False), (8, True), (12, True), (18, True), (19, False), (23, False)]
)
def test_contact_window_boundaries(hour, expected):
    assert in_contact_window(NOW.replace(hour=hour)) is expected


def test_deferral_always_lands_inside_the_window():
    for hour in range(24):
        moment = NOW.replace(hour=hour)
        assert in_contact_window(next_allowed_contact_time(moment))


def test_gate_never_raises_on_any_intervention_type():
    """A refusal is a return value, never an exception."""
    case = make_case()
    for itype in InterventionType:
        for channel in Channel:
            result = check(make_mandate(), case, make_decision(case, itype, channel), NOW)
            assert isinstance(result.allowed, bool)
