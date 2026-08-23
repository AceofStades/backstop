# Revenue Recovery Agent

**Razorpay AI Buildathon 2026 — Track 03, AI Revenue Recovery**

A bounded, audited agent that detects revenue at risk, diagnoses *why* it is at
risk, chooses an intervention matched to that cause, executes it inside hard
limits, and measures what it actually recovered against a held-out control arm.

Built on Razorpay test-mode APIs. One engine, three leak adapters, one policy
layer, one append-only ledger.

---

## The claim

On a 400-case batch, against a 162-case held-out control group:

| | |
|---|---|
| **Incremental recovery** | **+29.6 pp** (95% CI +20.9 to +38.2) |
| **Incremental money recovered** | **Rs 85,321** |
| Gross recovered in treatment | Rs 138,951 |
| ...of which would have arrived anyway | Rs 53,630 |
| Diagnosis accuracy vs ground truth | 95.4% over 238 cases |
| Actions fired per attributable recovery | 6.60 |
| Exceptions it could not resolve | 17 of 238 |

The number that matters is the **incremental** one. Gross recovery overstates the
agent by Rs 53,630, because a real customer whose payment fails often just tops up
and retries without anybody doing anything. An agent that takes credit for those
is measuring the world, not itself.

Reproduce: `uv run razor-pay seed --cases 400 --simulated && uv run razor-pay run --simulated`

---

## Why this is not a retry loop

Three design choices carry the project.

### 1. The failure reason picks the intervention

Razorpay publishes a failure taxonomy. It is the most useful signal in the whole
pipeline, and blind retry loops throw it away.

| Root cause | What the agent does | Why |
|---|---|---|
| `insufficient_funds` | Defer to the next payday window | Balance replenishes on a schedule. Repetition does not help; timing does. |
| `issuer_downtime` | Back off, widening each time | Transient issuer fault. |
| `instrument_invalid` | **Never retry.** Send an instrument-update link | A dead VPA cannot be charged. A retry is pure cost at zero expected recovery. |
| `customer_cancelled` | Exactly one soft touch, then stop forever | Possible genuine intent not to buy. |
| `collect_expired` | Re-present with a shorter window | Timing, not capability. |
| unclassified | **Stop** and list it as an exception | Honest failure beats a confident guess. |

Every cause maps to an *escalation ladder* indexed by attempts already made, so
the third action on a case differs from the first. Running off the end of a
ladder is a stopping rule, not a loop exit.

`test_never_retries_an_instrument_that_cannot_work` asserts across every root
cause that the policy never selects a retry the taxonomy says cannot succeed.

### 2. Compliance is enforced in code, not left to the model

Indian recovery communication is regulated, so the constraints are structural:

- **RBI**: no customer contact outside **08:00–19:00** local time — voice, SMS and
  instant messaging alike.
- **TRAI TCCCPR**: every A2P message must carry a **DLT-registered template id**.

Both are checked in `mandate.check()`, which sits in front of every money-moving
and customer-contacting call. The LLM proposes; the gate disposes. It cannot be
argued out of a closed contact window, because it is never asked.

A contact scheduled into a closed window is **deferred**, not dropped, and the
deferral is logged with the new fire time.

### 3. The measurement has a control arm

Assignment happens **at intake** — before diagnosis, before any policy sees the
case — by deterministic hash of the case id. Assigning later would let the
engine's own behaviour decide who lands in which arm, which is how a recovery
experiment quietly reports its selection effect as a result.

The response model uses **common random numbers**: a case's self-recovery draw is
seeded from its case id, so the same case makes the same self-recovery decision
in either arm. The measured difference is attributable to the intervention alone.

A treated case recovers if it *either* would have self-recovered anyway *or* an
intervention landed. Only the second gets credit.

---

## What is real and what is modelled

This is the first thing to ask about any hackathon metric, so it is stated up
front rather than buried.

**Real.** Every Order and Payment Link is a genuine Razorpay test-mode entity with
a live id, created through the API and verifiable in the dashboard. The failure
taxonomy is Razorpay's published one. The policy decisions, the gate checks, the
idempotency and the ledger are all real code paths under test.

**Modelled.** Whether a customer pays after an intervention. Test mode will create
an Order but it will not produce a synthetic human who decides to pay it. Those
draws come from an explicit parameter set in `harness/response_model.py`.

**Injected.** The specific decline reason on each seeded case, drawn from a
documented distribution, because test mode cannot be made to emit a chosen issuer
decline on demand. It is stored as `injected_cause` and used for exactly one
purpose — scoring the diagnoser afterwards. `test_diagnoser_never_reads_injected_ground_truth`
asserts no component under test reads it.

Because the response layer is modelled, the control baseline is an **assumption**,
so the headline is reported across a sensitivity sweep rather than as a point
estimate:

| Baseline scale | Control rate | Treatment rate | Lift |
|---:|---:|---:|---:|
| 0.50x | 7.4% | 42.0% | +34.6 pp |
| 0.75x | 11.1% | 45.4% | +34.3 pp |
| 1.00x | 17.9% | 47.5% | +29.6 pp |
| 1.25x | 23.5% | 50.0% | +26.5 pp |
| 1.50x | 28.4% | 53.4% | +25.0 pp |

The lift survives every baseline tested. That is a stronger claim than any single
number.

---

## Architecture

