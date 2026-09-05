# Work log

What was built, in what order, and what went wrong. Kept because the bugs are more
instructive than the features — each one is a case where the design was subtly
wrong in a way the tests did not initially catch.

---

## Build order

Deliberately inside-out: types first, then the deterministic core, then the things
that talk to the outside world, then measurement on top.

| Order | Component | Why here |
|---:|---|---|
| 1 | `schemas.py`, `taxonomy.py` | Every stage boundary is a type; settle them first |
| 2 | `policy.py` | The core claim of the project — pure, testable, no I/O |
| 3 | `mandate.py` | Gate before executor, so nothing can be written that bypasses it |
| 4 | `store.py`, `ledger.py` | Persistence with triggers |
| 5 | `config.py`, `execute.py`, `diagnose.py` | The outside-world edges |
| 6 | `adapters/` | Three leaks against a settled interface |
| 7 | `harness/` | Assignment, response model, runner, metrics |
| 8 | `cli.py`, `scenarios.py` | Surface |
| 9 | `tests/` | 115, written alongside from step 2 |

The gate landing before the executor was intentional. If the executor exists first,
it is possible to write a call path that does not go through the gate, and then the
"gate is the only path to money" invariant becomes something you have to remember
rather than something the code shape enforces.

---

## Bug 1 — the payday retry that fired one second later

**Found by** reading `razor-pay audit pf_0000` output, not by a test.

The audit trail showed two consecutive `RETRY_PAYDAY_WINDOW` actions on the same
case, one second apart:

```
[ 7] 2026-09-01T09:59:59  DECIDE   step 2/3: retry_payday_window, firing 2026-09-01T09:59:59
[ 9] 2026-09-01T09:59:59  EXECUTE  fired retry_payday_window
```

**Cause.** `seconds_to_next_payday()` searched for the next payday at 10:00. When
the case clock had already advanced to 09:59:59 on the 1st, "next payday" resolved
to 10:00 *that same morning* — one second away.

**Why it mattered more than it looks.** The entire argument for the
`insufficient_funds` ladder is that timing is the intervention. Two retries a second
apart is a blind retry loop wearing a policy label. The project's central claim —
"we are not a retry loop" — was quietly false for the most common root cause in the
batch.

**Fix.** A minimum gap, so a payday retry must clear a real interval before counting
as a different attempt:

```python
MIN_PAYDAY_GAP_SECONDS = 12 * 3600

def seconds_to_next_payday(now, min_gap_seconds=MIN_PAYDAY_GAP_SECONDS):
    earliest = now + timedelta(seconds=min_gap_seconds)
    ...
    if candidate >= earliest:
```

The original also had a structural flaw: it walked months looking for one day at a
time and `break`ing on first match, which could miss the nearer of the two payday
days. Rewrote it to enumerate all candidates across four months and take the
minimum.

**Regression tests.** `test_consecutive_payday_retries_target_different_windows`
pins the exact failing moment (09:59:59 on the 1st). A parametrized test walks all
28 candidate days of a month and asserts the delay always clears the gap *and*
lands on a payday.

**Lesson.** The audit trail found a bug the test suite missed. That is an argument
for the audit trail being a development tool and not only a compliance artifact —
if the ledger had been a log file, this would have shipped.

---

## Bug 2 — arm assignment inside the adapter

**Found by** writing the adapter and disliking the shape.

The first `payment_failure.py` set `arm=Arm_placeholder()` — a function returning
`Arm.CONTROL` — because `RecoveryCase` required the field but adapters have no
business assigning it.

**Why it mattered.** An adapter that assigns arms can, in principle, assign based on
something it observed about the case. That is the exact failure mode the intake-time
assignment invariant exists to prevent, and leaving the hook there invites a future
edit to use it.

**Fix.** `arm` defaults to `Arm.CONTROL` in the schema with a comment stating that
the harness assigns it at intake. The placeholder is gone. The conservative default
matters: an unassigned case is held out rather than acted on.

---

## Bug 3 — cause profiles overwritten by dict comprehension

**Found by** a smoke test printing the wrong explanatory note.

```python
CAUSE_PROFILES = {p.root_cause: p for p in ERROR_CODE_MAP.values()}
```

Several error codes map to the same root cause — `invalid_vpa`,
`vpa_resolution_failed`, `card_expired` and `invalid_card` all produce
`INSTRUMENT_INVALID`. The comprehension kept the **last** one, so
`profile_for(INSTRUMENT_INVALID).note` returned "Instrument unusable" instead of the
more specific "Customer is not a valid UPI user. Retrying can never succeed."

