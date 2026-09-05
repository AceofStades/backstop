"""How much of the policy survives being wrong about `BELIEVED_UPLIFT`?

## Why this exists

`economics.BELIEVED_UPLIFT` is the one input to the engine that is asserted
rather than measured. It cannot be measured without production data: nobody
knows P(recovery | cause, intervention) for a merchant that has never run these
interventions. Every expected-value decision the engine makes rests on it.

The move already made for the control baseline applies here. An unmeasurable
parameter does not have to be a soft spot in the argument, provided the
*sensitivity* to it is stated. So instead of defending the numbers, this module
reports which decisions would change if they are wrong, and by how much they
would have to be wrong before anything changes.

Two questions, answered separately:

  1. **Decision stability.** The engine's only use of belief today is refusing
     actions that cannot cover their own cost. For each ladder step there is a
     break-even ticket size: below it, fire; above it, refuse. Scaling the
     believed uplift moves that threshold. A step whose threshold stays below
     every realistic ticket across the whole range is *robust* -- being wrong
     about it changes nothing. A step whose threshold lands inside the ticket
     distribution is *fragile*, and is named.

  2. **Ranking stability.** The engine does not yet rank ladder steps by
     expected value (see docs/07-next-steps.md, item 2). Whether that would be
     worth building depends on whether a ranking computed from these beliefs
     would survive them being wrong. Uniform scaling cannot answer that -- it
     moves every step together and leaves order untouched -- so the beliefs are
     perturbed independently and the top-ranked step is checked for churn.

## What this module may read

Only the engine's own belief table, the playbook, and the cost table. It never
imports `harness/response_model.py`'s UPLIFT. Reading the simulated world's
truth here would turn a sensitivity analysis into a scoring of the engine
against an answer key it is not allowed to see, which is a different claim
entirely. `test_uplift_sensitivity_never_reads_harness_truth` asserts this.
"""

from __future__ import annotations

import random

from razor_pay.economics import (
    MIN_EXPECTED_VALUE_RATIO,
    action_cost_paise,
    believed_uplift,
)
from razor_pay.policy import PLAYBOOK
from razor_pay.schemas import RootCause

# Ticket sizes the analysis is judged against. The seeder draws from a
# right-skewed lognormal roughly spanning Rs 150 - Rs 20,000
# (`adapters/base.sample_amount_paise`); these percentiles stand in for that
# distribution without importing the sampler's RNG.
REFERENCE_TICKETS_PAISE: tuple[int, ...] = (
    15_000,     # Rs 150   -- the floor
    50_000,     # Rs 500
    100_000,    # Rs 1,000 -- near the median
    250_000,    # Rs 2,500
    600_000,    # Rs 6,000
    2_000_000,  # Rs 20,000 -- the ceiling
)

# How wrong the beliefs are allowed to be. Half to double is a wide band for a
# prior estimated from historical recovery data, and deliberately wider than
# anyone would defend in practice -- a conclusion that holds across it is not
# sensitive to the argument about what the true value is.
DEFAULT_SCALES: tuple[float, ...] = (0.5, 0.75, 1.0, 1.5, 2.0)


def breakeven_paise(
    cause: RootCause, step_index: int, scale: float = 1.0
) -> float:
    """Smallest ticket at which this ladder step still covers its own cost.

    ratio = amount * uplift / cost, and the engine fires when
    ratio >= MIN_EXPECTED_VALUE_RATIO, so the threshold is
    MIN * cost / uplift.
    """
    step = PLAYBOOK[cause][step_index]
    uplift = believed_uplift(cause, step.type, step_index) * scale
    cost = action_cost_paise(step.channel)
    if uplift <= 0:
        return float("inf")
    return MIN_EXPECTED_VALUE_RATIO * cost / uplift


