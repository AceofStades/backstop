"""Command line interface."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

from razor_pay import adapters as _adapters  # noqa: F401  (registration side effects)
from razor_pay.adapters import ADAPTERS, SeedClient, get_adapter
from razor_pay.adapters.base import SeedingFailed
from razor_pay.config import LiveKeyRefused, assert_test_mode, load_settings
from razor_pay.diagnose import Diagnoser
from razor_pay.execute import TEST_MODE_PAYMENT_LINK_CAP, build_executor
from razor_pay.harness.assign import assign_all
from razor_pay.harness.metrics import compute, render_markdown, sensitivity_sweep
from razor_pay.harness.response_model import ResponseModel
from razor_pay.harness.runner import BatchRunner, CaseTrace
from razor_pay.ledger import Ledger
from razor_pay.ledger import Stage as LedgerStage
from razor_pay.mandate import Mandate
from razor_pay.schemas import Arm, LeakType
from razor_pay.store import Store

REPORTS = Path("reports")
DEFAULT_LEAKS = "payment_failure,subscription_dunning"


def _store(db: str) -> Store:
    return Store(db)


def _mandate(batch_id: str, now: datetime, **overrides) -> Mandate:
    params = dict(
        mandate_id=f"mand_{batch_id}",
        merchant_id="merch_demo_001",
        issued_at=now,
        expires_at=now + timedelta(days=30),
        per_action_cap_paise=2_500_000,      # Rs 25,000 per action
        velocity_cap_paise=500_000_000,      # Rs 50,00,000 per 24h
        velocity_window_hours=24,
        max_attempts_per_case=3,
    )
    params.update(overrides)
    return Mandate(**params)


@click.group()
def main() -> None:
    """Bounded, audited revenue-recovery agent on Razorpay test-mode APIs."""


@main.command()
@click.option("--db", default="data/recovery.db", show_default=True)
def verify(db: str) -> None:
    """Day-one preflight: credentials, test-mode interlock, API reachability."""
    settings = load_settings(db)
    click.echo("== Preflight ==")

    if not settings.has_razorpay:
        click.secho("  Razorpay:  no credentials -> simulated executor", fg="yellow")
    else:
        try:
            assert_test_mode(settings.razorpay_key_id)
            click.secho(
                f"  Test-mode interlock: PASS ({settings.razorpay_key_id[:12]}...)",
                fg="green",
            )
        except LiveKeyRefused as exc:
            click.secho(f"  Test-mode interlock: REFUSED - {exc}", fg="red")
            raise SystemExit(1)

        try:
            import razorpay

            client = razorpay.Client(
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
            )
            order = client.order.create(
                {
                    "amount": 100,
                    "currency": "INR",
                    "receipt": "preflight",
                    "notes": {"purpose": "preflight"},
                }
            )
            click.secho(f"  API reachable: PASS (created {order['id']})", fg="green")
        except Exception as exc:
            click.secho(f"  API reachable: FAIL - {type(exc).__name__}: {exc}", fg="red")

    if settings.has_anthropic:
        click.secho("  Anthropic key: present -> LLM fallback enabled", fg="green")
    else:
        click.secho(
            "  Anthropic key: absent -> uncoded cases go to the exception list",
            fg="yellow",
        )

    store = _store(db)
    click.secho(f"  Store: {store.path}", fg="green")

    # A BEFORE DELETE trigger never fires on an empty table, so probing an empty
    # ledger reports a false FAIL. Write a probe row first, in a transaction that
    # is always rolled back so the real ledger is untouched.
    ok = False
    try:
        store.conn.execute("SAVEPOINT preflight")
        Ledger(store, "preflight").record(
            ts=datetime.now(timezone.utc),
            case_id="preflight_probe",
            stage=LedgerStage.DETECT,
            reason="append-only probe; rolled back",
        )
        try:
            store.conn.execute("DELETE FROM ledger WHERE case_id = 'preflight_probe'")
        except Exception:
            ok = True
        try:
            store.conn.execute("UPDATE ledger SET reason = 'x' WHERE case_id = 'preflight_probe'")
            ok = False
        except Exception:
            ok = ok and True
    finally:
        store.conn.execute("ROLLBACK TO preflight")
        store.conn.execute("RELEASE preflight")

    if ok:
        click.secho("  Append-only ledger: PASS (UPDATE and DELETE refused)", fg="green")
    else:
        click.secho("  Append-only ledger: FAIL (mutation succeeded)", fg="red")
    store.close()


@main.command()
@click.option("--cases", default=400, show_default=True, help="Total cases to seed.")
@click.option("--leaks", default=DEFAULT_LEAKS, show_default=True)
@click.option(
    "--control-fraction",
    default=0.4,
    show_default=True,
    help="Held-out share. At 0.4 the batch is powered from ~200 cases up.",
)
@click.option("--seed", default=42, show_default=True)
@click.option("--db", default="data/recovery.db", show_default=True)
@click.option("--simulated/--real", default=False, help="Skip real Razorpay calls.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Create 3 real Orders to prove credentials work, then stop without saving.",
)
def seed(cases, leaks, control_fraction, seed, db, simulated, dry_run) -> None:
    """Build a batch. Creates real test-mode Orders unless --simulated."""
    import random

    settings = load_settings(db)
    now = datetime.now(timezone.utc)
    batch_id = now.strftime("batch_%Y%m%d_%H%M%S")
    rng = random.Random(seed)

    def note_retry(attempt: int, delay: float, exc: BaseException) -> None:
        click.secho(
            f"  retry {attempt} in {delay:.1f}s after {type(exc).__name__}: "
            f"{str(exc)[:80]}",
            fg="yellow",
        )

    client = SeedClient(on_retry=note_retry)
    if not simulated and settings.has_razorpay:
        assert_test_mode(settings.razorpay_key_id)
        import razorpay

        client = SeedClient(
            razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret)),
            on_retry=note_retry,
        )

    if dry_run:
        # Prove the credential path on three calls rather than four hundred.
        if not client.is_real:
            raise click.ClickException(
                "--dry-run needs real credentials. Set RAZORPAY_KEY_ID/SECRET in .env."
            )
        click.echo("Dry run: creating 3 real test-mode Orders...")
        for i in range(3):
            order = client.create_order(
                10000, f"dryrun_{now.strftime('%H%M%S')}_{i}", {"purpose": "dry-run"}
            )
            click.secho(f"  created {order.get('id')}", fg="green")
        click.secho(
            f"\nCredentials work. {client.real_calls} call(s), "
            f"{client.retries} retry(ies). Nothing saved.",
            fg="green",
            bold=True,
        )
        return

    leak_types = [LeakType(name.strip()) for name in leaks.split(",") if name.strip()]
    per_leak = cases // len(leak_types)
    remainder = cases - per_leak * len(leak_types)

    # Case ids carry the batch's time token so they stay unique across batches.
    token = batch_id.rsplit("_", 1)[-1]
    all_cases = []
    try:
        for i, leak in enumerate(leak_types):
            n = per_leak + (remainder if i == 0 else 0)
            all_cases.extend(
                get_adapter(leak).seed(n, rng, now, "merch_demo_001", client, token)
            )
    except SeedingFailed as exc:
        raise click.ClickException(
            f"{exc}\n\nNothing was saved. {client.real_calls} order(s) were created "
            f"before the failure; they are harmless test-mode entities. "
            f"Re-run once the cause is resolved, or use --simulated."
        ) from exc

    # Assignment happens here, at intake, before anything has looked at a case.
    assign_all(all_cases, control_fraction)

    store = _store(db)
    mandate = _mandate(batch_id, now)
    store.create_batch(
        batch_id,
        now,
        mandate,
        {
            "cases": cases,
            "leaks": leaks,
            "control_fraction": control_fraction,
            "seed": seed,
            "real_api_calls": client.real_calls,
        },
    )
    for case in all_cases:
        store.insert_case(batch_id, case)
    store.commit()

    n_ctrl = sum(1 for c in all_cases if c.arm is Arm.CONTROL)
    total = sum(c.amount_at_risk_paise for c in all_cases)
    click.secho(f"Seeded batch {batch_id}", fg="green", bold=True)
    click.echo(f"  cases            : {len(all_cases)}")
    click.echo(f"  control/treatment: {n_ctrl}/{len(all_cases) - n_ctrl}")
    click.echo(f"  money at risk    : Rs {total / 100:,.0f}")
    click.echo(f"  real Razorpay orders created: {client.real_calls}")
    if client.retries:
        click.echo(f"  transient failures retried    : {client.retries}")
    store.close()


@main.command()
@click.option("--batch", default=None, help="Batch id (defaults to latest).")
@click.option("--db", default="data/recovery.db", show_default=True)
@click.option("--no-llm", is_flag=True, help="Disable the LLM fallback classifier.")
@click.option("--simulated/--real", default=False, help="Skip real Razorpay calls.")
@click.option("--force", is_flag=True, help="Re-run a batch that already ran.")
def run(batch, db, no_llm, simulated, force) -> None:
    """Run the recovery loop over a batch and write the report."""
    settings = load_settings(db)
    store = _store(db)
    batch_id = batch or store.latest_batch_id()
    if not batch_id:
        raise click.ClickException("No batches found. Run `seed` first.")

    row = store.get_batch(batch_id)
    mandate = Mandate.model_validate_json(row["mandate_json"])
    cases = store.load_cases(batch_id)
    if not cases:
        raise click.ClickException(f"Batch {batch_id} has no cases.")

    executor = build_executor(settings, force_simulated=simulated)

    # Razorpay test mode caps Payment Links at 30 per account. Roughly a third of
    # interventions are link-type, so a large real-API batch runs out partway and
    # the resulting numbers understate the policy for a reason that has nothing to
    # do with the policy. Warn before spending the budget rather than after.
    if getattr(executor, "name", "") == "razorpay-test":
        est_links = int(len(cases) * 0.35)
        if est_links > TEST_MODE_PAYMENT_LINK_CAP:
            click.secho(
                f"  WARNING: ~{est_links} payment links expected, but Razorpay test "
                f"mode caps them at {TEST_MODE_PAYMENT_LINK_CAP}.",
                fg="yellow",
            )
            click.secho(
                f"  Link-type interventions will fail once the cap is hit, and the "
                f"reported lift will be understated.",
                fg="yellow",
            )
            click.secho(
                f"  For integration proof use a small batch (--cases 60). For "
                f"statistical claims use `razor-pay replicate`, which is simulated.",
                fg="yellow",
            )
            if not click.confirm("  Continue anyway?", default=False):
                raise click.Abort()

    ledger = Ledger(store, batch_id)
    # Idempotency means a second run is a no-op, which would otherwise overwrite a
    # good report with an empty one. Fail loudly instead.
    if ledger.count() and not force:
        raise click.ClickException(
            f"Batch {batch_id} has already run ({ledger.count()} ledger entries). "
            f"Idempotency guarantees a re-run fires nothing, so the report would be "
            f"empty. Seed a fresh batch, or pass --force to regenerate anyway."
        )

    response = ResponseModel(baseline_scale=1.0, seed=0)
    runner = BatchRunner(
        store=store,
        ledger=ledger,
        mandate=mandate,
        diagnoser=Diagnoser(use_llm=not no_llm),
        executor=executor,
        response=response,
    )

    click.echo(f"Running {len(cases)} cases in {batch_id}...")
    traces = runner.run(cases)

    metrics = compute(
        traces, cases, response.describe(), getattr(executor, "degraded_links", 0)
    )
    sweep = sensitivity_sweep(traces, cases)
    report = render_markdown(metrics, batch_id, sweep)

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{batch_id}.md").write_text(report)
    (REPORTS / f"{batch_id}.json").write_text(
        json.dumps(
            {"metrics": metrics, "sensitivity": sweep, "traces": [t.__dict__ for t in traces]},
            indent=2,
            default=str,
        )
    )

    lo, hi = metrics["lift_ci_pp"]
    click.secho("\n== Headline ==", bold=True)
    click.echo(
        f"  incremental lift : {metrics['lift_pp']:+.1f} pp "
        f"(95% CI {lo:+.1f} .. {hi:+.1f})"
    )
    click.echo(f"  incremental money: Rs {metrics['incremental_paise'] / 100:,.0f}")
    click.echo(f"  gross recovered  : Rs {metrics['gross_recovered_paise'] / 100:,.0f}")
    click.echo(f"  actions fired    : {metrics['actions_fired']}")
    click.echo(f"  exceptions       : {len(metrics['exceptions'])}")
    if metrics.get("degraded_artifacts"):
        click.secho(
            f"  degraded links   : {metrics['degraded_artifacts']} "
            f"(test-mode quota spent; not real artifacts)",
            fg="yellow",
        )
    click.echo(f"  ledger entries   : {ledger.count()}")
    click.secho(f"\nReport: reports/{batch_id}.md", fg="green")
    store.close()


@main.command()
@click.option("--batch", default=None)
@click.option("--db", default="data/recovery.db", show_default=True)
def report(batch, db) -> None:
    """Print the saved report for a batch."""
    store = _store(db)
    batch_id = batch or store.latest_batch_id()
    store.close()
    path = REPORTS / f"{batch_id}.md"
    if not path.exists():
        raise click.ClickException(f"No report at {path}. Run `run` first.")
    click.echo(path.read_text())


@main.command()
@click.argument("case_id")
@click.option("--db", default="data/recovery.db", show_default=True)
def audit(case_id: str, db: str) -> None:
    """Print the full append-only audit trail for one case."""
    store = _store(db)
    batch_id = store.latest_batch_id()
    ledger = Ledger(store, batch_id or "")

    entries = ledger.entries_for_case(case_id)
    if not entries:
        # Case ids carry a batch token, so allow a unique partial match:
        # `audit pf_0000` still works when the full id is pf_134512_0000.
        matches = ledger.resolve_case_ids(case_id)
        if len(matches) == 1:
            case_id = matches[0]
            entries = ledger.entries_for_case(case_id)
        elif len(matches) > 1:
            raise click.ClickException(
                f"'{case_id}' matches {len(matches)} cases: "
                f"{', '.join(matches[:5])}{' ...' if len(matches) > 5 else ''}"
            )
        else:
            raise click.ClickException(f"No ledger entries for case {case_id}.")

    click.secho(f"Audit trail for {case_id} ({len(entries)} entries)", bold=True)
    for e in entries:
        head = f"  [{e['seq']:>4}] {e['ts'][:19]}  {e['stage'].upper():<9}"
        if e["allowed"] is not None:
            head += " ALLOWED" if e["allowed"] else f" REFUSED({e['refusal_code']})"
        click.echo(head)
        click.echo(f"         reason : {e['reason']}")
        if e["action_type"]:
            click.echo(
                f"         action : {e['action_type']} via {e['channel']} "
                f"amount={e['amount_paise']} policy={e['policy_version']}"
            )
        checks = json.loads(e["checks_performed"] or "[]")
        if checks:
            click.echo(f"         checked: {', '.join(checks)}")
        if e["idempotency_key"]:
            click.echo(f"         idem   : {e['idempotency_key']}")
    store.close()


@main.command()
@click.option("--runs", default=12, show_default=True, help="Independent batches.")
@click.option("--cases", default=400, show_default=True, help="Cases per batch.")
@click.option("--leaks", default=DEFAULT_LEAKS, show_default=True)
@click.option("--control-fraction", default=0.4, show_default=True)
def replicate(runs, cases, leaks, control_fraction) -> None:
    """Pool many independent batches, so the headline is not one lucky draw.

    Always simulated: this measures the policy under the response model, which is
    the thing that varies, and avoids creating thousands of test-mode orders.
    """
    from razor_pay.harness import replicate as rep

    leak_types = [LeakType(name.strip()) for name in leaks.split(",") if name.strip()]
    now = datetime.now(timezone.utc)

    click.echo(f"Running {runs} independent batches of {cases}...")
    with click.progressbar(length=runs, label="  batches") as bar:
        result = rep.replicate(
            runs, cases, leak_types, control_fraction, now, progress=lambda _: bar.update(1)
        )

    REPORTS.mkdir(exist_ok=True)
    stamp = now.strftime("replication_%Y%m%d_%H%M%S")
    (REPORTS / f"{stamp}.md").write_text(rep.render_markdown(result))
    (REPORTS / f"{stamp}.json").write_text(json.dumps(result, indent=2, default=str))

    lo, hi = result["pooled_ci_pp"]
    click.secho("\n== Pooled across replications ==", bold=True)
    click.echo(
        f"  mean lift      : {result['mean_lift_pp']:+.1f} pp "
        f"(95% CI of mean {lo:+.1f} .. {hi:+.1f})"
    )
    click.echo(
        f"  between-batch  : sd {result['sd_lift_pp']:.1f} pp, "
        f"range {result['min_lift_pp']:+.1f} .. {result['max_lift_pp']:+.1f}"
    )
    click.echo(
        f"  mean net value : Rs {result['mean_net_value_paise'] / 100:,.0f} "
        f"(sd Rs {result['sd_net_value_paise'] / 100:,.0f})"
    )
    click.secho(f"\nReport: reports/{stamp}.md", fg="green")


@main.command("uplift-sensitivity")
@click.option("--trials", default=2000, show_default=True,
              help="Perturbation draws for the ranking check.")
@click.option("--seed", default=0, show_default=True)
def uplift_sensitivity(trials, seed) -> None:
    """State the sensitivity to the one parameter that cannot be measured.

    `economics.BELIEVED_UPLIFT` is asserted, not observed. Rather than defend
    the numbers, report which decisions would change if they are wrong. Needs
    no batch and touches no API: it reads the belief table, the playbook and
    the cost table only.
    """
    from razor_pay.harness import uplift_sensitivity as us

    decisions = us.decision_stability()
    rankings = us.ranking_stability(trials=trials, seed=seed)

    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("uplift_sensitivity_%Y%m%d_%H%M%S")
    (REPORTS / f"{stamp}.md").write_text(us.render_markdown(decisions, rankings))
    (REPORTS / f"{stamp}.json").write_text(
        json.dumps({"decisions": decisions, "rankings": rankings}, indent=2)
    )

    fragile = [r for r in decisions if not r["stable"]]
    click.secho("\n== Decision stability ==", bold=True)
    click.echo(f"  ladder steps        : {len(decisions)}")
    click.echo(f"  stable across band  : {len(decisions) - len(fragile)}")
    click.echo(f"  sensitive to belief : {len(fragile)}")
    for r in fragile:
        click.echo(
            f"    - {r['cause']} #{r['step_index']} "
            f"({r['intervention']} / {r['channel']})"
        )

    click.secho("\n== Ranking stability ==", bold=True)
    for r in sorted(rankings, key=lambda r: r["stability"]):
        click.echo(
            f"  {r['cause']:<22} top={r['baseline_top']:<28} "
            f"unchanged {r['stability']:.1%}"
        )

    click.secho(f"\nReport: reports/{stamp}.md", fg="green")


@main.command("demo-refusals")
@click.option("--db", default="data/demo.db", show_default=True)
def demo_refusals(db: str) -> None:
    """Four scenarios where the gate refuses cleanly, each with a reason code."""
    from razor_pay.harness.scenarios import run_refusal_scenarios

    run_refusal_scenarios(db, click.echo, click.secho)
