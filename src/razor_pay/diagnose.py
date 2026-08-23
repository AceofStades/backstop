"""Diagnoser: failure evidence -> root cause.

Deterministic first. The LLM is a *fallback* for evidence that carries no
recognised error code, and its output is constrained to the RootCause enum -- it
proposes a classification, never an action.

Low-confidence results are not acted on. They route to the exception list, which
is reported honestly rather than hidden.

The diagnoser never reads `case.injected_cause`; that field exists only so the
harness can score this module afterwards.
"""

from __future__ import annotations

import json
import os
import re

from razor_pay import taxonomy
from razor_pay.schemas import Diagnosis, LeakType, RecoveryCase, RootCause

DETERMINISTIC_CONFIDENCE = 0.97
LLM_MAX_CONFIDENCE = 0.85

_VALID = {c.value for c in RootCause}

SYSTEM_PROMPT = """\
You classify failed Indian payment attempts into a fixed root-cause taxonomy.

Allowed root_cause values (use exactly one, verbatim):
- insufficient_funds: the payer's account lacked balance
- issuer_downtime: the payer's bank or a partner bank was unavailable
- gateway_error: a fault on the gateway/processor side
- instrument_invalid: the instrument cannot work at all (dead VPA, expired card)
- customer_cancelled: the payer deliberately aborted
- collect_expired: the payer did not act before the collect request lapsed
- account_mismatch: a different account than the one registered was used
- declined_unspecified: the debit was refused with no specific reason given
- abandoned_no_attempt: no charge was ever attempted
- unknown: the evidence does not support any of the above

Reply with ONLY a JSON object:
{"root_cause": "<value>", "confidence": <0.0-1.0>, "rationale": "<one sentence>"}

Be conservative. If the evidence is thin or ambiguous, return "unknown" with a
low confidence. A wrong confident answer costs a real customer a wrong action.
"""


class Diagnoser:
    def __init__(self, use_llm: bool = True, model: str | None = None) -> None:
        self.model = model or os.getenv("DIAGNOSER_MODEL", "claude-sonnet-5")
        self._client = None
        self.use_llm = use_llm and bool(os.getenv("ANTHROPIC_API_KEY"))
        self.llm_calls = 0

    def _client_or_none(self):
        if self._client is None and self.use_llm:
            try:
                from anthropic import Anthropic

                self._client = Anthropic()
            except Exception:
                self.use_llm = False
                return None
        return self._client

    def diagnose(self, case: RecoveryCase) -> Diagnosis:
        ev = case.failure_evidence

        # 1. Deterministic: a recognised error code is authoritative.
        profile = taxonomy.lookup(ev.error_code)
        if profile is not None:
            return Diagnosis(
                root_cause=profile.root_cause,
                confidence=DETERMINISTIC_CONFIDENCE,
                method="deterministic",
                rationale=(
                    f"Razorpay error_code '{ev.error_code}' maps directly to "
                    f"'{profile.root_cause}'. {profile.note}"
                ),
            )

        # 2. Structural: an abandonment has no charge to interpret.
        if case.leak_type is LeakType.CHECKOUT_ABANDONMENT and not ev.error_code:
            return Diagnosis(
                root_cause=RootCause.ABANDONED_NO_ATTEMPT,
                confidence=DETERMINISTIC_CONFIDENCE,
                method="deterministic",
                rationale="Order created with no payment attempt recorded.",
            )

        # 3. LLM fallback for unrecognised or free-text evidence.
        if self.use_llm and (ev.error_description or ev.error_reason or ev.error_code):
            result = self._classify_with_llm(case)
            if result is not None:
                return result

        return Diagnosis(
            root_cause=RootCause.UNKNOWN,
            confidence=0.0,
            method="fallback",
            rationale=(
                "No recognised error code and no usable free-text evidence. "
                "Routed to the exception list."
            ),
        )

    def _classify_with_llm(self, case: RecoveryCase) -> Diagnosis | None:
        client = self._client_or_none()
        if client is None:
            return None

        ev = case.failure_evidence
        evidence = json.dumps(
            {
                "leak_type": case.leak_type.value,
                "method": ev.method,
                "error_code": ev.error_code,
                "error_description": ev.error_description,
                "error_reason": ev.error_reason,
                "error_source": ev.error_source,
                "error_step": ev.error_step,
            },
            indent=2,
        )

        try:
            self.llm_calls += 1
            resp = client.messages.create(
                model=self.model,
                max_tokens=300,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"Evidence:\n{evidence}"}],
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", "") == "text"
            )
        except Exception as exc:  # network, auth, rate limit -- degrade, never crash
            return Diagnosis(
                root_cause=RootCause.UNKNOWN,
                confidence=0.0,
                method="fallback",
                rationale=f"LLM classification unavailable ({type(exc).__name__}).",
            )

        parsed = _extract_json(text)
        if not parsed:
            return None

        cause = str(parsed.get("root_cause", "")).strip().lower()
        if cause not in _VALID:
            return None

        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        return Diagnosis(
            root_cause=RootCause(cause),
            # Cap LLM confidence below the deterministic path: a model's stated
            # certainty is not the same evidence class as a documented error code.
            confidence=max(0.0, min(confidence, LLM_MAX_CONFIDENCE)),
            method="llm",
            rationale=str(parsed.get("rationale", ""))[:300],
        )


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