**Impact.** Cosmetic but user-facing: the note appears in every `DECIDE` ledger entry
and in the audit output, which is the artifact a panel actually reads.

**Fix.** `setdefault` in an explicit loop, so the first (most specific) profile wins.

---

## Bug 4 — re-running a batch silently destroyed the report

**Found by** re-running `razor-pay run` after a formatting change.

Idempotency works: the second run fires nothing. But it also regenerated the report
from an empty result set, overwriting a good report with zeros. Correct behaviour,
terrible ergonomics — and a live-demo hazard.

**Fix.** `run` refuses a batch that already has ledger entries, with a message
explaining *why* a re-run would be empty and offering `--force`:

```
Error: Batch batch_20260823_202337 has already run (3052 ledger entries).
Idempotency guarantees a re-run fires nothing, so the report would be empty.
Seed a fresh batch, or pass --force to regenerate anyway.
```

**Lesson.** A correct invariant with a confusing surface is still a bug. The failure
mode here was "the demo shows zeros in front of the panel."

---

## Bug 5 — "No refusals" read as "gate untested"

**Found by** reading the generated report as an outsider.

The report printed `_No refusals in this batch._` — technically true, and it made
the gate look like dead code. The batch had actually deferred 40 contacts for the
RBI window; deferrals just were not surfaced in that section.

**Fix.** Renamed the section to "Gate activity", surfaced the deferral count, and
replaced the bare negative with an explanation of the expected shape:

> No hard refusals fired in this batch. That is the expected shape: the policy
> engine's stopping rules retire a case before it can reach a mandate limit, so the
> gate acts as a backstop rather than the primary control. `razor-pay demo-refusals`
> exercises all four refusal paths directly.

**Lesson.** Two independent limits (ladder depth and mandate cap) means the tighter
one always fires first. That is correct defence-in-depth, but a report has to say so
or it reads as untested code.

---

## Bug 6 — the efficiency optimisation that destroyed value

**Found by** measuring the thing that was supposedly being improved.

`docs/07-next-steps.md` listed "optimise the cost ratio" as the highest-value work
after verification: 6.60 actions per attributable recovery is a bad-looking number.

Built `economics.py` — action costs per channel, a believed-uplift table, and an
expected-value stopping rule in `policy.decide()`. Set the threshold at 2.0, on the
intuition that an action should look *clearly* worthwhile rather than merely
break-even.

It worked, in the sense that the ratio improved: **6.60 → 6.31**.

Then swept the threshold and measured **net value** (incremental recovery minus
what the actions cost) instead of the ratio:

| threshold | actions | action cost | incremental | **net value** | act/rec |
|---:|---:|---:|---:|---:|---:|
| 0.0 (off) | 429 | Rs 3,486 | Rs 85,321 | **Rs 81,836** | 6.60 |
| 1.0 | 407 | Rs 2,936 | Rs 84,571 | **Rs 81,636** | 6.36 |
| 1.5 | 389 | Rs 2,496 | Rs 82,782 | **Rs 80,287** | 6.38 |
| 2.0 | 372 | Rs 2,080 | Rs 81,394 | **Rs 79,314** | 6.31 |
| 3.0 | 355 | Rs 1,666 | Rs 78,403 | **Rs 76,738** | 6.34 |

**The optimisation was destroying money.** At threshold 2.0 it saved Rs 1,406 in
action costs and gave up Rs 3,927 in recovery — a net loss of Rs 2,522, while the
ratio it was aimed at got *better*.

**Why.** Action costs are tiny relative to ticket sizes. A WhatsApp send costs
Rs 25 against an average ticket in the low thousands. Almost any action with a
non-trivial success probability pays for itself, so filtering on a margin above
break-even mostly discards profitable actions.

**Fix.** Threshold moved to 1.0 — pure break-even, which encodes a *principle*
("never fire an action that loses money in expectation") rather than a tuning
parameter. A ticket floor for contacting customers was tested at Rs 100 and Rs 200
and removed: it cost net value at every level.

The deeper fix was to the **metric**. The report now leads its cost section with
net value and explicitly labels actions-per-recovery as reported-but-not-optimised-
against. `test_break_even_threshold_is_a_principle_not_a_tuning_knob` guards
against someone re-tuning it upward later.

