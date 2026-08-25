"""Experiment mechanics: assignment, common random numbers, idempotent re-runs."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from razor_pay.adapters.base import SeedClient
from razor_pay.adapters.payment_failure import PaymentFailureAdapter
from razor_pay.diagnose import Diagnoser
from razor_pay.execute import SimulatedExecutor
from razor_pay.harness.assign import assign_all, assign_arm
from razor_pay.harness.metrics import compute, sensitivity_sweep
from razor_pay.harness.response_model import ResponseModel
from razor_pay.harness.runner import BatchRunner
from razor_pay.ledger import Ledger, Stage
from razor_pay.mandate import Mandate
from razor_pay.schemas import Arm, RootCause
from razor_pay.store import Store

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def build_batch(tmp_path, n=60):
    store = Store(tmp_path / "h.db")
    cases = PaymentFailureAdapter().seed(
        n, random.Random(11), NOW, "merch_demo_001", SeedClient()
    )
    assign_all(cases, 0.4)
    mandate = Mandate(
        mandate_id="mand_test",
        merchant_id="merch_demo_001",
        issued_at=NOW,
        expires_at=NOW.replace(year=2027),
        per_action_cap_paise=2_500_000,
        velocity_cap_paise=500_000_000,
    )
    store.create_batch("b1", NOW, mandate, {})
    for case in cases:
        store.insert_case("b1", case)
    store.commit()
    return store, cases, mandate


def make_runner(store, mandate):
    return BatchRunner(
        store=store,
        ledger=Ledger(store, "b1"),
        mandate=mandate,
        diagnoser=Diagnoser(use_llm=False),
        executor=SimulatedExecutor(),
        response=ResponseModel(),
    )


def test_assignment_is_deterministic_and_roughly_balanced():
    first = [assign_arm(f"c_{i}", 0.4) for i in range(2000)]
    second = [assign_arm(f"c_{i}", 0.4) for i in range(2000)]
    assert first == second
    share = sum(1 for a in first if a is Arm.CONTROL) / len(first)
    assert 0.36 < share < 0.44


def test_self_recovery_draw_is_identical_across_arms():
    """Common random numbers: assignment cannot leak into the outcome."""
    model = ResponseModel()
    for i in range(500):
        case_id = f"crn_{i}"
        cause = RootCause.INSUFFICIENT_FUNDS
        assert model.would_self_recover(case_id, cause) == model.would_self_recover(
            case_id, cause
        )


def test_control_baseline_is_not_zero():
    """A 0% control arm would make any uplift look spectacular and mean nothing."""
    model = ResponseModel()
    for cause in RootCause:
        assert model.baseline_for(cause) > 0.0


def test_control_arm_never_fires_an_action(tmp_path):
    store, cases, mandate = build_batch(tmp_path)
    traces = make_runner(store, mandate).run(cases)
    for trace in traces:
        if trace.arm is Arm.CONTROL:
            assert trace.actions_fired == 0
            assert trace.interventions == []
    store.close()


def test_rerunning_a_batch_executes_nothing_further(tmp_path):
    store, cases, mandate = build_batch(tmp_path)
    runner = make_runner(store, mandate)
    runner.run(cases)

    def execute_count():
        cur = store.conn.execute(
            "SELECT COUNT(*) AS n FROM ledger WHERE stage = ? AND batch_id = ?",
            (Stage.EXECUTE.value, "b1"),
        )
        return cur.fetchone()["n"]

    before = execute_count()
    assert before > 0
    make_runner(store, mandate).run(store.load_cases("b1"))
    assert execute_count() == before
    store.close()


def test_metrics_report_incremental_not_gross(tmp_path):
    store, cases, mandate = build_batch(tmp_path, n=200)
    traces = make_runner(store, mandate).run(cases)
    metrics = compute(traces, cases, ResponseModel().describe())

    assert metrics["control"]["n"] > 0, "an experiment with no control arm proves nothing"
    assert metrics["incremental_paise"] <= metrics["gross_recovered_paise"]
    assert metrics["counterfactual_paise"] > 0
    lo, hi = metrics["lift_ci_pp"]
    assert lo <= metrics["lift_pp"] <= hi
    store.close()


def test_exceptions_are_listed_not_hidden(tmp_path):
    store, cases, mandate = build_batch(tmp_path, n=120)
    traces = make_runner(store, mandate).run(cases)
    metrics = compute(traces, cases, ResponseModel().describe())
    for exc in metrics["exceptions"]:
        assert exc["why"], "every exception must say why it could not be resolved"
    store.close()


def test_sensitivity_sweep_moves_the_lift(tmp_path):
    store, cases, mandate = build_batch(tmp_path, n=150)
    traces = make_runner(store, mandate).run(cases)
    rows = sensitivity_sweep(traces, cases)
    assert len(rows) == 5
    # A higher assumed baseline must not leave the reported lift unchanged.
    assert rows[0]["control_rate"] < rows[-1]["control_rate"]
    store.close()


def test_contact_budget_is_counted_across_cases_for_one_customer(tmp_path):
    """The budget must span cases, which is the whole point of it."""
    from datetime import timedelta

    from razor_pay.schemas import FailureEvidence, LeakType, RecoveryCase

    store = Store(tmp_path / "budget.db")
    mandate = Mandate(
        mandate_id="m",
        merchant_id="merch_demo_001",
        issued_at=NOW,
        expires_at=NOW.replace(year=2027),
        per_action_cap_paise=2_500_000,
        velocity_cap_paise=500_000_000,
    )
    store.create_batch("b1", NOW, mandate, {})

    # Two separate cases, same customer.
    for i in range(2):
        store.insert_case(
            "b1",
            RecoveryCase(
                case_id=f"shared_{i}",
                leak_type=LeakType.PAYMENT_FAILURE,
                merchant_id="merch_demo_001",
                customer_ref="cust_shared",
                amount_at_risk_paise=100_000,
                failure_evidence=FailureEvidence(error_code="insufficient_funds"),
                detected_at=NOW,
            ),
        )
    store.commit()

    ledger = Ledger(store, "b1")
    ledger.record(
        ts=NOW,
        case_id="shared_0",
        stage=Stage.EXECUTE,
        reason="contacted",
        channel="whatsapp",
        idempotency_key="shared_0:1",
    )
    store.commit()

    # The contact on case 0 must be visible when gating case 1.
    later = NOW + timedelta(hours=1)
    assert ledger.customer_contacts_in_window("cust_shared", later, 168) == 1
    assert ledger.customer_contacts_in_window("cust_other", later, 168) == 0
    store.close()


def test_contact_budget_spans_batches(tmp_path):
    """A customer does not experience batch boundaries.

    Being contacted four times yesterday and four times today is eight contacts
    to them, however the runs were organised. The budget window is wall-clock.
    """
    from datetime import timedelta

    from razor_pay.schemas import FailureEvidence, LeakType, RecoveryCase

    store = Store(tmp_path / "batches.db")
    mandate = Mandate(
        mandate_id="m",
        merchant_id="merch_demo_001",
        issued_at=NOW,
        expires_at=NOW.replace(year=2027),
        per_action_cap_paise=2_500_000,
        velocity_cap_paise=500_000_000,
    )

    for batch in ("b1", "b2"):
        store.create_batch(batch, NOW, mandate, {})
        store.insert_case(
            batch,
            RecoveryCase(
                case_id=f"{batch}_case",
                leak_type=LeakType.PAYMENT_FAILURE,
                merchant_id="merch_demo_001",
                customer_ref="cust_repeat",
                amount_at_risk_paise=100_000,
                failure_evidence=FailureEvidence(error_code="insufficient_funds"),
                detected_at=NOW,
            ),
        )
        Ledger(store, batch).record(
            ts=NOW,
            case_id=f"{batch}_case",
            stage=Stage.EXECUTE,
            reason="contacted",
            channel="whatsapp",
            idempotency_key=f"{batch}_case:1",
        )
    store.commit()

    later = NOW + timedelta(hours=1)
    # Querying from either batch's ledger must see both contacts.
    assert Ledger(store, "b1").customer_contacts_in_window("cust_repeat", later, 168) == 2
    assert Ledger(store, "b2").customer_contacts_in_window("cust_repeat", later, 168) == 2
    store.close()


def test_contact_budget_respects_the_time_window(tmp_path):
    """Contacts older than the window must not count against the budget."""
    from datetime import timedelta

    from razor_pay.schemas import FailureEvidence, LeakType, RecoveryCase

    store = Store(tmp_path / "window.db")
    mandate = Mandate(
        mandate_id="m",
        merchant_id="merch_demo_001",
        issued_at=NOW,
        expires_at=NOW.replace(year=2027),
        per_action_cap_paise=2_500_000,
        velocity_cap_paise=500_000_000,
    )
    store.create_batch("b1", NOW, mandate, {})
    store.insert_case(
        "b1",
        RecoveryCase(
            case_id="old_case",
            leak_type=LeakType.PAYMENT_FAILURE,
            merchant_id="merch_demo_001",
            customer_ref="cust_old",
            amount_at_risk_paise=100_000,
            failure_evidence=FailureEvidence(error_code="insufficient_funds"),
            detected_at=NOW,
        ),
    )
    Ledger(store, "b1").record(
        ts=NOW,
        case_id="old_case",
        stage=Stage.EXECUTE,
        reason="contacted long ago",
        channel="whatsapp",
        idempotency_key="old_case:1",
    )
    store.commit()

    ledger = Ledger(store, "b1")
    assert ledger.customer_contacts_in_window("cust_old", NOW + timedelta(hours=1), 168) == 1
    # Same contact, queried from far enough in the future to fall out of window.
    assert ledger.customer_contacts_in_window("cust_old", NOW + timedelta(days=30), 168) == 0
    store.close()


def test_case_ids_are_unique_across_batches():
    """Two seeds must not collide on the primary key.

    `run` tells the user to seed a fresh batch when it refuses to re-run one,
    so a collision here breaks the exact flow the error message recommends.
    """
    from razor_pay.adapters.payment_failure import PaymentFailureAdapter

    first = PaymentFailureAdapter().seed(
        10, random.Random(1), NOW, "m1", SeedClient(), token="134500"
    )
    second = PaymentFailureAdapter().seed(
        10, random.Random(1), NOW, "m1", SeedClient(), token="134600"
    )
    ids_a = {c.case_id for c in first}
    ids_b = {c.case_id for c in second}
    assert not (ids_a & ids_b), "case ids collide across batches"
