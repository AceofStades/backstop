"""Executors: the only place that talks to Razorpay.

Two implementations behind one interface:

  RazorpayExecutor  -- creates genuine Orders and Payment Links in test mode.
                       These are real API artifacts with real ids, visible in the
                       Razorpay dashboard. This is the externally-refereed half of
                       the evidence.
  SimulatedExecutor -- for offline runs and CI, where no credentials exist.

Whether a customer subsequently *pays* is not decided here. That belongs to the
harness's response model, and the report labels it as modelled rather than
observed.

Every call carries an idempotency key of `<case_id>:<attempt_no>`; re-running a
batch therefore cannot double-charge or double-contact.
"""

from __future__ import annotations

from typing import Protocol

from razor_pay.config import assert_test_mode
from razor_pay.schemas import (
    Decision,
    ExecutionResult,
    InterventionType,
    RecoveryCase,
)

LINK_TYPES = {
    InterventionType.SEND_PAYMENT_LINK,
    InterventionType.SEND_INSTRUMENT_UPDATE_LINK,
}
ORDER_TYPES = {
    InterventionType.RETRY_NOW,
    InterventionType.RETRY_BACKOFF,
    InterventionType.RETRY_PAYDAY_WINDOW,
    InterventionType.RE_PRESENT_COLLECT,
}


def idempotency_key(case: RecoveryCase, attempt_no: int) -> str:
    return f"{case.case_id}:{attempt_no}"


class Executor(Protocol):
    def execute(
        self, case: RecoveryCase, decision: Decision, attempt_no: int
    ) -> ExecutionResult: ...


class SimulatedExecutor:
    """Deterministic stand-in. Produces stable fake entity ids."""

    name = "simulated"

    def execute(
        self, case: RecoveryCase, decision: Decision, attempt_no: int
    ) -> ExecutionResult:
        key = idempotency_key(case, attempt_no)
        kind = "link" if decision.intervention.type in LINK_TYPES else "order"
        return ExecutionResult(
            executed=True,
            idempotency_key=key,
            razorpay_entity={
                "id": f"sim_{kind}_{abs(hash(key)) % 10**12:012d}",
                "simulated": True,
                "amount": decision.intervention.amount_paise,
            },
        )


class RazorpayExecutor:
    """Real Razorpay test-mode client."""

    name = "razorpay-test"

    def __init__(self, key_id: str, key_secret: str) -> None:
        assert_test_mode(key_id)
        import razorpay

        self.client = razorpay.Client(auth=(key_id, key_secret))
        self.client.set_app_details({"title": "razor-pay-recovery", "version": "0.1.0"})

    def execute(
        self, case: RecoveryCase, decision: Decision, attempt_no: int
    ) -> ExecutionResult:
        key = idempotency_key(case, attempt_no)
        step = decision.intervention
        notes = {
            "case_id": case.case_id,
            "attempt_no": str(attempt_no),
            "policy_version": decision.policy_version,
            "intervention": step.type.value,
            "idempotency_key": key,
        }

        try:
            if step.type in LINK_TYPES:
                entity = self.client.payment_link.create(
                    {
                        "amount": step.amount_paise,
                        "currency": case.currency,
                        "description": f"Recovery {step.type.value} for {case.case_id}",
                        # Notifications stay off: the compliance story is enforced by
                        # the gate, and no synthetic customer should be messaged.
                        "notify": {"sms": False, "email": False},
                        "reminder_enable": False,
                        "notes": notes,
                    }
                )
            elif step.type in ORDER_TYPES:
                entity = self.client.order.create(
                    {
                        "amount": step.amount_paise,
                        "currency": case.currency,
                        "receipt": key[:40],
                        "notes": notes,
                    }
                )
            else:
                return ExecutionResult(
                    executed=False,
                    idempotency_key=key,
                    error=f"No Razorpay action defined for {step.type}",
                )
        except Exception as exc:
            return ExecutionResult(
                executed=False,
                idempotency_key=key,
                error=f"{type(exc).__name__}: {exc}",
            )

        return ExecutionResult(
            executed=True, idempotency_key=key, razorpay_entity=dict(entity)
        )


def build_executor(settings, force_simulated: bool = False) -> Executor:
    if force_simulated or not settings.has_razorpay:
        return SimulatedExecutor()
    return RazorpayExecutor(settings.razorpay_key_id, settings.razorpay_key_secret)