**Lesson.** A ratio improves when its denominator is cut. That is not the same as
making money, and optimising a ratio without checking the absolute number is how
efficiency metrics quietly destroy value. This is the most useful thing the project
learned about itself.

---

## Bug 7 — the compliance check that could never fire

**Found by** building the per-customer contact budget and watching it do nothing.

Added a contact budget capping customer contacts across *cases*, not just within
one case — the obvious gap, since a customer with several failed payments would
otherwise receive several independent sequences, each individually compliant.

The batch report showed zero refusals from it.

**Cause.** Seeding drew customer refs as `cust_{rng.randint(1000, 9999)}` — a
9,000-wide pool for 400 cases. Repeat customers were essentially impossible, so no
customer ever owned enough cases to hit a cross-case budget.

**Why it mattered beyond the test.** The seeded population was unrealistic in a way
that flattered the engine. Real merchants have repeat customers, and a customer
whose payment failed once is disproportionately likely to fail again — same thin
balance, same dead instrument, same flaky issuer. A uniform draw across a wide pool
models a world with no repeat offenders, which is not the world.

**Fix.** A 120-customer pool with a heavy tail: 35% of cases draw from the troubled
sixth of the population. The batch now has customers owning up to 15 failing cases,
and the contact budget refuses 8 contacts.

**Lesson.** A compliance check that never fires is indistinguishable from one that
does not work. When a new guard shows zero activations, suspect the test data
before concluding the guard is unnecessary.

---

## Bug 8 — the two-batch flow the error message recommends

**Found by** following the project's own advice.

`run` refuses to re-run a completed batch and says "seed a fresh batch". Doing
exactly that crashed:

```
sqlite3.IntegrityError: UNIQUE constraint failed: cases.case_id
```

**Cause.** Case ids were `pf_0000`, `pf_0001`, … regenerated identically for every
batch, and `cases.case_id` is a primary key.

**Why it mattered beyond the crash.** The ledger joins on `case_id` alone. Repeated
ids across batches would have made one batch's contacts count against another
batch's customer contact budget — silently wrong rather than loudly broken, which
is worse.

**Fix.** Case ids carry the batch's time token: `pf_134512_0000`. `audit` gained a
resolver so short ids still work, preferring the current batch.

That resolver had a bug of its own: `_` is a single-character wildcard in SQL
`LIKE`, so an unescaped `pf_0000` matched nothing at all rather than matching
`pf_134512_0000`. Escaped now, with `test_partial_case_id_resolves_despite_underscore_wildcard`
pinning it.

---

## Bug 9 — a compliance window scoped to a batch

**Found by** running two batches back to back once Bug 8 was fixed.

The per-customer contact budget filtered on `batch_id`. So a customer contacted
four times in batch A had a full budget again in batch B, minutes later.

**Why it mattered.** A customer does not experience batch boundaries. Four contacts
yesterday and four today is eight contacts to them, however the runs were
organised — and that is the regulator's view too. Batch scoping made the budget an
accounting artifact rather than a protection.

**Fix.** The window is wall-clock across the whole ledger. The effect is immediate
and visible: the second batch now refuses 14 contacts that the first batch's
activity had already spent, up from 1.

---

## Bug 10 — a preflight check that could not fail correctly

**Found by** running `verify` on a fresh database.

```
Append-only ledger: FAIL (delete succeeded)
```

**Cause.** The probe ran `DELETE FROM ledger` on an **empty** table. A
`BEFORE DELETE` trigger fires per row, so with no rows it never fires, the delete
"succeeds", and the check reports failure.

**Why it mattered.** `verify` is the first command anyone runs, and it was
reporting that the project's headline safety property was broken — on a correct
system.

**Fix.** Write a probe row inside a `SAVEPOINT`, assert both `UPDATE` and `DELETE`
are refused, then roll back so the real ledger is untouched.

---

## Bug 11 — the headline was one draw, not a result

**Found by** re-running the pipeline from clean and getting +17.0 pp where the
README claimed +30.4 pp.

**Investigation.** Ran 12 seeds at n=400:

| | lift |
|---|---|
| mean | +26.1 pp |
| sd | 4.4 pp |
| range | +17.4 to +31.7 pp |

**The finding.** Both numbers were honest draws from a process centred around +26.
The README had quoted a lucky one. Any reviewer re-running a single batch would
have landed somewhere else in that range and reasonably concluded the numbers were
unreliable.

