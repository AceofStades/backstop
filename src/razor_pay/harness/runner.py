"""Batch runner: drives every case through the loop and records what happened.

Each case carries its own virtual clock starting at `detected_at` and advancing
by each intervention's delay. That matters: an action scheduled with a 6-hour
backoff can land outside the RBI contact window even though the batch was started
at noon, so the compliance gate is exercised by ordinary cases and not only by
the scripted refusal demo.

Control-arm cases are never diagnosed and never acted on. They exist to answer
"what would have happened anyway", which is the only way the treatment number
means anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from razor_pay import economics
from razor_pay import mandate as mandate_mod
from razor_pay import policy
from razor_pay.diagnose import Diagnoser
from razor_pay.execute import Executor, idempotency_key
from razor_pay.harness.response_model import ResponseModel
from razor_pay.ledger import Ledger, Stage
from razor_pay.mandate import Mandate
from razor_pay.schemas import (
    Arm,
    Channel,
    InterventionType,
    RecoveryCase,
    RefusalCode,
    RootCause,
)
from razor_pay.store import Store

MAX_DEFERRALS = 2
# Ceiling on loop iterations per case; the policy ladder normally stops first.
MAX_ITERATIONS = 6


@dataclass
class CaseTrace:
    case_id: str
    arm: Arm
    diagnosed_cause: RootCause | None = None
    diagnosis_confidence: float = 0.0
    diagnosis_method: str = "n/a"
    actions_fired: int = 0
    deferrals: int = 0
    refusals: list[str] = field(default_factory=list)
    interventions: list[str] = field(default_factory=list)
    action_cost_paise: int = 0
    contacts: int = 0
    recovered: bool = False
    recovered_via: str = "none"  # "self" | "intervention" | "none"
    stop_reason: str = ""
    amount_paise: int = 0


class BatchRunner:
    def __init__(
        self,
        store: Store,
        ledger: Ledger,
        mandate: Mandate,
        diagnoser: Diagnoser,
        executor: Executor,
        response: ResponseModel,
    ) -> None:
        self.store = store
        self.ledger = ledger
        self.mandate = mandate
        self.diagnoser = diagnoser
        self.executor = executor
        self.response = response

    def run(self, cases: list[RecoveryCase]) -> list[CaseTrace]:
        traces = [self._run_case(c) for c in cases]
        self.store.commit()
        return traces

    # ------------------------------------------------------------------

    def _run_case(self, case: RecoveryCase) -> CaseTrace:
        trace = CaseTrace(
            case_id=case.case_id, arm=case.arm, amount_paise=case.amount_at_risk_paise
        )
        clock = case.detected_at

        self.ledger.record(
            ts=clock,
            case_id=case.case_id,
            stage=Stage.DETECT,
            reason=(
                f"{case.leak_type.value}: {case.amount_at_risk_paise} paise at risk "
                f"(arm={case.arm.value})."
            ),
            amount_paise=case.amount_at_risk_paise,
            detail={"entity_refs": case.entity_refs, "arm": case.arm.value},
        )

        # Ground truth for the response model only. Never visible to the engine.
        true_cause = case.injected_cause or RootCause.UNKNOWN
        self_recovers = self.response.would_self_recover(case.case_id, true_cause)

        if case.arm is Arm.CONTROL:
            case.recovered = self_recovers
            trace.recovered = self_recovers
            trace.recovered_via = "self" if self_recovers else "none"
            trace.stop_reason = "Control arm: held out, no intervention attempted."
            self.ledger.record(
                ts=clock,
                case_id=case.case_id,
                stage=Stage.OUTCOME,
                reason=trace.stop_reason,
                amount_paise=case.amount_at_risk_paise if self_recovers else 0,
                outcome="recovered_self" if self_recovers else "not_recovered",
            )
            self.store.save_case(case)
            return trace

        intervention_landed = False

        for _ in range(MAX_ITERATIONS):
            diagnosis = self.diagnoser.diagnose(case)
            trace.diagnosed_cause = diagnosis.root_cause
            trace.diagnosis_confidence = diagnosis.confidence
            trace.diagnosis_method = diagnosis.method
            self.ledger.record(
                ts=clock,
                case_id=case.case_id,
                stage=Stage.DIAGNOSE,
                reason=diagnosis.rationale,
                outcome=diagnosis.root_cause.value,
                detail={
                    "confidence": diagnosis.confidence,
                    "method": diagnosis.method,
                },
            )

            decision = policy.decide(case, diagnosis, clock)
            self.ledger.record(
                ts=clock,
                case_id=case.case_id,
                stage=Stage.DECIDE,
                reason=decision.reason,
                policy_version=decision.policy_version,
                action_type=decision.intervention.type.value,
                channel=decision.intervention.channel.value,
                amount_paise=decision.intervention.amount_paise,
            )

            if decision.is_stop:
                case.stopped = True
                trace.stop_reason = decision.reason
                self.ledger.record(
                    ts=clock,
                    case_id=case.case_id,
                    stage=Stage.STOP,
                    reason=decision.reason,
                    policy_version=decision.policy_version,
                    outcome="stopped",
                )
                break

            gate, clock, deferrals = self._gate_with_deferral(case, decision, clock)
            trace.deferrals += deferrals

            if not gate.allowed:
                trace.refusals.append(
                    gate.refusal_code.value if gate.refusal_code else "unknown"
                )
                case.stopped = True
                trace.stop_reason = gate.reason
                break

            attempt_no = case.attempts_made + 1
            key = idempotency_key(case, attempt_no)
            if self.ledger.has_fired(key):
                # Idempotency: this exact attempt already executed in a prior run.
                break

            result = self.executor.execute(case, decision, attempt_no)
            self.ledger.record(
                ts=clock,
                case_id=case.case_id,
                stage=Stage.EXECUTE,
                reason=(
                    f"Fired {decision.intervention.type.value} via "
                    f"{decision.intervention.channel.value}."
                ),
                mandate_id=self.mandate.mandate_id,
                policy_version=decision.policy_version,
                action_type=decision.intervention.type.value,
                channel=decision.intervention.channel.value,
                amount_paise=decision.intervention.amount_paise,
                idempotency_key=key,
                outcome="executed" if result.executed else "execution_failed",
                detail={
                    "razorpay_entity_id": result.razorpay_entity.get("id"),
                    "degraded": result.razorpay_entity.get("degraded", False),
                    "error": result.error,
                },
            )

            if not result.executed:
                case.stopped = True
                trace.stop_reason = f"Execution failed: {result.error}"
                break

            case.attempts_made = attempt_no
            trace.actions_fired += 1
            trace.interventions.append(decision.intervention.type.value)
            trace.action_cost_paise += economics.action_cost_paise(
                decision.intervention.channel
            )
            if decision.intervention.channel is not Channel.NONE:
                trace.contacts += 1

            if self.response.intervention_lands(
                case.case_id, true_cause, decision.intervention.type, attempt_no - 1
            ):
                intervention_landed = True
                break

            clock = clock + timedelta(seconds=max(decision.intervention.delay_seconds, 60))

        recovered = self_recovers or intervention_landed
        case.recovered = recovered
        case.recovered_at = clock if recovered else None
        trace.recovered = recovered
        # Attribution: an intervention only gets credit where self-recovery would
        # not have happened anyway. This is what keeps "incremental" honest.
        if recovered:
            trace.recovered_via = "self" if self_recovers else "intervention"

        self.ledger.record(
            ts=clock,
            case_id=case.case_id,
            stage=Stage.OUTCOME,
            reason=(
                f"Case closed. recovered={recovered} via={trace.recovered_via} "
                f"after {trace.actions_fired} action(s)."
            ),
            amount_paise=case.amount_at_risk_paise if recovered else 0,
            outcome=f"recovered_{trace.recovered_via}" if recovered else "not_recovered",
        )
        self.store.save_case(case)
        return trace

    def _gate_with_deferral(self, case, decision, clock):
        """Run the gate, deferring rather than refusing on a closed contact window."""
        deferrals = 0
        for _ in range(MAX_DEFERRALS + 1):
            spent = self.ledger.spent_in_window(
                case.merchant_id, clock, self.mandate.velocity_window_hours
            )
            contacts = self.ledger.customer_contacts_in_window(
                case.customer_ref, clock, self.mandate.contact_budget_window_hours
            )
            gate = mandate_mod.check(
                self.mandate, case, decision, clock, spent, contacts
            )
            self.ledger.record_gate(clock, case, decision, gate, self.mandate.mandate_id)

            if gate.allowed or gate.refusal_code is not RefusalCode.OUTSIDE_CONTACT_WINDOW:
                return gate, clock, deferrals

            fires_at = clock + timedelta(seconds=decision.intervention.delay_seconds)
            deferred_to = mandate_mod.next_allowed_contact_time(fires_at)
            self.ledger.record(
                ts=clock,
                case_id=case.case_id,
                stage=Stage.DEFER,
                reason=(
                    f"Contact deferred from {fires_at.isoformat()} to "
                    f"{deferred_to.isoformat()} to respect the RBI 08:00-19:00 window."
                ),
                outcome="deferred",
            )
            deferrals += 1
            clock = deferred_to - timedelta(seconds=decision.intervention.delay_seconds)

        return gate, clock, deferrals
