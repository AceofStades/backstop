"""The page is a rendering of the saved metrics, never a second source of truth."""

from __future__ import annotations

import random
import re
from datetime import datetime, timezone

from razor_pay.adapters.base import SeedClient
from razor_pay.adapters.payment_failure import PaymentFailureAdapter
from razor_pay.diagnose import Diagnoser
from razor_pay.execute import SimulatedExecutor
from razor_pay.harness.assign import assign_all
from razor_pay.harness.html_report import render_html
from razor_pay.harness.metrics import compute, sensitivity_sweep
from razor_pay.harness.response_model import ResponseModel
from razor_pay.harness.runner import BatchRunner
from razor_pay.ledger import Ledger
from razor_pay.mandate import Mandate
from razor_pay.store import Store

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _batch(tmp_path, n=120):
    store = Store(tmp_path / "html.db")
    cases = PaymentFailureAdapter().seed(
        n, random.Random(5), NOW, "merch_demo_001", SeedClient()
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
    traces = BatchRunner(
        store=store,
        ledger=Ledger(store, "b1"),
        mandate=mandate,
        diagnoser=Diagnoser(use_llm=False),
        executor=SimulatedExecutor(),
        response=ResponseModel(),
    ).run(cases)
    return store, traces, cases


def test_page_is_a_body_fragment_with_a_stable_title(tmp_path):
    store, traces, cases = _batch(tmp_path)
    m = compute(traces, cases, ResponseModel().describe())
    page = render_html(m, "b1")

    lowered = page.lower()
    for forbidden in ("<!doctype", "<html", "<body"):
        assert forbidden not in lowered
    assert "<title>Recovery Batch Readout</title>" in page
    store.close()


def test_page_leads_with_incremental_and_never_only_gross(tmp_path):
    """The headline on the page must be the same headline as the markdown."""
    store, traces, cases = _batch(tmp_path)
    m = compute(traces, cases, ResponseModel().describe())
    page = render_html(m, "b1")

    assert f"{m['lift_pp']:+.1f}" in page
    lo, hi = m["lift_ci_pp"]
    assert f"{lo:+.1f}" in page and f"{hi:+.1f}" in page
    assert "incremental" in page.lower()
    # Gross appears only as the line that is subtracted from.
    assert "would have come back anyway" in page
    store.close()


def test_page_states_the_verdict_the_interval_supports(tmp_path):
    """A wide interval that crosses zero must not be presented as a result."""
    store, traces, cases = _batch(tmp_path)
    m = compute(traces, cases, ResponseModel().describe())
    page = render_html(m, "b1")

    if m["lift_significant"]:
        assert "clears zero" in page
    else:
        assert "spans zero" in page
    store.close()


def test_page_discloses_degraded_artifacts(tmp_path):
    store, traces, cases = _batch(tmp_path)
    m = compute(traces, cases, ResponseModel().describe(), degraded_artifacts=7)
    page = render_html(m, "b1")

    assert "7 degraded artifact" in page
    assert "never counted as real" in page
    store.close()


def test_page_carries_the_provenance_split(tmp_path):
    """Blurring the evidence tiers is the easiest way to oversell a demo."""
    store, traces, cases = _batch(tmp_path)
    m = compute(traces, cases, ResponseModel().describe())
    page = render_html(m, "b1")

    for tier in ("Real", "Degraded", "Modelled", "Injected"):
        assert f">{tier}</span>" in page
    store.close()


def test_every_element_the_page_opens_it_closes(tmp_path):
    store, traces, cases = _batch(tmp_path)
    m = compute(traces, cases, ResponseModel().describe())
    page = render_html(
        m, "b1", sensitivity=sensitivity_sweep(traces, cases)
    )

    for tag in ("div", "section", "table", "svg", "figure", "tr", "td", "span"):
        opened = len(re.findall(rf"<{tag}[ >]", page))
        closed = len(re.findall(rf"</{tag}>", page))
        assert opened == closed, f"{tag}: {opened} open, {closed} closed"
    store.close()


def test_colours_are_defined_for_both_themes(tmp_path):
    """A token defined only inside a dark block renders one theme on the other's ground."""
    store, traces, cases = _batch(tmp_path)
    page = render_html(compute(traces, cases, ResponseModel().describe()), "b1")

    base = page.split("@media (prefers-color-scheme: dark)")[0]
    dark = page.split(':root[data-theme="dark"]')[1].split("}")[0]
    for token in re.findall(r"(--[a-z-]+):", dark):
        assert f"{token}:" in base, f"{token} has no light-theme definition"
    store.close()
