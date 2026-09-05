# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A revenue-recovery agent for the Razorpay AI Buildathon 2026 (Track 03). It detects
money at risk, diagnoses why, picks an intervention matched to that cause, executes
it inside hard limits, and measures recovery against a held-out control arm.

The deliverable is judged in an architecture interview, so the invariants below are
the product. Breaking one silently turns a defensible result into an indefensible
one — treat them as load-bearing, not stylistic.

## Commands

```bash
uv sync --extra dev

uv run razor-pay verify          # preflight: test-mode interlock, API reachability, ledger triggers
uv run razor-pay seed --cases 400 # build a batch (creates real test-mode Orders)
uv run razor-pay run              # run the loop, write reports/<batch>.md and .json
uv run razor-pay replicate --runs 12  # pooled headline across independent batches
uv run razor-pay uplift-sensitivity   # what decisions change if BELIEVED_UPLIFT is wrong
uv run razor-pay report           # print the saved report
uv run razor-pay report --html    # render the same saved metrics as a standalone page
uv run razor-pay audit pf_0000    # full append-only audit trail for one case
uv run razor-pay demo-refusals    # the four gate-refusal scenarios

uv run pytest                                             # full suite
uv run pytest tests/test_policy.py -q                      # one file
uv run pytest tests/test_policy.py::test_ladder_exhaustion_is_a_stop -q   # one test
```

`--simulated` on `seed`/`run` skips Razorpay entirely; `--no-llm` skips the fallback
classifier. Both together run fully offline — that is how the tests run, and how to
work without credentials.

`run` refuses to re-run a completed batch (idempotency makes it a no-op that would
overwrite a good report with an empty one). Seed a fresh batch, or pass `--force`.

## Pipeline

```
Detector (leak adapter)  ->  RecoveryCase
  -> Diagnoser     -> RootCause + confidence   diagnose.py
  -> Policy engine -> Intervention | STOP      policy.py + economics.py
  -> Mandate gate  -> ALLOW | REFUSE(code)     mandate.py
  -> Executor      -> Razorpay test-mode call  execute.py
  -> Ledger        -> append-only entry        ledger.py / store.py
  -> Harness       -> outcome vs control       harness/
```

Every adapter emits the same `RecoveryCase`, which is why a new leak type costs an
adapter and nothing else. Register it in `adapters/base.py` and import it in
`adapters/__init__.py` for the side effect.

## Invariants

**The three-layer evidence split.** Understanding this is prerequisite to changing
anything in `harness/` or `adapters/`:

- *Real* — Razorpay Orders and Payment Links are genuine test-mode entities with live
  ids. Policy, gate, idempotency and ledger are real code paths.
- *Modelled* — whether a customer pays after an intervention (`harness/response_model.py`).
- *Injected* — the decline reason on each seeded case, stored as `injected_cause`.

The README states this split explicitly to the panel. Do not blur it, and do not let
a change quietly move something from "modelled" into language that implies "real".

**`injected_cause` is ground truth for scoring only.** No component under test may
read it — not the diagnoser, not the policy engine, not the executor. Only
`harness/` reads it, to score the diagnoser and drive the response model.
`test_diagnoser_never_reads_injected_ground_truth` guards this.

**The LLM proposes; deterministic code disposes.** The model only ever produces a
`Diagnosis` constrained to the `RootCause` enum. It never selects an action, and its
confidence is capped below the deterministic path (`LLM_MAX_CONFIDENCE` <
`DETERMINISTIC_CONFIDENCE`) because a model's stated certainty is not the same class
of evidence as a documented error code. Below `CONFIDENCE_THRESHOLD` (0.60) the engine
refuses to act and routes to the exception list.

**No default retry.** `policy.PLAYBOOK` maps each cause to an escalation ladder
indexed by attempts made. A cause absent from the playbook stops — deliberately, and
`RootCause.UNKNOWN` is absent on purpose. A retry may only be selected where
`taxonomy.profile_for(cause).retryable_same_instrument` is true;
`test_never_retries_an_instrument_that_cannot_work` asserts this across every cause.

**Net value, not the efficiency ratio.** Actions-per-recovery is reported but is NOT
an optimisation target. Tightening `economics.MIN_EXPECTED_VALUE_RATIO` above
break-even improves that ratio and destroys net value — measured, −Rs 2,522. A ratio
improves when you cut its denominator. The threshold stays at 1.0, where it encodes
a principle rather than a tuning knob, and
`test_break_even_threshold_is_a_principle_not_a_tuning_knob` guards it.

**The engine's beliefs are not the harness's truth.** `economics.BELIEVED_UPLIFT` is a
separate object from `harness/response_model.py`'s `UPLIFT`. Never import one into the
other: the engine would gain perfect knowledge of the simulated customer, and any
expected-value rule would then be measuring the simulation rather than the policy.

