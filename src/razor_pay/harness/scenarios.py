"""The four scripted refusal scenarios.

Each one ends in a structured refusal carrying a reason code, logged to the
append-only ledger. None of them raises, none of them retries blindly, and none
of them moves money. This is the "one failure handled gracefully" the brief asks
for, times four.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from razor_pay import mandate as mandate_mod
from razor_pay import policy
from razor_pay.ledger import Ledger
from razor_pay.mandate import IST, Mandate
from razor_pay.schemas import (
    Arm,
    Channel,
    Decision,
    Diagnosis,
    FailureEvidence,
    Intervention,
    InterventionType,
    LeakType,
    RecoveryCase,
    RootCause,
)
from razor_pay.store import Store

MERCHANT = "merch_demo_001"


def _case(case_id: str, amount_paise: int, detected_at: datetime, **kw) -> RecoveryCase:
    return RecoveryCase(
        case_id=case_id,
        leak_type=LeakType.PAYMENT_FAILURE,
        merchant_id=kw.pop("merchant_id", MERCHANT),
        customer_ref="cust_demo",
        amount_at_risk_paise=amount_paise,
        failure_evidence=FailureEvidence(error_code="insufficient_funds"),
        detected_at=detected_at,
        arm=Arm.TREATMENT,
        injected_cause=RootCause.INSUFFICIENT_FUNDS,
        **kw,
    )


def _mandate(now: datetime, **overrides) -> Mandate:
    params = dict(
        mandate_id="mand_demo",
        merchant_id=MERCHANT,
        issued_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=7),
        per_action_cap_paise=500_000,
        velocity_cap_paise=5_000_000,
        velocity_window_hours=24,
        max_attempts_per_case=3,
    )
    params.update(overrides)
    return Mandate(**params)


def _decision(case: RecoveryCase, itype: InterventionType, channel: Channel, delay=0) -> Decision:
    return Decision(
        case_id=case.case_id,
        intervention=Intervention(
            type=itype,
            channel=channel,
            amount_paise=case.amount_at_risk_paise,
            delay_seconds=delay,
            template_id=policy.TEMPLATE_PAYMENT_LINK if channel is not Channel.NONE else None,
        ),
        reason="scripted scenario",
        policy_version=policy.POLICY_VERSION,
    )


def run_refusal_scenarios(db: str, echo, secho) -> None:
    now = datetime(2026, 8, 24, 12, 0, tzinfo=IST)
    store = Store(db)
    ledger = Ledger(store, "batch_demo_refusals")

    scenarios: list[tuple[str, Mandate, RecoveryCase, Decision]] = []

    # 1. Amount above the per-action ceiling.
    c1 = _case("demo_ceiling", 900_000, now)
    scenarios.append(
        (
            "Action exceeds the mandate's per-action ceiling",
            _mandate(now),
            c1,
            _decision(c1, InterventionType.RETRY_BACKOFF, Channel.NONE),
        )
    )

    # 2. Case has exhausted its permitted attempts.
    c2 = _case("demo_attempts", 120_000, now, attempts_made=3)
    scenarios.append(
        (
            "Case has reached its attempt cap",
            _mandate(now),
            c2,
            _decision(c2, InterventionType.RETRY_BACKOFF, Channel.NONE),
        )
    )

    # 3. Contact would land at 23:00 IST, outside the RBI window.
    c3 = _case("demo_window", 150_000, now)
    scenarios.append(
        (
            "Customer contact would fire outside the RBI 08:00-19:00 window",
            _mandate(now),
            c3,
            _decision(c3, InterventionType.SEND_PAYMENT_LINK, Channel.WHATSAPP, delay=11 * 3600),
        )
    )

    # 4. The grant itself has lapsed.
    c4 = _case("demo_expired", 100_000, now)
    scenarios.append(
        (
            "Mandate has expired",
            _mandate(now, expires_at=now - timedelta(hours=1)),
            c4,
            _decision(c4, InterventionType.RETRY_BACKOFF, Channel.NONE),
        )
    )

    secho("\n== Refusal scenarios ==", bold=True)
    all_refused = True
    for i, (title, mandate, case, decision) in enumerate(scenarios, start=1):
        gate = mandate_mod.check(mandate, case, decision, now, spent_in_window_paise=0)
        ledger.record_gate(now, case, decision, gate, mandate.mandate_id)

        status = "REFUSED" if not gate.allowed else "ALLOWED"
        colour = "green" if not gate.allowed else "red"
        all_refused &= not gate.allowed

        secho(f"\n{i}. {title}", bold=True)
        secho(f"   result : {status}", fg=colour)
        echo(f"   code   : {gate.refusal_code.value if gate.refusal_code else '-'}")
        echo(f"   reason : {gate.reason}")
        echo(f"   checked: {', '.join(gate.checks_performed)}")

        if gate.refusal_code and gate.refusal_code.value == "outside_contact_window":
            fires_at = now + timedelta(seconds=decision.intervention.delay_seconds)
            echo(
                f"   defer  : rescheduled to "
                f"{mandate_mod.next_allowed_contact_time(fires_at).isoformat()}"
            )

    store.commit()
    secho(
        f"\nAll four refused cleanly: {all_refused}. "
        f"Ledger entries written: {ledger.count()}",
        fg="green" if all_refused else "red",
        bold=True,
    )
    store.close()
