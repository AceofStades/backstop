# Measurement

What the numbers are, how they are produced, and — the part that matters most —
what they do not prove.

---

## The three-layer evidence split

State this before any number. It is the first thing a payments panel will probe,
and volunteering it is worth more than any headline figure.

### Real

Genuine Razorpay test-mode API state:

- Every `Order` and `PaymentLink` created by seeding or by an intervention is a real
  test-mode entity with a live id, verifiable in the dashboard
- The failure taxonomy is Razorpay's published one, not invented
- Policy decisions, gate checks, idempotency, and the ledger are real code paths
  under test — not narrated, not stubbed

### Modelled

**Whether a customer pays after an intervention.** Test mode will happily create an
Order; it will not produce a synthetic human who decides to pay it. Those draws come
from `harness/response_model.py` with parameters stated openly in the report.

### Injected

**The specific decline reason on each seeded case.** Test mode cannot be made to emit
a chosen issuer decline on demand, so the reason is drawn from a documented
distribution and stored as `injected_cause`.

It is used for exactly one purpose: scoring the diagnoser afterwards. No component
under test reads it. `test_diagnoser_never_reads_injected_ground_truth` sets a
deliberately contradictory value and asserts the diagnoser still reads the error
code.

> The temptation is to blur these — to let "real test-mode API" imply the whole
> pipeline is real. Do not. The blur is exactly what an experienced reviewer is
> listening for, and being caught at it costs more than the modelled layer does.

---

## Why a control arm at all

Real customers self-recover. They top up and retry, the issuer recovers on its own,
they pay through another channel. Some fraction of "recovered" cases would have
recovered with nobody doing anything.

An agent that reports gross recovery is claiming credit for those. On the current
400-case batch:

