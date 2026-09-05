"""Metrics and the report.

The headline is **incremental** recovery: treatment rate minus control rate, with
a 95% interval. Gross recovery is reported too, but labelled as the number that
overstates the agent's contribution, because some of those cases would have
recovered with nobody doing anything.

Everything the engine could not resolve is listed, not summarised away.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from razor_pay import economics
from razor_pay.harness.runner import CaseTrace
from razor_pay.schemas import Arm, RecoveryCase, RootCause


@dataclass
class ArmStats:
    n: int = 0
    recovered: int = 0
    at_risk_paise: int = 0
    recovered_paise: int = 0

    @property
    def rate(self) -> float:
        return self.recovered / self.n if self.n else 0.0

    @property
    def value_rate(self) -> float:
        return self.recovered_paise / self.at_risk_paise if self.at_risk_paise else 0.0


def _two_proportion_ci(p1: float, n1: int, p0: float, n0: int) -> tuple[float, float]:
    """95% interval for (p1 - p0). Normal approximation; adequate at n>=100."""
    if n1 == 0 or n0 == 0:
        return (0.0, 0.0)
    se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    diff = p1 - p0
    return (diff - 1.96 * se, diff + 1.96 * se)


def compute(
    traces: list[CaseTrace],
    cases: list[RecoveryCase],
    response_params: dict,
    degraded_artifacts: int = 0,
) -> dict:
    by_id = {c.case_id: c for c in cases}
    arms = {Arm.TREATMENT: ArmStats(), Arm.CONTROL: ArmStats()}

    actions_fired = 0
    action_cost_paise = 0
    contacts = 0
    deferrals = 0
    refusals: dict[str, int] = {}
    diag_correct = 0
    diag_scored = 0
    diag_methods: dict[str, int] = {}
    # truth -> predicted -> count. A single accuracy figure hides which causes
    # get confused with which, and that is the actionable part: it locates the
    # error in a specific slice of the diagnoser rather than spreading it.
    diag_confusion: dict[str, dict[str, int]] = {}
    diag_by_method: dict[str, dict[str, int]] = {}
    exceptions: list[dict] = []

    for t in traces:
        stats = arms[t.arm]
        stats.n += 1
        stats.at_risk_paise += t.amount_paise
        if t.recovered:
            stats.recovered += 1
            stats.recovered_paise += t.amount_paise

        actions_fired += t.actions_fired
        action_cost_paise += t.action_cost_paise
        contacts += t.contacts
        deferrals += t.deferrals
        for code in t.refusals:
            refusals[code] = refusals.get(code, 0) + 1

        if t.arm is Arm.TREATMENT and t.diagnosed_cause is not None:
            diag_methods[t.diagnosis_method] = diag_methods.get(t.diagnosis_method, 0) + 1
            truth = by_id[t.case_id].injected_cause
            if truth is not None:
                diag_scored += 1
                hit = truth == t.diagnosed_cause
                if hit:
                    diag_correct += 1
                row = diag_confusion.setdefault(truth.value, {})
                row[t.diagnosed_cause.value] = row.get(t.diagnosed_cause.value, 0) + 1
                by_m = diag_by_method.setdefault(
                    t.diagnosis_method, {"scored": 0, "correct": 0}
                )
                by_m["scored"] += 1
                by_m["correct"] += int(hit)

            unresolved = (
                t.diagnosed_cause is RootCause.UNKNOWN
                or t.actions_fired == 0
                or bool(t.refusals)
            )
            if unresolved:
                exceptions.append(
                    {
                        "case_id": t.case_id,
                        "amount_paise": t.amount_paise,
                        "diagnosed": t.diagnosed_cause.value,
                        "confidence": round(t.diagnosis_confidence, 2),
                        "method": t.diagnosis_method,
                        "actions_fired": t.actions_fired,
                        "refusals": t.refusals,
                        "why": t.stop_reason,
                    }
                )

    tr, ct = arms[Arm.TREATMENT], arms[Arm.CONTROL]
    lift = tr.rate - ct.rate
    lo, hi = _two_proportion_ci(tr.rate, tr.n, ct.rate, ct.n)

    # Value-weighted: what the control arm's rate would have recovered from the
    # treatment arm's money, subtracted from what the treatment arm actually got.
    counterfactual_paise = int(ct.value_rate * tr.at_risk_paise)
    incremental_paise = tr.recovered_paise - counterfactual_paise

    attributed = sum(
        1 for t in traces if t.arm is Arm.TREATMENT and t.recovered_via == "intervention"
    )

    return {
        "n_total": tr.n + ct.n,
        "treatment": {
            "n": tr.n,
            "recovered": tr.recovered,
            "rate": tr.rate,
            "at_risk_paise": tr.at_risk_paise,
            "recovered_paise": tr.recovered_paise,
        },
        "control": {
            "n": ct.n,
            "recovered": ct.recovered,
            "rate": ct.rate,
            "at_risk_paise": ct.at_risk_paise,
            "recovered_paise": ct.recovered_paise,
        },
        "lift_pp": lift * 100,
        "lift_ci_pp": (lo * 100, hi * 100),
        "lift_significant": lo > 0,
        "gross_recovered_paise": tr.recovered_paise,
        "counterfactual_paise": counterfactual_paise,
        "incremental_paise": incremental_paise,
        "attributed_to_intervention": attributed,
        "actions_fired": actions_fired,
        "degraded_artifacts": degraded_artifacts,
        "contacts": contacts,
        "action_cost_paise": action_cost_paise,
        # The metric that matters: money kept after paying for the actions that
        # kept it. The actions-per-recovery ratio below is reported alongside it
        # but is NOT an optimisation target -- a ratio improves when its
        # denominator is cut, which is not the same as making money.
        "net_value_paise": incremental_paise - action_cost_paise,
        "actions_per_incremental_recovery": (
            actions_fired / attributed if attributed else float("inf")
        ),
        "deferrals": deferrals,
        "refusals_by_code": dict(sorted(refusals.items(), key=lambda kv: -kv[1])),
        "diagnosis_accuracy": diag_correct / diag_scored if diag_scored else 0.0,
        "diagnosis_scored": diag_scored,
        "diagnosis_methods": diag_methods,
        "diagnosis_confusion": diag_confusion,
        "diagnosis_by_method": diag_by_method,
        "exceptions": exceptions,
        "response_params": response_params,
    }


def _rs(paise: int) -> str:
    return f"Rs {paise / 100:,.0f}"


def _abbr(cause: str) -> str:
    return "".join(w[0] for w in cause.split("_")).upper()


def _diagnosis_section(m: dict) -> list[str]:
    """Accuracy, then where the error actually is.

    A single accuracy number invites the wrong reading -- that the diagnoser is
    uniformly 95% right. It is not: the deterministic path is a lookup table on
    documented error codes and is right by construction, so essentially all
    error lives in the LLM-fallback slice. The per-method split says so
    directly, and the matrix says which causes the fallback confuses.
    """
    confusion: dict[str, dict[str, int]] = m.get("diagnosis_confusion") or {}
    by_method: dict[str, dict[str, int]] = m.get("diagnosis_by_method") or {}

    lines = [
        "## Diagnosis",
        "",
        f"- Accuracy vs injected ground truth: {m['diagnosis_accuracy']:.1%} "
        f"over {m['diagnosis_scored']} scored cases",
        f"- Methods used: {m['diagnosis_methods']}",
        "",
    ]

    if by_method:
        lines += [
            "### Accuracy by method",
            "",
            "| Method | Scored | Correct | Accuracy |",
            "|---|---:|---:|---:|",
        ]
        for method, st in sorted(by_method.items(), key=lambda kv: -kv[1]["scored"]):
            acc = st["correct"] / st["scored"] if st["scored"] else 0.0
            lines.append(
                f"| {method} | {st['scored']} | {st['correct']} | {acc:.1%} |"
            )
        lines.append("")

    if confusion:
        causes = sorted(
            set(confusion) | {p for row in confusion.values() for p in row}
        )
        lines += [
            "### Confusion matrix",
            "",
            "Rows are the injected truth, columns the diagnosis. Off-diagonal",
            "cells are the errors.",
            "",
            "| truth \\ predicted | " + " | ".join(_abbr(c) for c in causes) + " |",
            "|---" * (len(causes) + 1) + "|",
        ]
        for truth in causes:
            row = confusion.get(truth, {})
            cells = []
            for pred in causes:
                n = row.get(pred, 0)
                if not n:
                    cells.append(".")
                elif pred == truth:
                    cells.append(f"**{n}**")
                else:
                    cells.append(str(n))
            lines.append(f"| {_abbr(truth)} | " + " | ".join(cells) + " |")

        lines += [
            "",
            "Legend: " + ", ".join(f"`{_abbr(c)}` {c}" for c in causes) + ".",
            "",
        ]

        errors = sorted(
            (
                (n, truth, pred)
                for truth, row in confusion.items()
                for pred, n in row.items()
                if pred != truth
            ),
            reverse=True,
        )
        if errors:
            lines += ["Most frequent confusions:", ""]
            lines += [
                f"- `{truth}` diagnosed as `{pred}` ({n}x)"
                for n, truth, pred in errors[:5]
            ]
            lines.append("")

            # The shape of the error matters more than its size. An error that
            # lands on UNKNOWN costs a recovery: the case routes to the
            # exception list and no action fires. An error that lands on the
            # wrong *cause* costs more than that -- it fires a confident,
            # specific, wrong intervention, which is the failure the whole
            # cause-keyed design exists to avoid. Distinguish them.
            confident = [e for e in errors if e[2] != RootCause.UNKNOWN.value]
            if not confident:
                lines += [
                    "**Every misclassification is a confusion with `unknown`, not "
                    "with another cause.** No case was diagnosed confidently and "
                    "wrongly. The diagnoser's whole error budget is spent failing "
                    "to the exception list, where a human sees it, rather than on "
                    "firing a specific wrong intervention. Headline accuracy "
                    "therefore understates the safety property: the residual "
                    f"{1 - m['diagnosis_accuracy']:.1%} is abstention, not error.",
                    "",
                ]
            else:
                n_conf = sum(e[0] for e in confident)
                lines += [
                    f"**{n_conf} case(s) were diagnosed as the wrong cause rather "
                    "than as `unknown`.** These are the expensive errors: a wrong "
                    "cause fires a confident, specific, wrong intervention instead "
                    "of routing to the exception list.",
                    "",
                ]
        else:
            lines += ["No misclassifications in this batch.", ""]

    return lines


def render_markdown(m: dict, batch_id: str, sensitivity: list[dict] | None = None) -> str:
    tr, ct = m["treatment"], m["control"]
    lo, hi = m["lift_ci_pp"]
    lines = [
        f"# Recovery batch report - `{batch_id}`",
        "",
        "## Headline",
        "",
        f"- **Incremental recovery: {m['lift_pp']:+.1f} pp** "
        f"(95% CI {lo:+.1f} to {hi:+.1f} pp)"
        f"{'  <- significant' if m['lift_significant'] else '  <- NOT significant'}",
        f"- **Incremental money recovered: {_rs(m['incremental_paise'])}**",
        f"- Gross recovered in treatment: {_rs(m['gross_recovered_paise'])} "
        f"(overstates the agent: {_rs(m['counterfactual_paise'])} of it would have "
        f"arrived anyway at the control arm's rate)",
        "",
        "## Arms",
        "",
        "| Arm | Cases | Recovered | Rate | At risk | Recovered |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Treatment | {tr['n']} | {tr['recovered']} | {tr['rate']:.1%} | "
        f"{_rs(tr['at_risk_paise'])} | {_rs(tr['recovered_paise'])} |",
        f"| Control (held out) | {ct['n']} | {ct['recovered']} | {ct['rate']:.1%} | "
        f"{_rs(ct['at_risk_paise'])} | {_rs(ct['recovered_paise'])} |",
        "",
        "## Cost of acting",
        "",
        f"- **Net value: {_rs(m['net_value_paise'])}** "
        f"(incremental recovery minus what the actions cost)",
        f"- Action cost: {_rs(m['action_cost_paise'])} across "
        f"{m['actions_fired']} action(s), {m['contacts']} of them customer contacts",
        f"- Recoveries attributable to an intervention: {m['attributed_to_intervention']}",
        f"- Actions per attributable recovery: "
        f"{m['actions_per_incremental_recovery']:.2f} "
        f"(reported, not optimised against -- see net value above)",
        f"- Contacts deferred for the RBI window: {m['deferrals']}",
        "",
        *_diagnosis_section(m),
        "## Gate activity",
        "",
        f"- Contacts deferred to respect the RBI window: {m['deferrals']}",
        "",
    ]
    if m["refusals_by_code"]:
        lines += ["| Refusal code | Count |", "|---|---:|"]
        lines += [f"| `{k}` | {v} |" for k, v in m["refusals_by_code"].items()]
    else:
        lines += [
            "No hard refusals fired in this batch. That is the expected shape: the",
            "policy engine's stopping rules retire a case before it can reach a",
            "mandate limit, so the gate acts as a backstop rather than the primary",
            "control. `razor-pay demo-refusals` exercises all four refusal paths",
            "directly.",
        ]

    if sensitivity:
        lines += [
            "",
            "## Sensitivity to the assumed control baseline",
            "",
            "The control baseline is an assumption, so the headline is reported as a",
            "range rather than a point estimate.",
            "",
            "| Baseline scale | Control rate | Treatment rate | Lift (pp) |",
            "|---:|---:|---:|---:|",
        ]
        for row in sensitivity:
            lines.append(
                f"| {row['baseline_scale']:.2f}x | {row['control_rate']:.1%} | "
                f"{row['treatment_rate']:.1%} | {row['lift_pp']:+.1f} |"
            )

    lines += [
        "",
        "## Exceptions the engine could not resolve",
        "",
        f"{len(m['exceptions'])} of {tr['n']} treatment cases.",
        "",
    ]
    if m["exceptions"]:
        lines += [
            "| Case | Amount | Diagnosed | Conf | Actions | Why |",
            "|---|---:|---|---:|---:|---|",
        ]
        for e in m["exceptions"][:40]:
            why = e["why"].replace("|", "/")[:90]
            lines.append(
                f"| `{e['case_id']}` | {_rs(e['amount_paise'])} | {e['diagnosed']} | "
                f"{e['confidence']:.2f} | {e['actions_fired']} | {why} |"
            )
        if len(m["exceptions"]) > 40:
            lines.append(f"| ... | | | | | {len(m['exceptions']) - 40} more |")
    else:
        lines.append("_None._")

    lines += [
        "",
        "## What is real and what is modelled",
        "",
        "- **Real:** every Razorpay Order and Payment Link created by this run is a",
        "  genuine test-mode entity with a live id, verifiable in the dashboard.",
        (
            f"- **Degraded:** {m['degraded_artifacts']} link-type action(s) could not "
            f"produce a real artifact, because Razorpay test mode allows only 30 "
            f"Payment Links per business for the lifetime of the account. These are "
            f"flagged `degraded: true` in the ledger and are NOT real artifacts."
            if m.get("degraded_artifacts")
            else "- No degraded artifacts: every action produced a real entity."
        ),
        "- **Modelled:** whether a customer pays after an intervention. Parameters:",
        f"  `{m['response_params']}`.",
        "- The control arm exists precisely because the modelled layer would",
        "  otherwise let any intervention look effective.",
        "",
    ]
    return "\n".join(lines)


def sensitivity_sweep(
    traces: list[CaseTrace],
    cases: list[RecoveryCase],
    scales: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25, 1.5),
    seed: int = 0,
) -> list[dict]:
    """Recompute arm rates under different assumed control baselines.

    Purely offline: the actions the policy chose do not depend on the response
    model, so the sequence already fired is replayed against a new baseline
    without touching Razorpay again.
    """
    from razor_pay.harness.response_model import ResponseModel
    from razor_pay.schemas import InterventionType

    by_id = {c.case_id: c for c in cases}
    rows: list[dict] = []

    for scale in scales:
        model = ResponseModel(baseline_scale=scale, seed=seed)
        tr_n = tr_rec = ct_n = ct_rec = 0

        for t in traces:
            case = by_id[t.case_id]
            cause = case.injected_cause or RootCause.UNKNOWN
            self_rec = model.would_self_recover(t.case_id, cause)

            if t.arm is Arm.CONTROL:
                ct_n += 1
                ct_rec += int(self_rec)
                continue

            landed = any(
                model.intervention_lands(t.case_id, cause, InterventionType(name), i)
                for i, name in enumerate(t.interventions)
            )
            tr_n += 1
            tr_rec += int(self_rec or landed)

        ct_rate = ct_rec / ct_n if ct_n else 0.0
        tr_rate = tr_rec / tr_n if tr_n else 0.0
        rows.append(
            {
                "baseline_scale": scale,
                "control_rate": ct_rate,
                "treatment_rate": tr_rate,
                "lift_pp": (tr_rate - ct_rate) * 100,
            }
        )
    return rows