def decision_stability(
    tickets: tuple[int, ...] = REFERENCE_TICKETS_PAISE,
    scales: tuple[float, ...] = DEFAULT_SCALES,
) -> list[dict]:
    """Per ladder step: does the fire/refuse decision change across `scales`?

    A step is stable when every reference ticket lands on the same side of the
    break-even threshold at every scale. Instability is not a defect -- it is
    where the belief is actually doing work, and therefore where being wrong
    would cost something.
    """
    rows: list[dict] = []

    for cause, ladder in PLAYBOOK.items():
        for i, step in enumerate(ladder):
            per_scale = {s: breakeven_paise(cause, i, s) for s in scales}
            fires = {
                s: tuple(t >= per_scale[s] for t in tickets) for s in scales
            }
            at_unit = fires[1.0] if 1.0 in fires else next(iter(fires.values()))
            flipping = [
                t
                for j, t in enumerate(tickets)
                if len({fires[s][j] for s in scales}) > 1
            ]
            rows.append(
                {
                    "cause": cause.value,
                    "step_index": i,
                    "intervention": step.type.value,
                    "channel": step.channel.value,
                    "cost_paise": action_cost_paise(step.channel),
                    "believed_uplift": believed_uplift(cause, step.type, i),
                    "breakeven_paise": per_scale.get(1.0, float("nan")),
                    "breakeven_by_scale": {str(s): per_scale[s] for s in scales},
                    "fires_at_unit": sum(at_unit),
                    "n_tickets": len(tickets),
                    "flipping_tickets_paise": flipping,
                    "stable": not flipping,
                }
            )
    return rows


def ranking_stability(
    ticket_paise: int = 100_000,
    sigma: float = 0.4,
    trials: int = 2000,
    seed: int = 0,
) -> list[dict]:
    """Would an expected-value *ranking* of ladder steps survive wrong beliefs?

    Each believed uplift is perturbed independently by a lognormal factor, which
    keeps it positive and multiplicative -- the natural error shape for a
    probability estimated from counts. `sigma` 0.4 puts roughly two thirds of
    draws within a factor of 1.5 of the stated value.

    Only causes with at least two ladder steps can have a ranking, so
    single-step ladders are skipped rather than reported as trivially stable.
    """
    rng = random.Random(seed)
    rows: list[dict] = []

    for cause, ladder in PLAYBOOK.items():
        if len(ladder) < 2:
            continue

        def ev(step_index: int, factors: list[float]) -> float:
            step = ladder[step_index]
            gain = (
                ticket_paise
                * believed_uplift(cause, step.type, step_index)
                * factors[step_index]
            )
            return gain - action_cost_paise(step.channel)

        unit = [1.0] * len(ladder)
        baseline_top = max(range(len(ladder)), key=lambda i: ev(i, unit))

        unchanged = 0
        for _ in range(trials):
            factors = [rng.lognormvariate(0.0, sigma) for _ in ladder]
            if max(range(len(ladder)), key=lambda i: ev(i, factors)) == baseline_top:
                unchanged += 1

        rows.append(
            {
                "cause": cause.value,
                "steps": len(ladder),
                "baseline_top_index": baseline_top,
                "baseline_top": ladder[baseline_top].type.value,
                "stability": unchanged / trials,
            }
        )
    return rows


def _rs(paise: float) -> str:
    if paise == float("inf"):
        return "never"
    return f"Rs {paise / 100:,.0f}"