Notably this was **not** a bug in the CI: each batch's own 95% interval is about
the right width for the observed spread. The error was reporting a point estimate
from one batch as though it were the result.

**Fix.** `razor-pay replicate` runs independent batches and pools them, reporting
the mean with a CI of the mean *and* the raw between-batch spread. The headline is
now +24.7 pp (95% CI 22.8 to 26.6) over 12 batches, with the +18.8 to +29.8 range
stated alongside so nobody is surprised by a single re-run.

**Lesson.** The most dangerous number is the one that is individually defensible
and collectively misleading. A confidence interval describes uncertainty *within* a
sample; it says nothing about having picked the sample that flattered you.

---

## First contact with the real API

Everything up to this point ran against the simulated executor. The day the
`rzp_test_` credentials arrived, the loop ran end to end against real Razorpay
test mode for the first time — and four things broke that simulation had never
exercised. All four are worth recording, because each is a case where the
*simulator was more permissive than reality*.

### Bug 12 — rate limiting, caught by luck

A 400-case seed makes 400 sequential order-creation calls. It drew **124 "Too many
requests" responses**.

Retry with jittered exponential backoff had been added an hour earlier as
speculative prep, on the reasoning that "400 sequential calls with no backoff is
the most likely thing to break." It absorbed all 124 and the seed completed. Without
it the run would have died partway and left a half-populated batch.

**Follow-up fix.** Recovering from a limit 124 times is not the same as not hitting
it. Added a `Throttle` that paces calls to a minimum interval, so the run avoids the
limit rather than repeatedly discovering it.

**Lesson.** The prep was speculative and it paid for itself on the first real run.
Worth remembering next time the temptation is to skip hardening because the happy
path works.

### Bug 13 — an intervention the executor could not perform

```
ValueError: No Razorpay action defined for soft_nudge
```

`SOFT_NUDGE` is in the policy ladder for `CUSTOMER_CANCELLED`, but the executor's
dispatch only knew `LINK_TYPES` and `ORDER_TYPES`, and `SOFT_NUDGE` was in neither.
Seventeen cases hit it.

**Why simulation missed it.** `SimulatedExecutor` computed
`kind = "link" if type in LINK_TYPES else "order"` — a total function that silently
classified the unknown type as an order and returned a plausible fake id. The gap
existed from the day the ladder was written and was invisible for the entire build.

**Fix.** A soft nudge is a customer contact that should still give them a way to
pay, so it maps to a link like the others.

**Lesson.** A permissive stub is worse than a strict one. The simulator should have
raised on an unmapped type exactly as the real client did.

### Bug 14 — not every 5xx is transient

The retry classifier checked `500 <= status < 600` and treated it as retryable.
Razorpay returns its test-mode payment-link quota as a `ServerError`, so every
quota failure burned all five attempts on something that could never succeed —
slowing the run and obscuring the real cause.

**Fix.** An explicit permanent-condition list checked *before* the status code:
quota exhaustion, authentication failure, invalid key. Conservative in the right
direction — anything not positively identified as permanent is still retried.

### Bug 15 — an account quota masquerading as a policy result

The most consequential finding.

**Razorpay test mode allows 30 Payment Links per business, for the lifetime of the
account.** Documented, not resettable by backoff, and support must be contacted to
raise it. The first 400-case live run spent the entire budget.

Every subsequent link-type intervention then failed, the runner retired those cases
as execution failures, and the reported lift fell to +15.0 pp — below the entire
simulated range. **The number looked like a policy result and was actually an
account quota.** That is precisely the class of error the project exists to avoid,
arriving from an unexpected direction.

**Fix.** Three parts:

1. The executor detects quota exhaustion once, then **degrades**: it records a
   placeholder flagged `degraded: true` rather than failing the case. The
   measurement survives; the artifact claim does not silently inflate.
2. The report states how many artifacts were degraded and why, in the same section
   that distinguishes real from modelled.
3. `run` warns before starting a real batch large enough to exceed the quota, and
   points at `replicate` for statistical claims.

**The resulting honest split**, which is what should be said to a panel:

| | |
|---|---|
| Real-API run | proves the integration: real orders, real ids, real error codes, real rate limits handled |
| `replicate` (simulated) | provides the statistical claim, +24.7 pp pooled |

