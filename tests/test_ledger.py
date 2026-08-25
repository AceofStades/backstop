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


def _seed_case(store, batch, case_id, customer="cust_x"):
    from razor_pay.mandate import Mandate
    from razor_pay.schemas import FailureEvidence, LeakType, RecoveryCase

    if store.get_batch(batch) is None:
        store.create_batch(
            batch,
            NOW,
            Mandate(
                mandate_id="m",
                merchant_id="m1",
                issued_at=NOW,
                expires_at=NOW.replace(year=2027),
                per_action_cap_paise=100,
                velocity_cap_paise=100,
            ),
            {},
        )
    store.insert_case(
        batch,
        RecoveryCase(
            case_id=case_id,
            leak_type=LeakType.PAYMENT_FAILURE,
            merchant_id="m1",
            customer_ref=customer,
            amount_at_risk_paise=1000,
            failure_evidence=FailureEvidence(),
            detected_at=NOW,
        ),
    )
    Ledger(store, batch).record(
        ts=NOW, case_id=case_id, stage=Stage.DETECT, reason="seeded"
    )
    store.commit()


def test_partial_case_id_resolves_despite_underscore_wildcard(tmp_path):
    """`_` is a single-char wildcard in SQL LIKE and must be escaped.

    Unescaped, `pf_0000` silently matches nothing instead of finding
    `pf_134512_0000`.
    """
    store = Store(tmp_path / "resolve.db")
    _seed_case(store, "b1", "pf_134512_0000")
    matches = Ledger(store, "b1").resolve_case_ids("pf_0000")
    assert matches == ["pf_134512_0000"]
    store.close()


def test_case_id_resolution_prefers_the_current_batch(tmp_path):
    """Two batches carry the same logical case; audit should pick the current one."""
    store = Store(tmp_path / "prefer.db")
    _seed_case(store, "b1", "pf_111111_0000")
    _seed_case(store, "b2", "pf_222222_0000")
    assert Ledger(store, "b2").resolve_case_ids("pf_0000") == ["pf_222222_0000"]
    assert Ledger(store, "b1").resolve_case_ids("pf_0000") == ["pf_111111_0000"]
    store.close()


def test_unknown_case_id_resolves_to_nothing(tmp_path):
    store = Store(tmp_path / "none.db")
    _seed_case(store, "b1", "pf_134512_0000")
    assert Ledger(store, "b1").resolve_case_ids("zz_9999") == []
    store.close()
