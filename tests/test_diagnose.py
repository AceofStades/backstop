"""Diagnoser and taxonomy coverage."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from razor_pay import taxonomy
from razor_pay.diagnose import Diagnoser
from razor_pay.schemas import FailureEvidence, LeakType, RecoveryCase, RootCause

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def make_case(evidence: FailureEvidence, leak=LeakType.PAYMENT_FAILURE) -> RecoveryCase:
    return RecoveryCase(
        case_id="d1",
        leak_type=leak,
        merchant_id="m1",
        customer_ref="c1",
        amount_at_risk_paise=100_000,
        failure_evidence=evidence,
        detected_at=NOW,
        injected_cause=RootCause.INSUFFICIENT_FUNDS,
    )


@pytest.mark.parametrize("code", taxonomy.known_error_codes())
def test_every_known_code_diagnoses_deterministically(code):
    diagnosis = Diagnoser(use_llm=False).diagnose(make_case(FailureEvidence(error_code=code)))
    assert diagnosis.method == "deterministic"
    assert diagnosis.confidence > 0.9
    assert diagnosis.root_cause is not RootCause.UNKNOWN


@pytest.mark.parametrize("cause", list(RootCause))
def test_every_root_cause_has_a_profile(cause):
    assert taxonomy.profile_for(cause) is not None


def test_unrecognised_code_without_llm_becomes_an_exception():
    diagnosis = Diagnoser(use_llm=False).diagnose(
        make_case(FailureEvidence(error_code=None, error_description="something odd"))
    )
    assert diagnosis.root_cause is RootCause.UNKNOWN
    assert diagnosis.confidence == 0.0


def test_abandonment_is_structural_not_guessed():
    diagnosis = Diagnoser(use_llm=False).diagnose(
        make_case(FailureEvidence(error_description="no attempt"), LeakType.CHECKOUT_ABANDONMENT)
    )
    assert diagnosis.root_cause is RootCause.ABANDONED_NO_ATTEMPT
    assert diagnosis.method == "deterministic"


def test_diagnoser_never_reads_injected_ground_truth():
    """The ground-truth field exists only for scoring; it must not influence output."""
    evidence = FailureEvidence(error_code="invalid_vpa")
    honest = make_case(evidence)
    honest.injected_cause = RootCause.INSUFFICIENT_FUNDS  # deliberately contradictory
    diagnosis = Diagnoser(use_llm=False).diagnose(honest)
    assert diagnosis.root_cause is RootCause.INSTRUMENT_INVALID


def test_diagnoser_degrades_rather_than_crashing_without_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    diagnoser = Diagnoser(use_llm=True)
    assert diagnoser.use_llm is False
    assert diagnoser.diagnose(make_case(FailureEvidence())).root_cause is RootCause.UNKNOWN