Claiming one live run gave both would have been the overreach. A 60-case live run
came in at +12.7 pp with a 95% CI of −12.8 to +38.2 — wide enough to be worthless
as evidence, exactly as the batch-size calibration predicted.

### Bug 16 — a shadowed parameter that hid the whole finding

`compute()` gained a `degraded_artifacts` parameter, but the counter-initialisation
block a few lines below still had `degraded_artifacts = 0`, shadowing it. The report
cheerfully printed "No degraded artifacts: every action produced a real entity"
while 17 placeholders sat in the ledger.

A three-line bug that would have turned the honest disclosure into a false claim.
`test_degraded_artifacts_are_reported_not_swallowed` pins both branches.

---

## Calibration: batch size

Not a bug — a measurement that changed the defaults. Ran the full pipeline at four
sizes:

| n | control | 95% CI | Verdict |
|---:|---:|---|---|
| 100 | 30% | −3.2 to +39.6 pp | spans zero |
| 200 | 40% | +15.6 to +40.8 pp | significant |
| 400 | 40% | +20.9 to +38.2 pp | significant |
| 800 | 40% | +16.5 to +29.1 pp | tightest |

The original defaults (100 cases, 30% control) produced an interval spanning zero.
Shipping that as a headline would have been the exact error a technical panel looks
for. Defaults are now 400 at 40%.

---

## Verified: the payment-link quota cannot be reclaimed

Bug 15 left an open question — is the 30-link cap a live-object limit or a
lifetime counter? If it counted live objects, cancelling old links would free
budget and the live run could be re-run at scale. Worth knowing before asking
support for anything.

Probed directly rather than reasoned about:

```
links visible: 0    total_count=None
CREATE FAILED -> ServerError test mode limit of 30 reached for payment_link
```

**The account holds zero payment links and is still blocked.** So it counts
creations over the lifetime of the business, and no amount of cleanup recovers
it. Three consequences:

- Deleting or cancelling links is not a workaround; there is nothing to delete.
- Rotating the API keys is not a workaround either. Keys authenticate *to* an
  account; the quota belongs *to* the account, so new credentials for the same
  business inherit the same exhausted counter.
- Registering a second Razorpay account would work and was deliberately not
  done. It is a hiring submission tied to a real identity, and 30 more links
  would not change the statistical picture anyway — the statistic comes from
  `replicate`. Asking support to raise the cap is the only clean path.

Incidentally the probe re-confirms Bug 14: the quota arrives as a `ServerError`,
a 5xx that is permanently true. Exactly why `retry.is_transient` consults a
permanent-condition list before it looks at the status code.

The generalisable point: an assumption about a vendor limit is cheap to test and
expensive to be wrong about. One API call settled a question that would otherwise
have been argued from documentation.

---

## Stating the sensitivity to the one unmeasurable parameter

`economics.BELIEVED_UPLIFT` was always the softest thing in the repo. Every
expected-value decision rests on it, and it cannot be measured without production
data. The temptation was to defend the numbers. The better move — already made
once for the control baseline — is to report what would change if they are wrong.

`razor-pay uplift-sensitivity` does that across a half-to-double band. The result
was more interesting than expected:

**8 of 18 ladder steps are sensitive to the belief. All 8 cost money to fire. All
10 free steps are stable.**

That is not a coincidence, it is the mechanism: belief only decides anything when
there is a cost to weigh it against. A rail-side retry is close enough to free
that no plausible uplift refuses it. A WhatsApp message has to earn its place, so
the estimate is load-bearing there and nowhere else. The report derives this
pattern rather than asserting it — if a free step ever became sensitive, the
prose changes, and `test_free_actions_are_never_sensitive_to_the_belief` fails.

The second half was the useful one. Ranking ladder steps by expected value has
been on the next-steps list since the economics work. Perturbing each belief
independently and checking whether the top-ranked step holds gives:

| Cause | Top step unchanged |
|---|---:|
| `collect_expired` | 98.6% |
| ...five between | 83–86% |
| `abandoned_no_attempt` | 72.3% |

Only one ladder is stable enough for a computed ranking to mean anything. At 72%
the top step is close to a coin flip. **That is an argument against building the
feature**, and it is the first time the sensitivity analysis has talked us out of
work rather than into it. Ladder order stays a stated policy choice, which is
also easier to defend in an interview than a number derived from a guess.

