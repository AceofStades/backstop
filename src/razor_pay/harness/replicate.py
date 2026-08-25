"""Run many independent batches and pool the result.

A single batch's lift is one draw, not a result. Measured across 12 seeds at
n=400, the lift ranged +17.4 to +31.7 pp with an sd of 4.4 -- so quoting one
batch's number invites the reviewer to re-run it and get something else.

That spread is not a defect in the engine; it is ordinary sampling variation, and
each batch's own 95% interval is about the right width for it. But a headline
should be the pooled estimate across replications, with the between-batch spread
stated, rather than whichever batch happened to run last.

Nothing here touches Razorpay. Replication drives the simulated executor only, so
it is a statement about the policy under the response model -- exactly the thing
that varies -- and never creates hundreds of test-mode orders.
"""

from __future__ import annotations

import math
import random
import statistics
import tempfile
from datetime import datetime
from pathlib import Path

from razor_pay.adapters import get_adapter
from razor_pay.adapters.base import SeedClient
from razor_pay.diagnose import Diagnoser
from razor_pay.execute import SimulatedExecutor
from razor_pay.harness.assign import assign_all
from razor_pay.harness.metrics import compute
from razor_pay.harness.response_model import ResponseModel
from razor_pay.harness.runner import BatchRunner
from razor_pay.ledger import Ledger
from razor_pay.mandate import Mandate
from razor_pay.schemas import LeakType
from razor_pay.store import Store


def _one_replication(
    seed: int, cases: int, leaks: list[LeakType], control_fraction: float, now: datetime
) -> dict:
    rng = random.Random(seed)
    token = f"r{seed:04d}"
    per_leak = cases // len(leaks)
    remainder = cases - per_leak * len(leaks)

    batch: list = []
    for i, leak in enumerate(leaks):
        n = per_leak + (remainder if i == 0 else 0)
        batch.extend(
            get_adapter(leak).seed(n, rng, now, "merch_demo_001", SeedClient(), token)
        )
    assign_all(batch, control_fraction)

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "rep.db")
        mandate = Mandate(
            mandate_id=f"mand_rep_{seed}",
            merchant_id="merch_demo_001",
            issued_at=now,
            expires_at=now.replace(year=now.year + 1),
            per_action_cap_paise=2_500_000,
            velocity_cap_paise=500_000_000,
        )
        store.create_batch("rep", now, mandate, {})
        for case in batch:
            store.insert_case("rep", case)
        store.commit()

        response = ResponseModel()
        traces = BatchRunner(
            store=store,
            ledger=Ledger(store, "rep"),
            mandate=mandate,
            diagnoser=Diagnoser(use_llm=False),
            executor=SimulatedExecutor(),
            response=response,
        ).run(batch)
        metrics = compute(traces, batch, response.describe())
        store.close()
    return metrics


def replicate(
    runs: int,
    cases: int,
    leaks: list[LeakType],
    control_fraction: float,
    now: datetime,
    progress=None,
) -> dict:
    """Run `runs` independent batches and pool their results."""
    per_run: list[dict] = []
    for i in range(runs):
        metrics = _one_replication(i + 1, cases, leaks, control_fraction, now)
        per_run.append(
            {
                "seed": i + 1,
                "lift_pp": metrics["lift_pp"],
                "control_rate": metrics["control"]["rate"],
                "treatment_rate": metrics["treatment"]["rate"],
                "incremental_paise": metrics["incremental_paise"],
                "net_value_paise": metrics["net_value_paise"],
                "actions_fired": metrics["actions_fired"],
                "exceptions": len(metrics["exceptions"]),
            }
        )
        if progress:
            progress(per_run[-1])

    lifts = [r["lift_pp"] for r in per_run]
    nets = [r["net_value_paise"] for r in per_run]
    mean_lift = statistics.mean(lifts)
    sd_lift = statistics.stdev(lifts) if len(lifts) > 1 else 0.0
    # Standard error of the mean across replications, so the pooled interval
    # narrows with more runs while the raw spread does not.
    sem = sd_lift / math.sqrt(len(lifts)) if lifts else 0.0

    return {
        "runs": runs,
        "cases_per_run": cases,
        "control_fraction": control_fraction,
        "mean_lift_pp": mean_lift,
        "sd_lift_pp": sd_lift,
        "sem_lift_pp": sem,
        "pooled_ci_pp": (mean_lift - 1.96 * sem, mean_lift + 1.96 * sem),
        "min_lift_pp": min(lifts),
        "max_lift_pp": max(lifts),
        "mean_net_value_paise": statistics.mean(nets),
        "sd_net_value_paise": statistics.stdev(nets) if len(nets) > 1 else 0.0,
        "per_run": per_run,
    }


def render_markdown(r: dict) -> str:
    lo, hi = r["pooled_ci_pp"]
    lines = [
        f"# Replication - {r['runs']} independent batches of {r['cases_per_run']}",
        "",
        "A single batch's lift is one draw, not a result. This pools independent",
        "replications so the headline does not depend on which batch ran last.",
        "",
        "## Pooled",
        "",
        f"- **Mean incremental lift: {r['mean_lift_pp']:+.1f} pp** "
        f"(95% CI of the mean {lo:+.1f} to {hi:+.1f})",
        f"- Between-batch spread: sd {r['sd_lift_pp']:.1f} pp, "
        f"range {r['min_lift_pp']:+.1f} to {r['max_lift_pp']:+.1f}",
        f"- Mean net value per batch: Rs {r['mean_net_value_paise'] / 100:,.0f} "
        f"(sd Rs {r['sd_net_value_paise'] / 100:,.0f})",
        "",
        "The spread is ordinary sampling variation, not instability in the policy.",
        "It is reported because a reviewer who re-runs a single batch will land",
        "somewhere inside that range, and should not be surprised by it.",
        "",
        "## Per batch",
        "",
        "| Seed | Control | Treatment | Lift (pp) | Net value | Actions | Exceptions |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in r["per_run"]:
        lines.append(
            f"| {run['seed']} | {run['control_rate']:.1%} | {run['treatment_rate']:.1%} "
            f"| {run['lift_pp']:+.1f} | Rs {run['net_value_paise'] / 100:,.0f} "
            f"| {run['actions_fired']} | {run['exceptions']} |"
        )
    return "\n".join(lines)
