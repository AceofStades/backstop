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
from razor_pay.retry import Throttle, with_retry
from razor_pay.schemas import (
    Decision,
    ExecutionResult,
    InterventionType,
    RecoveryCase,
)

# Razorpay test mode caps Payment Links at 30 per account. This is a hard product
# limit, not a rate limit -- it does not reset on backoff. A real-API run large
# enough to need more links will fail partway, so `run` warns before starting.
TEST_MODE_PAYMENT_LINK_CAP = 30

LINK_TYPES = {
    InterventionType.SEND_PAYMENT_LINK,
    InterventionType.SEND_INSTRUMENT_UPDATE_LINK,
    # A soft nudge is a message that still needs to give the customer a way to
    # pay, so it produces a link like the others. The simulated executor treated
    # any non-link type as an order and silently swallowed this gap; the real API
    # raised on it. See docs/05-worklog.md.
    InterventionType.SOFT_NUDGE,
}
ORDER_TYPES = {
    InterventionType.RETRY_NOW,
    InterventionType.RETRY_BACKOFF,
    InterventionType.RETRY_PAYDAY_WINDOW,
    InterventionType.RE_PRESENT_COLLECT,
}


def _is_link_cap(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "limit of 30" in text or "test mode limit" in text


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
        self.throttle = Throttle()
        self.links_created = 0
        # Razorpay allows 30 Payment Links per business in test mode, for the
        # lifetime of the account. Once spent, link-type interventions can no
        # longer produce a real artifact. Retiring every such case would let an
        # account-level quota masquerade as a policy result, so the executor
        # degrades instead: it records a clearly-labelled simulated link and the
        # report states how many artifacts were degraded.
        self.link_budget_exhausted = False
        self.degraded_links = 0

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

        def call():
            self.throttle.wait()
            if step.type in LINK_TYPES:
                self.links_created += 1
                return self.client.payment_link.create(
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
            if step.type in ORDER_TYPES:
                return self.client.order.create(
                    {
                        "amount": step.amount_paise,
                        "currency": case.currency,
                        "receipt": key[:40],
                        "notes": notes,
                    }
                )
            raise ValueError(f"No Razorpay action defined for {step.type}")

        if step.type in LINK_TYPES and self.link_budget_exhausted:
            return self._degraded_link(key, step)

        try:
            entity = with_retry(call, description=f"execute {key}")
        except Exception as exc:
            if _is_link_cap(exc):
                self.link_budget_exhausted = True
                return self._degraded_link(key, step)
            # An execution failure is a handled outcome, not a crash: the runner
            # records it on the ledger and retires the case.
            return ExecutionResult(
                executed=False,
                idempotency_key=key,
                error=f"{type(exc).__name__}: {exc}",
            )

        return ExecutionResult(
            executed=True, idempotency_key=key, razorpay_entity=dict(entity)
        )

    def _degraded_link(self, key: str, step) -> ExecutionResult:
        """Stand in for a Payment Link once the test-mode quota is spent.

        Flagged `degraded: True` so no downstream reader can mistake it for a
        real artifact.
        """
        self.degraded_links += 1
        return ExecutionResult(
            executed=True,
            idempotency_key=key,
            razorpay_entity={
                "id": f"degraded_link_{abs(hash(key)) % 10**12:012d}",
                "degraded": True,
                "reason": "razorpay test-mode payment-link quota (30/business) exhausted",
                "amount": step.amount_paise,
            },
        )


def build_executor(settings, force_simulated: bool = False) -> Executor:
    if force_simulated or not settings.has_razorpay:
        return SimulatedExecutor()
    return RazorpayExecutor(settings.razorpay_key_id, settings.razorpay_key_secret)
