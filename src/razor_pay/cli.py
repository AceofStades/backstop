"""Command line interface."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

from razor_pay import adapters as _adapters  # noqa: F401  (registration side effects)
from razor_pay.adapters import ADAPTERS, SeedClient, get_adapter
from razor_pay.config import LiveKeyRefused, assert_test_mode, load_settings
from razor_pay.diagnose import Diagnoser
from razor_pay.execute import build_executor
from razor_pay.harness.assign import assign_all
from razor_pay.harness.metrics import compute, render_markdown, sensitivity_sweep
from razor_pay.harness.response_model import ResponseModel
from razor_pay.harness.runner import BatchRunner, CaseTrace
from razor_pay.ledger import Ledger
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
    try:
        store.conn.execute("DELETE FROM ledger")
        click.secho("  Append-only ledger: FAIL (delete succeeded)", fg="red")
    except Exception:
        click.secho("  Append-only ledger: PASS (DELETE refused by trigger)", fg="green")
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
def seed(cases, leaks, control_fraction, seed, db, simulated) -> None:
    """Build a batch. Creates real test-mode Orders unless --simulated."""
    import random

    settings = load_settings(db)
    now = datetime.now(timezone.utc)
    batch_id = now.strftime("batch_%Y%m%d_%H%M%S")
    rng = random.Random(seed)

    client = SeedClient()
    if not simulated and settings.has_razorpay:
        assert_test_mode(settings.razorpay_key_id)
        import razorpay

        client = SeedClient(
            razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
        )

    leak_types = [LeakType(name.strip()) for name in leaks.split(",") if name.strip()]
    per_leak = cases // len(leak_types)
    remainder = cases - per_leak * len(leak_types)

    # Case ids carry the batch's time token so they stay unique across batches.
    token = batch_id.rsplit("_", 1)[-1]
    all_cases = []
    for i, leak in enumerate(leak_types):
        n = per_leak + (remainder if i == 0 else 0)
        all_cases.extend(
            get_adapter(leak).seed(n, rng, now, "merch_demo_001", client, token)
        )

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
        executor=build_executor(settings, force_simulated=simulated),
        response=response,
    )

    click.echo(f"Running {len(cases)} cases in {batch_id}...")
    traces = runner.run(cases)

    metrics = compute(traces, cases, response.describe())
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


@main.command("demo-refusals")
@click.option("--db", default="data/demo.db", show_default=True)
def demo_refusals(db: str) -> None:
    """Four scenarios where the gate refuses cleanly, each with a reason code."""
    from razor_pay.harness.scenarios import run_refusal_scenarios

    run_refusal_scenarios(db, click.echo, click.secho)