```
Detector (leak adapter)  ->  RecoveryCase
  -> Diagnoser     -> RootCause + confidence   (deterministic first, LLM fallback)
  -> Policy engine -> Intervention | STOP      (escalation ladder, no default retry)
  -> Mandate gate  -> ALLOW | REFUSE(code)     (caps, velocity, RBI window, DLT)
  -> Executor      -> Razorpay test-mode call  (idempotent)
  -> Ledger        -> append-only entry
  -> Harness       -> outcome vs control, metrics
```

**One engine, pluggable leaks.** Every adapter emits the same `RecoveryCase`, so a
second leak type costs an adapter rather than a second pipeline:

- `payment_failure` — a one-off payment that declined
- `subscription_dunning` — a recurring auto-debit that bounced; adds the retry-*timing*
  axis and proves the abstraction generalises
- `checkout_abandonment` — no decline to interpret at all; detection is the
  *absence* of a capture

**The LLM does judgment; deterministic code moves money.** The model only ever
produces a `Diagnosis`, constrained to a fixed enum, and its confidence is capped
below the deterministic path — a model's stated certainty is not the same class of
evidence as a documented error code. Below a 0.60 confidence threshold the engine
refuses to act and routes the case to the exception list.

| Module | Role |
|---|---|
| `taxonomy.py` | Razorpay error codes -> root causes, with retryability |
| `diagnose.py` | Deterministic map, LLM fallback, confidence routing |
| `policy.py` | Escalation ladders and stopping rules |
| `mandate.py` | The gate: caps, velocity, RBI window, DLT templates |
| `execute.py` | Razorpay test-mode client, idempotency |
| `store.py` / `ledger.py` | Case state and the append-only audit trail |
| `harness/` | Arm assignment, response model, batch runner, metrics |

---

## Safety properties

- **Test-mode interlock.** A key that is not `rzp_test_` is refused before a
  client is constructed. Misconfiguration fails closed instead of moving real money.
- **Append-only ledger, enforced by the database.** `UPDATE` and `DELETE` against
  the ledger raise, via SQLite triggers. Corrections are new rows. Not a convention.
- **Idempotency.** Every call carries a `<case_id>:<attempt_no>` key under a unique
  index, so re-running a batch cannot double-charge or double-contact.
- **Refusals are values, not exceptions.** The gate returns a structured
  `RefusalCode` with the list of checks it performed. `test_gate_never_raises_on_any_intervention_type`
  asserts this across the full cross product of intervention types and channels.
- **Notifications are off.** Payment links are created with `notify: {sms: false,
  email: false}`. The compliance logic is demonstrated by the gate and the ledger;
  no synthetic customer is ever actually messaged.

---

## Failures handled gracefully

`uv run razor-pay demo-refusals` runs four scenarios. Each is refused cleanly with
a reason code, logged, and moves no money:

1. Action exceeds the mandate's per-action ceiling
2. Case has reached its attempt cap
3. Contact would fire outside the RBI 08:00–19:00 window — **deferred**, with the
   new fire time logged
4. Mandate has expired

---

## Running it

```bash
uv sync --extra dev
cp .env.example .env      # add rzp_test_ credentials; optional ANTHROPIC_API_KEY
```

```bash
uv run razor-pay verify                          # preflight: interlock, API, ledger triggers
uv run razor-pay seed --cases 400                # creates real test-mode Orders
uv run razor-pay run                             # runs the loop, writes reports/<batch>.md
uv run razor-pay report                          # print the report
uv run razor-pay audit pf_0000                   # full audit trail for one case
uv run razor-pay demo-refusals                   # the four refusal scenarios
uv run pytest                                    # 115 tests
```

Add `--simulated` to `seed` and `run` to skip Razorpay entirely, and `--no-llm` to
skip the fallback classifier. Both run fully offline, which is how CI runs.

### Batch size

Defaults are 400 cases with a 40% control arm. That is a deliberate choice: at 100
cases with a 30% control the 95% interval spans zero, and an underpowered
experiment is not evidence. The batch is powered from roughly 200 cases up.

---

## Honest limitations

- Customer response is modelled, not observed. The action layer is real; the
  response layer is not.
- The control baseline is an assumption. Mitigated by the sensitivity sweep, not
  eliminated by it.
- Test mode does not reproduce real issuer downtime distributions, so
  timing-based interventions are argued directionally rather than proven.
- 6.60 actions per attributable recovery is a high cost ratio. The contact-cost
  side of the ledger is measured but not yet optimised against.
- The uplift parameters encode a belief about which interventions work. They are
  stated explicitly so they can be argued with, but they are not measured from
  production data.

---

## An audit trail

```
[ 1] DETECT    payment_failure: 122200 paise at risk (arm=treatment).
[ 2] DIAGNOSE  error_code 'insufficient_funds' maps to 'insufficient_funds'.
                Balance may replenish; timing matters more than repetition.
[ 3] DECIDE    step 1/3: retry_payday_window, firing 2026-09-01T10:00
[ 4] GATE      ALLOWED  checked: stopped, expiry, scope, attempt_cap,
                                 per_action_ceiling, velocity_cap
[ 5] EXECUTE   fired retry_payday_window   idem: pf_0000:1
[ 7] DECIDE    step 2/3: retry_payday_window, firing 2026-09-15T10:00
...
[11] DECIDE    step 3/3: send_payment_link via whatsapp
[13] EXECUTE   fired send_payment_link     idem: pf_0000:3
[16] STOP      Escalation ladder exhausted after 3 attempts. Stopping rule reached.
```

Every entry answers: which actor, under which mandate, under which policy
version, what was checked before it fired, and what happened.
