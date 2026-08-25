"""Policy engine: the table must be total, and it must never contradict physics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from razor_pay import policy, taxonomy
from razor_pay.schemas import (
    Diagnosis,
    FailureEvidence,
    InterventionType,
    LeakType,
    RecoveryCase,
    RootCause,
)

NOW = datetime(2026, 8, 24, 11, 0, tzinfo=timezone.utc)

RETRY_TYPES = {
    InterventionType.RETRY_NOW,
    InterventionType.RETRY_BACKOFF,
    InterventionType.RETRY_PAYDAY_WINDOW,
}


def make_case(**kw) -> RecoveryCase:
    defaults = dict(
        case_id="t1",
        leak_type=LeakType.PAYMENT_FAILURE,
        merchant_id="m1",
        customer_ref="c1",
        amount_at_risk_paise=100_000,
        failure_evidence=FailureEvidence(),
        detected_at=NOW,
    )
    defaults.update(kw)
    return RecoveryCase(**defaults)


def diag(cause: RootCause, confidence: float = 0.97) -> Diagnosis:
    return Diagnosis(
        root_cause=cause, confidence=confidence, method="deterministic", rationale="test"
    )


@pytest.mark.parametrize("cause", list(RootCause))
def test_every_cause_resolves_to_action_or_explicit_stop(cause):
    """No cause may fall through. Either it has a ladder or it stops on purpose."""
    decision = policy.decide(make_case(), diag(cause), NOW)
    assert decision.reason, f"{cause} produced a decision with no stated reason"
    if cause in policy.PLAYBOOK:
        assert not decision.is_stop
    else:
        assert decision.is_stop


@pytest.mark.parametrize("cause", list(RootCause))
def test_never_retries_an_instrument_that_cannot_work(cause):
    """A retry may only be chosen where the taxonomy says it could succeed."""
    decision = policy.decide(make_case(), diag(cause), NOW)
    if decision.intervention.type in RETRY_TYPES:
        assert taxonomy.profile_for(cause).retryable_same_instrument, (
            f"policy chose {decision.intervention.type} for {cause}, which can "
            f"never succeed on the same instrument"
        )


def test_instrument_invalid_goes_to_an_update_link_not_a_retry():
    decision = policy.decide(make_case(), diag(RootCause.INSTRUMENT_INVALID), NOW)
    assert decision.intervention.type is InterventionType.SEND_INSTRUMENT_UPDATE_LINK


def test_low_confidence_diagnosis_stops_instead_of_guessing():
    decision = policy.decide(
        make_case(), diag(RootCause.INSUFFICIENT_FUNDS, confidence=0.4), NOW
    )
    assert decision.is_stop
    assert "confidence" in decision.reason.lower()


def test_ladder_exhaustion_is_a_stop():
    cause = RootCause.INSUFFICIENT_FUNDS
    depth = len(policy.PLAYBOOK[cause])
    decision = policy.decide(make_case(attempts_made=depth), diag(cause), NOW)
    assert decision.is_stop
    assert "exhausted" in decision.reason.lower()


def test_cancelled_customer_gets_exactly_one_touch():
    cause = RootCause.CUSTOMER_CANCELLED
    assert len(policy.PLAYBOOK[cause]) == 1
    assert policy.decide(make_case(attempts_made=1), diag(cause), NOW).is_stop


def test_stopped_and_recovered_cases_are_never_acted_on():
    assert policy.decide(make_case(stopped=True), diag(RootCause.ISSUER_DOWNTIME), NOW).is_stop
    assert policy.decide(make_case(recovered=True), diag(RootCause.ISSUER_DOWNTIME), NOW).is_stop


def test_insufficient_funds_defers_to_a_payday_window():
    decision = policy.decide(make_case(), diag(RootCause.INSUFFICIENT_FUNDS), NOW)
    assert decision.intervention.type is InterventionType.RETRY_PAYDAY_WINDOW
    # Must be a real future delay, not an immediate retry wearing a label.
    assert decision.intervention.delay_seconds > 3600
    fires = NOW + timedelta(seconds=decision.intervention.delay_seconds)
    assert fires.day in policy.PAYDAY_DAYS


def test_every_contacting_step_carries_a_dlt_template():
    from razor_pay.schemas import Channel

    for cause, ladder in policy.PLAYBOOK.items():
        for step in ladder:
            if step.channel is not Channel.NONE:
                assert step.template_id, f"{cause}/{step.type} contacts with no DLT template"


def test_consecutive_payday_retries_target_different_windows():
    """Two 'payday' retries seconds apart would be a blind retry wearing a label."""
    just_before = datetime(2026, 9, 1, 9, 59, 59, tzinfo=timezone.utc)
    delay = policy.seconds_to_next_payday(just_before)
    assert delay >= policy.MIN_PAYDAY_GAP_SECONDS
    assert (just_before + timedelta(seconds=delay)).day in policy.PAYDAY_DAYS


@pytest.mark.parametrize("day", range(1, 29))
def test_payday_delay_always_lands_on_a_payday(day):
    moment = datetime(2026, 9, day, 10, 0, tzinfo=timezone.utc)
    delay = policy.seconds_to_next_payday(moment)
    assert delay >= policy.MIN_PAYDAY_GAP_SECONDS
    assert (moment + timedelta(seconds=delay)).day in policy.PAYDAY_DAYS


def test_engine_belief_is_not_harness_truth():
    """The engine's uplift estimate must stay separate from the simulated truth.

    If the policy engine read the harness's UPLIFT table it would have perfect
    knowledge of customer behaviour, and any expected-value rule built on it
    would be measuring the simulation rather than the policy.
    """
    from razor_pay import economics
    from razor_pay.harness import response_model

    assert economics.BELIEVED_UPLIFT is not response_model.UPLIFT
    assert economics.BELIEVED_ATTEMPT_DECAY != response_model.ATTEMPT_DECAY
    # And they must actually disagree somewhere, not merely be copies.
    shared = set(economics.BELIEVED_UPLIFT) & set(response_model.UPLIFT)
    assert any(
        economics.BELIEVED_UPLIFT[k] != response_model.UPLIFT[k] for k in shared
    ), "engine belief is identical to harness truth; that is perfect knowledge"


def test_break_even_threshold_is_a_principle_not_a_tuning_knob():
    """1.0 encodes 'never fire an action that loses money in expectation'.

    Raising it above break-even was measured and destroyed net value; see
    docs/05-worklog.md. Guard against someone re-tuning it upward.
    """
    from razor_pay import economics

    assert economics.MIN_EXPECTED_VALUE_RATIO == 1.0


def test_action_below_its_own_cost_is_refused():
    from razor_pay import economics
    from razor_pay.schemas import Channel

    # Tiny ticket, expensive channel: expected gain cannot cover the send.
    worth_it, reason = economics.is_worth_firing(
        amount_at_risk_paise=1_000,
        cause=RootCause.INSUFFICIENT_FUNDS,
        intervention=InterventionType.SEND_PAYMENT_LINK,
        channel=Channel.WHATSAPP,
        attempt_index=0,
    )
    assert not worth_it
    assert "ratio" in reason.lower()


def test_large_ticket_clears_the_threshold():
    from razor_pay import economics
    from razor_pay.schemas import Channel

    worth_it, _ = economics.is_worth_firing(
        amount_at_risk_paise=500_000,
        cause=RootCause.INSUFFICIENT_FUNDS,
        intervention=InterventionType.RETRY_PAYDAY_WINDOW,
        channel=Channel.NONE,
        attempt_index=0,
    )
    assert worth_it