**The gate is the only path to money.** Nothing may call Razorpay without passing
`mandate.check()` first. Refusals are structured `GateResult` values carrying a
`RefusalCode`, never exceptions — `test_gate_never_raises_on_any_intervention_type`
asserts this across the full intervention/channel cross product. RBI (08:00–19:00
contact window) and TRAI (DLT template required) are enforced here in deterministic
code precisely so the model cannot be argued past them.

**Arm assignment happens at intake**, in `seed`, before any policy sees a case.
Assigning later would let the engine's behaviour decide who lands in which arm. The
response model uses common random numbers keyed on `case_id`, so the same case makes
the same self-recovery draw in either arm.

**Contacts are capped per customer as well as per case.** `max_contacts_per_customer`
is counted from the ledger across every case with that `customer_ref`. Seeding draws
from a deliberately small customer pool with a heavy tail — a wide uniform pool makes
repeat customers vanish and silently disables this check.

**An unmeasurable parameter is stated as a sensitivity, not defended.**
`economics.BELIEVED_UPLIFT` cannot be measured without production data.
`harness/uplift_sensitivity.py` reports which decisions change if it is wrong
across a half-to-double band — 8 of 18 ladder steps are sensitive and all 8 cost
money to fire, which is the mechanism rather than a coincidence. That module may
read the engine's belief table and must never import `response_model.UPLIFT`;
doing so converts a sensitivity analysis into a scoring against an answer key.
`test_uplift_sensitivity_never_reads_harness_truth` parses its imports to stop it.

**Diagnosis error is reported by shape, not just by size.** The report splits
accuracy by method (the deterministic path is a lookup table and scores 100%) and
renders a confusion matrix. What matters is that every error lands on `UNKNOWN`
rather than on another cause: abstention routes to the exception list, whereas a
confident wrong cause fires a specific wrong intervention. Never collapse these
into one accuracy figure — `test_report_distinguishes_abstention_from_confident_error`
guards the distinction.

**The HTML page renders, it does not compute.** `harness/html_report.py` takes the
metrics dict `compute()` already produced and formats it. Nothing on that page may be
calculated there, or the page and the markdown report could disagree and both look
authoritative. It exists for a projector, where markdown and terminal output are
unreadable — not as a dashboard, and not as a second reporting path.

**Report incremental, never gross.** Gross recovery counts cases that would have
recovered anyway. The control baseline is an assumption, so the headline always ships
with the sensitivity sweep alongside it.

**One batch is one draw.** Single-batch lift varies ±5 pp around the pooled mean
(measured: +18.8 to +29.8 across seeds). Any headline quoted publicly must come from
`razor-pay replicate`, never from whichever batch ran last — a single-batch number
was quoted once and a clean re-run produced something 13 pp lower. Per-batch reports
are fine for inspecting behaviour; they are not the result.

**The ledger is append-only, enforced by SQLite triggers.** `UPDATE`/`DELETE` raise.
Corrections are new rows. Idempotency keys are `<case_id>:<attempt_no>` under a unique
index, so a re-run cannot double-charge or double-contact.

**Test-mode interlock.** `config.assert_test_mode()` refuses any key not prefixed
`rzp_test_` before a client is constructed. Never weaken this to accommodate a test —
use `--simulated` instead.

## Docs

`docs/` carries the reasoning, not just the API surface. Before changing measurement
or compliance code, read `docs/03-measurement.md` and `docs/04-compliance.md`;
`docs/05-worklog.md` records sixteen bugs and why each was subtle — bugs 12-16 are
the ones only the live API exposed. `docs/08-submission.md` holds the drafted
application answers and the video plan; personal fields there are marked `[FILL]`
and must never be invented. Keep reported
numbers in README and docs in sync with the latest batch — stale figures in a doc
that claims measurement rigour are worse than no doc.

## Real-API constraints

Verified against live `rzp_test_` credentials. Orders work at scale; two limits bite:

- **Rate limiting.** A 400-case seed draws ~124 `Too many requests` responses.
  `retry.py` handles this with jittered backoff plus a `Throttle` that paces calls.
  Never remove the throttle to make a run faster.
- **30 Payment Links per business, for the lifetime of the test account.** Not
  resettable by backoff; support must raise it. Once spent, the executor records a
  placeholder flagged `degraded: true` rather than failing the case — otherwise an
  account quota masquerades as a policy result (it did once, dragging a live run to
  +15.0 pp). A degraded artifact is never counted as real, and the report always
  discloses the count.

**Two claims, two sources — never blur them.** The live run proves the
*integration*. `replicate` (simulated, pooled) provides the *statistic*. A live run
at quota-permitted scale has a CI spanning zero and is not evidence of effect.

Not every 5xx is transient: Razorpay returns the link quota as a `ServerError`, so
`retry.is_transient` checks a permanent-condition list before the status code.

## Current state

Uncommitted work may still be pending; check `git status` before assuming the tree is
clean.
