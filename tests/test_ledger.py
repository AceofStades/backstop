"""The ledger's append-only guarantee is enforced by the database, not by manners."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from razor_pay.ledger import Ledger, Stage
from razor_pay.store import Store

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ledger(tmp_path):
    store = Store(tmp_path / "t.db")
    lg = Ledger(store, "batch_test")
    lg.record(ts=NOW, case_id="c1", stage=Stage.DETECT, reason="seeded", amount_paise=1000)
    store.commit()
    yield lg
    store.close()


def test_update_is_refused(ledger):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.store.conn.execute("UPDATE ledger SET reason = 'tampered'")


def test_delete_is_refused(ledger):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.store.conn.execute("DELETE FROM ledger")


def test_idempotency_key_cannot_be_reused(ledger):
    ledger.record(
        ts=NOW, case_id="c1", stage=Stage.EXECUTE, reason="fired", idempotency_key="c1:1"
    )
    ledger.store.commit()
    assert ledger.has_fired("c1:1")
    with pytest.raises(sqlite3.IntegrityError):
        ledger.record(
            ts=NOW, case_id="c1", stage=Stage.EXECUTE, reason="again", idempotency_key="c1:1"
        )
        ledger.store.commit()


def test_entries_are_ordered_and_complete(ledger):
    for stage in (Stage.DIAGNOSE, Stage.DECIDE, Stage.OUTCOME):
        ledger.record(ts=NOW, case_id="c1", stage=stage, reason=str(stage))
    ledger.store.commit()
    seqs = [e["seq"] for e in ledger.entries_for_case("c1")]
    assert seqs == sorted(seqs)
    assert len(seqs) == 4


def test_every_entry_carries_a_reason(ledger):
    for entry in ledger.entries_for_case("c1"):
        assert entry["reason"].strip()
