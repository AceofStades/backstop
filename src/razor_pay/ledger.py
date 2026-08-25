"""Append-only audit trail.

Every entry answers the five questions a reviewer will ask about any money that
moved: which actor, under which mandate, under which policy version, what was
checked before it fired, and what happened.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from razor_pay.schemas import Decision, GateResult, RecoveryCase
from razor_pay.store import Store


class Stage(StrEnum):
    DETECT = "detect"
    DIAGNOSE = "diagnose"
    DECIDE = "decide"
    GATE = "gate"
    EXECUTE = "execute"
    DEFER = "defer"
    OUTCOME = "outcome"
    STOP = "stop"


class Ledger:
    def __init__(self, store: Store, batch_id: str, actor: str = "recovery-agent/1") -> None:
        self.store = store
        self.batch_id = batch_id
        self.actor = actor

    def record(
        self,
        *,
        ts: datetime,
        case_id: str,
        stage: Stage,
        reason: str,
        mandate_id: str | None = None,
        policy_version: str | None = None,
        action_type: str | None = None,
        channel: str | None = None,
        amount_paise: int | None = None,
        checks_performed: list[str] | None = None,
        allowed: bool | None = None,
        refusal_code: str | None = None,
        idempotency_key: str | None = None,
        outcome: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.store.conn.execute(
            "INSERT INTO ledger (ts, batch_id, case_id, actor, stage, mandate_id, "
            "policy_version, action_type, channel, amount_paise, checks_performed, "
            "allowed, refusal_code, reason, idempotency_key, outcome, detail_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts.isoformat(),
                self.batch_id,
                case_id,
                self.actor,
                stage.value,
                mandate_id,
                policy_version,
                action_type,
                channel,
                amount_paise,
                json.dumps(checks_performed or []),
                None if allowed is None else int(allowed),
                refusal_code,
                reason,
                idempotency_key,
                outcome,
                json.dumps(detail or {}, default=str),
            ),
        )

    def record_gate(
        self,
        ts: datetime,
        case: RecoveryCase,
        decision: Decision,
        gate: GateResult,
        mandate_id: str,
    ) -> None:
        self.record(
            ts=ts,
            case_id=case.case_id,
            stage=Stage.GATE,
            reason=gate.reason or "Action authorised by mandate gate.",
            mandate_id=mandate_id,
            policy_version=decision.policy_version,
            action_type=decision.intervention.type.value,
            channel=decision.intervention.channel.value,
            amount_paise=decision.intervention.amount_paise,
            checks_performed=gate.checks_performed,
            allowed=gate.allowed,
            refusal_code=gate.refusal_code.value if gate.refusal_code else None,
        )

    # ---- queries ----------------------------------------------------------

    def spent_in_window(
        self, merchant_scope: str, now: datetime, window_hours: int
    ) -> int:
        """Total executed money-moving volume inside the velocity window."""
        since = (now - timedelta(hours=window_hours)).isoformat()
        cur = self.store.conn.execute(
            "SELECT COALESCE(SUM(amount_paise), 0) AS total FROM ledger "
            "WHERE batch_id = ? AND stage = ? AND ts >= ? AND amount_paise IS NOT NULL",
            (self.batch_id, Stage.EXECUTE.value, since),
        )
        return int(cur.fetchone()["total"])

    def customer_contacts_in_window(
        self, customer_ref: str, now: datetime, window_hours: int
    ) -> int:
        """Executed customer-contacting actions for one customer, across all cases.

        Counted from the ledger rather than an in-memory tally, so the budget
        survives a restart and spans every case belonging to that customer.

        Deliberately NOT scoped to this batch. A customer does not experience
        batch boundaries -- being contacted four times yesterday and four times
        today is eight contacts to them, however the runs were organised. The
        window is wall-clock, which is what the regulator's view is too.
        """
        since = (now - timedelta(hours=window_hours)).isoformat()
        cur = self.store.conn.execute(
            "SELECT COUNT(*) AS n FROM ledger l "
            "JOIN cases c ON c.case_id = l.case_id "
            "WHERE l.stage = ? AND l.ts >= ? "
            "AND l.channel IS NOT NULL AND l.channel != 'none' "
            "AND json_extract(c.case_json, '$.customer_ref') = ?",
            (Stage.EXECUTE.value, since, customer_ref),
        )
        return int(cur.fetchone()["n"])

    def has_fired(self, idempotency_key: str) -> bool:
        cur = self.store.conn.execute(
            "SELECT 1 FROM ledger WHERE idempotency_key = ? LIMIT 1",
            (idempotency_key,),
        )
        return cur.fetchone() is not None

    def refusals_by_code(self) -> dict[str, int]:
        cur = self.store.conn.execute(
            "SELECT refusal_code, COUNT(*) AS n FROM ledger "
            "WHERE batch_id = ? AND allowed = 0 AND refusal_code IS NOT NULL "
            "GROUP BY refusal_code ORDER BY n DESC",
            (self.batch_id,),
        )
        return {r["refusal_code"]: r["n"] for r in cur.fetchall()}

    def entries_for_case(self, case_id: str) -> list[dict]:
        cur = self.store.conn.execute(
            "SELECT * FROM ledger WHERE case_id = ? ORDER BY seq", (case_id,)
        )
        return [dict(r) for r in cur.fetchall()]

    def resolve_case_ids(self, fragment: str) -> list[str]:
        """Case ids matching `fragment`, newest batch first.

        Case ids carry a batch token, so `pf_0000` should still find
        `pf_134512_0000` when it is unambiguous.

        Note `_` is a single-character wildcard in SQL LIKE, so a fragment like
        `pf_0000` must be escaped before it is used as a literal -- otherwise it
        silently matches nothing.
        """

        def escape(text: str) -> str:
            return text.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")

        patterns = [f"%{escape(fragment)}%"]
        # `prefix_NNNN` should also find `prefix_<token>_NNNN`.
        head, sep, tail = fragment.rpartition("_")
        if sep and head and tail:
            patterns.append(f"{escape(head)}\\_%\\_{escape(tail)}")

        # Prefer this ledger's own batch, so `audit pf_0000` resolves to the
        # batch under examination rather than reporting ambiguity across every
        # batch that ever ran.
        for scope_to_batch in (True, False):
            for pattern in patterns:
                sql = (
                    "SELECT DISTINCT case_id FROM ledger WHERE case_id LIKE ? "
                    "ESCAPE '\\'"
                )
                args: list[str] = [pattern]
                if scope_to_batch:
                    sql += " AND batch_id = ?"
                    args.append(self.batch_id)
                cur = self.store.conn.execute(sql + " ORDER BY case_id DESC", args)
                found = [r["case_id"] for r in cur.fetchall()]
                if found:
                    return found
        return []

    def count(self) -> int:
        cur = self.store.conn.execute(
            "SELECT COUNT(*) AS n FROM ledger WHERE batch_id = ?", (self.batch_id,)
        )
        return int(cur.fetchone()["n"])
