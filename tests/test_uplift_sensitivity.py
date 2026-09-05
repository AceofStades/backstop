"""The engine's beliefs are asserted, so the sensitivity to them must be stated."""

from __future__ import annotations

import ast
import inspect

from razor_pay import economics
from razor_pay.harness import uplift_sensitivity as us
from razor_pay.policy import PLAYBOOK
from razor_pay.schemas import Channel, RootCause


def test_uplift_sensitivity_never_reads_harness_truth():
    """The analysis may read the engine's belief; never the simulation's answer key.

    Importing `response_model.UPLIFT` here would silently turn a sensitivity
    analysis into a scoring against ground truth, which is a different and much
    weaker claim. Guarded at the source level because an import is exactly the
    accident this prevents.
    """
    tree = ast.parse(inspect.getsource(us))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any("response_model" in name for name in imported), imported
    assert not hasattr(us, "UPLIFT")
    assert us.believed_uplift is economics.believed_uplift


def test_breakeven_is_the_point_where_expected_value_covers_cost():
    for cause, ladder in PLAYBOOK.items():
        for i, step in enumerate(ladder):
            threshold = us.breakeven_paise(cause, i)
            if threshold == float("inf"):
                continue
            _, _, ratio = economics.expected_value_paise(
                int(threshold) + 1, cause, step.type, step.channel, i
            )
            assert ratio >= economics.MIN_EXPECTED_VALUE_RATIO


def test_scaling_belief_moves_breakeven_inversely():
    """Twice the believed uplift halves the ticket needed to justify the action."""
    cause = RootCause.INSTRUMENT_INVALID
    assert us.breakeven_paise(cause, 0, 2.0) == us.breakeven_paise(cause, 0, 1.0) / 2


def test_free_actions_are_never_sensitive_to_the_belief():
    """A rail-side retry costs almost nothing, so no plausible uplift refuses it.

    This is the load-bearing half of the sensitivity story: the belief only
    decides anything where there is a cost to weigh it against.
    """
    rows = us.decision_stability()
    free = [r for r in rows if r["channel"] == Channel.NONE.value]
    assert free, "expected at least one no-contact ladder step"
    assert all(r["stable"] for r in free)


def test_every_ladder_step_is_analysed():
    rows = us.decision_stability()
    assert len(rows) == sum(len(ladder) for ladder in PLAYBOOK.values())


def test_ranking_stability_skips_ladders_too_short_to_rank():
    rows = us.ranking_stability(trials=50)
    ranked = {r["cause"] for r in rows}
    for cause, ladder in PLAYBOOK.items():
        assert (cause.value in ranked) == (len(ladder) >= 2)
    assert all(0.0 <= r["stability"] <= 1.0 for r in rows)


def test_report_names_the_steps_that_are_sensitive():
    rows = us.decision_stability()
    md = us.render_markdown(rows, us.ranking_stability(trials=50))
    for r in rows:
        if not r["stable"]:
            assert r["cause"] in md
    assert "not accuracy" in md