def render_markdown(
    decisions: list[dict],
    rankings: list[dict],
    tickets: tuple[int, ...] = REFERENCE_TICKETS_PAISE,
    scales: tuple[float, ...] = DEFAULT_SCALES,
) -> str:
    fragile = [r for r in decisions if not r["stable"]]
    lines = [
        "# Sensitivity to the engine's believed uplift",
        "",
        "`economics.BELIEVED_UPLIFT` is asserted, not measured. This asks what",
        "would change if it is wrong, across a deliberately wide band",
        f"({min(scales):g}x to {max(scales):g}x the stated values).",
        "",
        "## Decision stability",
        "",
        f"- Ladder steps analysed: **{len(decisions)}**",
        f"- Stable across the whole band: **{len(decisions) - len(fragile)}**",
        f"- Sensitive to the assumption: **{len(fragile)}**",
        "",
        "A step is *stable* when every reference ticket "
        f"({_rs(min(tickets))} to {_rs(max(tickets))}) lands on the same side of",
        "break-even at every scale -- being wrong about it changes no decision.",
        "",
        "| Cause | Step | Intervention | Channel | Cost | Uplift | Break-even | Stable |",
        "|---|---:|---|---|---:|---:|---:|:--:|",
    ]
    for r in decisions:
        lines.append(
            f"| {r['cause']} | {r['step_index']} | {r['intervention']} | "
            f"{r['channel']} | {_rs(r['cost_paise'])} | "
            f"{r['believed_uplift']:.3f} | {_rs(r['breakeven_paise'])} | "
            f"{'yes' if r['stable'] else '**no**'} |"
        )

    paid = [r for r in fragile if r["channel"] != "none"]
    free_stable = all(
        r["stable"] for r in decisions if r["channel"] == "none"
    )
    lines += ["", "### Where the belief is load-bearing", ""]
    if fragile and len(paid) == len(fragile) and free_stable:
        lines += [
            "Every sensitive step is one that costs money to fire, and every",
            "free step is stable. That is the whole pattern: belief only matters",
            "where there is a cost to weigh it against. A rail-side retry is",
            "close enough to free that no plausible uplift makes it not worth",
            "trying; a WhatsApp message has to earn its place, so the estimate",
            "decides.",
            "",
        ]
    if not fragile:
        lines += [
            "No step changes its fire/refuse decision anywhere in the band. Every",
            "action the engine takes today is one it would still take if the",
            "uplift estimates were wrong by a factor of two in either direction.",
            "That is a claim about the *cost* table as much as the belief table:",
            "channel costs are small next to the tickets, so break-even sits far",
            "below the ticket distribution.",
        ]
    else:
        for r in fragile:
            band = r["breakeven_by_scale"]
            lo = _rs(band[str(max(scales))])
            hi = _rs(band[str(min(scales))])
            lines.append(
                f"- `{r['cause']}` step {r['step_index']} "
                f"({r['intervention']} over {r['channel']}): break-even moves "
                f"{lo} -> {hi} across the band, crossing "
                f"{len(r['flipping_tickets_paise'])} of {r['n_tickets']} "
                f"reference tickets."
            )

    lines += [
        "",
        "## Ranking stability",
        "",
        "The engine does not rank ladder steps by expected value today; it only",
        "refuses steps that cannot cover their cost. This measures whether a",
        "ranking *would* be trustworthy, by perturbing each believed uplift",
        "independently and checking how often the top-ranked step changes.",
        "",
        "| Cause | Steps | Highest-EV step | Unchanged under perturbation |",
        "|---|---:|---|---:|",
    ]
    for r in rankings:
        lines.append(
            f"| {r['cause']} | {r['steps']} | {r['baseline_top']} "
            f"(#{r['baseline_top_index']}) | {r['stability']:.1%} |"
        )

    weakest = min(rankings, key=lambda r: r["stability"]) if rankings else None
    lines += [
        "",
        (
            f"Weakest: `{weakest['cause']}` at {weakest['stability']:.1%}. "
            "A ranking is only worth building where this number is high; where "
            "it is low the ladder order should stay a stated policy choice "
            "rather than a computed one."
            if weakest
            else "No multi-step ladders to rank."
        ),
        "",
        "## What this does not establish",
        "",
        "Sensitivity is not accuracy. A decision that is stable across the band",
        "is stable because the ticket sizes dwarf the action costs, not because",
        "the uplift estimates are correct. The estimates remain unmeasured, and",
        "measuring them needs production data. What this rules out is the",
        "sharper objection -- that the reported result is an artifact of numbers",
        "chosen to produce it.",
        "",
    ]
    return "\n".join(lines)