Note this module reads only the engine's belief table, never
`response_model.UPLIFT`. Importing the latter would quietly convert a sensitivity
analysis into a scoring against an answer key —
`test_uplift_sensitivity_never_reads_harness_truth` parses the module's imports
to prevent it, since a stray import is exactly the accident that would do it.

---

## The confusion matrix said something the accuracy figure hid

Diagnosis accuracy was reported as a single percentage. Splitting it revealed two
things that the blended number actively disguised.

**First, where the error lives.** By method:

| Method | Scored | Correct | Accuracy |
|---|---:|---:|---:|
| deterministic | 222 | 222 | 100.0% |
| fallback | 21 | 5 | 23.8% |

The deterministic path is a lookup table on a documented error code; it is right
by construction. Averaging it with the ambiguous slice produced a number that
described neither. The split says *improve the fallback*, which the blend did not.

**Second, and more important, what shape the error takes.** Every off-diagonal
cell in the matrix lands on `UNKNOWN`. Not one case was diagnosed as the wrong
*cause*.

Those are different failures wearing the same percentage:

- Wrong-as-`UNKNOWN` routes to the exception list. It costs a recovery, and a
  human sees it.
- Wrong-as-another-cause fires a confident, specific, wrong intervention. It
  costs money and customer goodwill, and nobody sees it.

The second is precisely the failure the cause-keyed design exists to prevent, and
its count is zero. So the headline *understates* the safety property: the residual
is abstention, not error. That sentence is worth more in an interview than a
higher accuracy number would be.

The report computes the distinction instead of asserting it. If a confident
misdiagnosis ever appears, the prose flips and names the count.

---

## What the tests actually guard

141 tests, but a handful carry most of the weight:

| Test | Invariant |
|---|---|
| `test_never_retries_an_instrument_that_cannot_work` | Policy never contradicts the taxonomy, across every cause |
| `test_every_cause_resolves_to_action_or_explicit_stop` | The policy table is total — no silent fallthrough |
| `test_gate_never_raises_on_any_intervention_type` | Refusals are values, across the full intervention × channel product |
| `test_diagnoser_never_reads_injected_ground_truth` | Ground truth stays invisible to components under test |
| `test_update_is_refused` / `test_delete_is_refused` | Append-only is enforced by the database |
| `test_rerunning_a_batch_executes_nothing_further` | Idempotency holds end to end |
| `test_control_arm_never_fires_an_action` | The held-out arm is genuinely held out |
| `test_control_baseline_is_not_zero` | The control group could actually have recovered |
| `test_metrics_report_incremental_not_gross` | Incremental ≤ gross, and a control arm exists |
| `test_engine_belief_is_not_harness_truth` | The policy does not have perfect knowledge of the simulated customer |
| `test_break_even_threshold_is_a_principle_not_a_tuning_knob` | Guards Bug 6 from being reintroduced |
| `test_contact_budget_is_counted_across_cases_for_one_customer` | The per-customer cap actually spans cases |
| `test_contact_budget_spans_batches` | Compliance windows are wall-clock, not batch-scoped |
| `test_case_ids_are_unique_across_batches` | Two seeds do not collide (Bug 8) |
| `test_partial_case_id_resolves_despite_underscore_wildcard` | `_` escaped in LIKE (Bug 8) |
| `test_replication_is_deterministic` | The pooled headline is reproducible |
| `test_degraded_artifacts_are_reported_not_swallowed` | A spent link quota is disclosed, not hidden (Bug 16) |
| `test_uplift_sensitivity_never_reads_harness_truth` | The sensitivity analysis cannot become a scoring against ground truth |
| `test_free_actions_are_never_sensitive_to_the_belief` | The belief is load-bearing only where an action costs something |
| `test_confusion_matrix_accounts_for_every_scored_case` | The matrix partitions the scored cases rather than sampling them |
| `test_report_distinguishes_abstention_from_confident_error` | A confident misdiagnosis can never be averaged away |

Several are parametrized across every enum member, so adding a `RootCause`,
`InterventionType` or `Channel` without handling it fails the suite rather than
silently doing nothing.

---

## Commit history

The first ten commits were staged by subsystem — types, policy, gate, ledger,
executors, adapters, harness, CLI, tests, README — so the history reads as
deliberate work in an architecture review rather than one dump. That sequence was
handed over as a script to run locally because commit signing needed the user's GPG
key. It was ultimately squashed into a single `v1` commit by the user; subsequent
commits (CLAUDE.md, these docs) are unsigned at the user's request via
`--no-gpg-sign`.