| | |
|---|---:|
| Gross recovered in treatment | Rs 167,251 |
| Would have arrived anyway (at control's rate) | Rs 64,381 |
| **Actually attributable to the agent** | **Rs 102,869** |

38% of the headline number is not the agent's work. Reporting the Rs 138,951 would
not be a rounding error; it would be a 63% overstatement of the thing being sold.

---

## Three properties that make it defensible

A control arm is easy to claim and easy to build wrong. Three specifics:

### 1. Assignment at intake

`assign_all()` runs in `seed`, **before any policy sees a case** — before diagnosis,
before the engine has observed anything. Assignment is a deterministic hash of
`case_id`, so it is reproducible and independent of iteration order.

Assigning later would let the engine's own behaviour influence who lands in which
arm. That is the classic way a recovery experiment quietly reports its own selection
effect as a result: treat the cases that look promising, hold out the ones that look
hopeless, then report the difference.

### 2. Common random numbers

A case's self-recovery draw is seeded from `case_id`, not from a global stream:

```python
def _rng(self, case_id: str, salt: str) -> random.Random:
    digest = hashlib.sha256(f"{self.seed}:{case_id}:{salt}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))
```

So the same case makes the **same** self-recovery decision whichever arm it lands
in. Arm assignment cannot leak into the outcome through the random stream, and the
measured difference is attributable to the intervention alone.

This is a variance-reduction technique borrowed from simulation practice, and it is
the difference between a control arm that controls for something and one that just
splits the data.

### 3. A non-zero control baseline

`BASELINE_SELF_RECOVERY` gives every root cause a non-zero probability of
unassisted recovery, from 5% (`INSTRUMENT_INVALID` — a dead VPA rarely fixes
itself) to 35% (`ISSUER_DOWNTIME` — transient faults clear).

**A control arm that recovers 0% by construction is worse than no control arm at
all.** It produces a spectacular-looking uplift that is a pure artifact of the
simulation, and it invites exactly one question: "so your control group could never
have recovered?" `test_control_baseline_is_not_zero` asserts every cause has a
positive baseline.

---

## Attribution

A treated case recovers if **either** it would have self-recovered anyway **or** an
intervention landed. Only the second gets credit:

```python
if recovered:
    trace.recovered_via = "self" if self_recovers else "intervention"
```

Self-recovery wins the tie deliberately. If a case would have recovered on its own,
the agent does not get to claim it merely because it also acted.

---

## The headline

```
Incremental recovery: +30.4 pp (95% CI +21.7 to +39.1)
Incremental money:    Rs 102,869
Net value:            Rs 100,297
```

The interval is a normal-approximation two-proportion CI. At n=238 treatment and
n=162 control that approximation is adequate; below roughly n=100 per arm it would
not be, which is part of why the defaults are what they are.

**Significance is reported, not assumed.** `lift_significant` is `lo > 0`, and the
report prints "NOT significant" when the interval spans zero rather than quietly
printing the point estimate.

---

## Sensitivity, because the baseline is an assumption

The control baseline is the weakest link: it is a parameter, not an observation. So
the headline ships with a sweep rather than alone.

| Baseline scale | Control rate | Treatment rate | Lift |
|---:|---:|---:|---:|
| 0.50× | 8.6% | 41.6% | +33.0 pp |
| 0.75× | 13.6% | 43.3% | +29.7 pp |
| 1.00× | 17.9% | 48.3% | +30.4 pp |
| 1.25× | 23.5% | 52.9% | +29.5 pp |
| 1.50× | 29.0% | 54.6% | +25.6 pp |

**The lift survives every baseline tested.** That is a stronger claim than any single
number, and it is the honest way to present a result that rests on an assumption.

The sweep is computed **offline**: policy decisions do not depend on the response
model, so the already-fired intervention sequence is replayed against a new baseline
without touching Razorpay again. `trace.interventions` exists for exactly this.

---

## The cost side, and a metric that was actively misleading

Recovery numbers without cost numbers are half a story.

| Metric | Value | Why it is reported |
|---|---:|---|
| **Net value** | **Rs 100,297** | Incremental recovery minus what the actions cost |
| Action cost | Rs 2,572 | 396 actions, 101 of them customer contacts |
| Attributable recoveries | 68 | Recoveries the agent actually caused |
| Actions per attributable recovery | 5.82 | Reported, *not* optimised against |
| Contacts deferred for RBI window | 34 | Compliance load |

**Net value is the headline, and actions-per-recovery is deliberately demoted.**

The original design had it the other way around, and that was a real mistake worth
recording. Tightening the expected-value threshold to make actions-per-recovery
look better did exactly that — 6.60 → 6.31 — while destroying Rs 2,522 of net
value, because the forgone recoveries were worth far more than the actions saved.

A ratio improves when you cut its denominator. That is not the same as making
money. The threshold now sits at break-even, where it encodes a principle rather
than a tuning knob, and the report leads with the absolute number.

Full sweep in [`05-worklog.md`](05-worklog.md).

### Why the engine's uplift beliefs are a separate table

`economics.BELIEVED_UPLIFT` is not `response_model.UPLIFT`, and
`test_engine_belief_is_not_harness_truth` keeps them apart.

If the policy engine read the harness's table it would have perfect knowledge of
customer behaviour, and any expected-value rule built on it would be measuring the
simulation rather than the policy. A real engine only ever has a prior, estimated
from history and wrong at the edges. Two tables that are allowed to disagree model
that honestly.

---

## Statistical power

Measured during the build rather than assumed:

| n | control fraction | 95% CI | Verdict |
|---:|---:|---|---|
| 100 | 30% | −3.2 to +39.6 pp | spans zero |
| 200 | 40% | +15.6 to +40.8 pp | significant |
| 400 | 40% | +20.9 to +38.2 pp | significant, tighter |
| 800 | 40% | +16.5 to +29.1 pp | significant, tightest |

Defaults are 400 cases at 40% control. An underpowered experiment is not evidence,
and a point estimate from one is the exact error a technical panel looks for.

---

## The exception list

17 of 238 treatment cases could not be resolved. Every one is listed with its
amount, what the diagnoser concluded, its confidence, how many actions fired, and
why it stopped.

Cases land here when:

- the diagnoser returned `UNKNOWN` (no recognised code, no usable free text)
- confidence fell below 0.60
- the cause has no ladder
- the gate refused

**These are a feature.** An agent that resolves 100% of cases is either
misclassifying or lying. `test_exceptions_are_listed_not_hidden` asserts every
exception carries a reason.

---

## What these numbers do not prove

Stated plainly, because volunteering limitations is worth more than defending
against them:

1. **Customer response is modelled, not observed.** The action layer is real; the
   response layer is not. No claim about real-world recovery rates follows.
2. **The uplift parameters encode a belief.** `UPLIFT` says a payday-timed retry is
   worth more than an immediate one for `insufficient_funds`. That is a plausible
   belief drawn from how salary credit works, but it is not measured from
   production data. It is stated explicitly so it can be argued with.
3. **The control baseline is an assumption.** Mitigated by the sweep, not eliminated.
4. **Test mode does not reproduce real issuer downtime distributions.** Timing-based
   interventions are argued directionally, not proven.
5. **Diagnosis accuracy is measured against injected labels.** It says the
   deterministic mapping is correctly implemented; it does not say Razorpay's error
   codes correspond to reality as cleanly as the taxonomy assumes.
