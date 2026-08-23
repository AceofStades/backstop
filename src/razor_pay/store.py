"""SQLite persistence: mutable case state plus a genuinely append-only ledger.

The ledger's immutability is enforced by database triggers, not by convention.
`UPDATE` and `DELETE` against it raise. Corrections are new rows. This is worth
demonstrating live: it is the difference between an audit trail and a log file.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from razor_pay.schemas import Arm, RecoveryCase

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id      TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    mandate_json  TEXT NOT NULL,
    params_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    case_id       TEXT PRIMARY KEY,
    batch_id      TEXT NOT NULL,
    arm           TEXT NOT NULL,
    leak_type     TEXT NOT NULL,
    amount_paise  INTEGER NOT NULL,
    attempts_made INTEGER NOT NULL DEFAULT 0,
    stopped       INTEGER NOT NULL DEFAULT 0,
    recovered     INTEGER NOT NULL DEFAULT 0,
    case_json     TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batches (batch_id)
);

CREATE INDEX IF NOT EXISTS idx_cases_batch ON cases (batch_id);

CREATE TABLE IF NOT EXISTS ledger (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    batch_id         TEXT NOT NULL,
    case_id          TEXT NOT NULL,
    actor            TEXT NOT NULL,
    stage            TEXT NOT NULL,
    mandate_id       TEXT,
    policy_version   TEXT,
    action_type      TEXT,
    channel          TEXT,
    amount_paise     INTEGER,
    checks_performed TEXT,
    allowed          INTEGER,
    refusal_code     TEXT,
    reason           TEXT NOT NULL,
    idempotency_key  TEXT,
    outcome          TEXT,
    detail_json      TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_case ON ledger (case_id);
CREATE INDEX IF NOT EXISTS idx_ledger_batch ON ledger (batch_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_idem
    ON ledger (idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Append-only enforcement.
CREATE TRIGGER IF NOT EXISTS ledger_no_update
BEFORE UPDATE ON ledger
BEGIN
    SELECT RAISE(ABORT, 'ledger is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS ledger_no_delete
BEFORE DELETE ON ledger
BEGIN
    SELECT RAISE(ABORT, 'ledger is append-only: DELETE is forbidden');
END;
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- batches ----------------------------------------------------------

    def create_batch(
        self, batch_id: str, created_at: datetime, mandate: Any, params: dict
    ) -> None:
        self.conn.execute(
            "INSERT INTO batches (batch_id, created_at, mandate_json, params_json) "
            "VALUES (?, ?, ?, ?)",
            (
                batch_id,
                created_at.isoformat(),
                mandate.model_dump_json(),
                json.dumps(params, default=str),
            ),
        )
        self.conn.commit()

    def get_batch(self, batch_id: str) -> sqlite3.Row | None:
        cur = self.conn.execute(
            "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
        )
        return cur.fetchone()

    def latest_batch_id(self) -> str | None:
        cur = self.conn.execute(
            "SELECT batch_id FROM batches ORDER BY created_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row["batch_id"] if row else None

    # ---- cases ------------------------------------------------------------

    def insert_case(self, batch_id: str, case: RecoveryCase) -> None:
        self.conn.execute(
            "INSERT INTO cases (case_id, batch_id, arm, leak_type, amount_paise, "
            "attempts_made, stopped, recovered, case_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case.case_id,
                batch_id,
                case.arm.value,
                case.leak_type.value,
                case.amount_at_risk_paise,
                case.attempts_made,
                int(case.stopped),
                int(case.recovered),
                case.model_dump_json(),
            ),
        )

    def save_case(self, case: RecoveryCase) -> None:
        self.conn.execute(
            "UPDATE cases SET attempts_made = ?, stopped = ?, recovered = ?, "
            "case_json = ? WHERE case_id = ?",
            (
                case.attempts_made,
                int(case.stopped),
                int(case.recovered),
                case.model_dump_json(),
                case.case_id,
            ),
        )

    def load_cases(self, batch_id: str, arm: Arm | None = None) -> list[RecoveryCase]:
        sql = "SELECT case_json FROM cases WHERE batch_id = ?"
        args: list[Any] = [batch_id]
        if arm is not None:
            sql += " AND arm = ?"
            args.append(arm.value)
        sql += " ORDER BY case_id"
        cur = self.conn.execute(sql, args)
        return [RecoveryCase.model_validate_json(r["case_json"]) for r in cur.fetchall()]

    def commit(self) -> None:
        self.conn.commit()
