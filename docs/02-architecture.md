# Architecture

How the pipeline fits together, what each seam is for, and where to make a change
without breaking an invariant.

---

## The pipeline

```
Detector (leak adapter)  ->  RecoveryCase
  -> Diagnoser     -> RootCause + confidence     diagnose.py
  -> Policy engine -> Intervention | STOP        policy.py
  -> Mandate gate  -> ALLOW | REFUSE(code)       mandate.py
  -> Executor      -> Razorpay test-mode call    execute.py
  -> Ledger        -> append-only entry          ledger.py / store.py
  -> Harness       -> outcome vs control         harness/
```

Each stage has exactly one job and hands a typed object to the next. The types are
in `schemas.py` and are the real interface contract:

| Type | Produced by | Consumed by |
|---|---|---|
| `RecoveryCase` | adapters | everything downstream |
| `Diagnosis` | diagnoser | policy engine |
| `Decision` | policy engine | gate, executor, ledger |
| `GateResult` | gate | runner, ledger |
| `ExecutionResult` | executor | runner, ledger |

---

## Why `RecoveryCase` is the load-bearing type

Every adapter emits the same shape, whatever the leak. That is what makes the
multi-leak claim true rather than aspirational — the diagnoser, policy engine,
gate, executor and ledger never learn which leak they are serving.

```python
class RecoveryCase(BaseModel):
    case_id: str
    leak_type: LeakType
    merchant_id: str
    customer_ref: str
    amount_at_risk_paise: int
    entity_refs: dict[str, str]        # real Razorpay ids
    failure_evidence: FailureEvidence  # raw signal, uninterpreted
    detected_at: datetime
    arm: Arm = Arm.CONTROL             # assigned by the harness at intake
    injected_cause: RootCause | None   # ground truth, scoring only
    attempts_made: int
    stopped: bool
    recovered: bool
```

Two fields deserve attention.

**`failure_evidence` is raw, not interpreted.** The adapter records what Razorpay
said — error code, description, source, step, method. It does not decide what that
means. Interpretation is the diagnoser's job, and keeping the boundary clean is
what lets the diagnoser be scored honestly afterwards.

**`arm` defaults to `CONTROL` and is assigned by the harness.** An earlier draft had
adapters setting it, which was wrong: it would let something the adapter observed
influence assignment. The default is the conservative one — an unassigned case is
held out rather than acted on.

---

## Adding a leak type

Three steps, and the second is where people get it wrong.

1. Write the adapter in `adapters/`, emitting `RecoveryCase` objects.
2. Call `register(YourAdapter())` at module scope, and import the module in
   `adapters/__init__.py` for the side effect. Registration is import-time, so a
   module nobody imports is silently absent.
3. If the leak introduces a genuinely new failure mode, add the cause to
   `RootCause`, give it a `CauseProfile` in `taxonomy.py`, and add a ladder in
   `policy.PLAYBOOK`.

Step 3 is optional by design. A cause with no ladder **stops**, which is the
correct default. `test_every_cause_resolves_to_action_or_explicit_stop` enforces
that the table stays total: every enum member either has a ladder or is
deliberately absent from one.

**Worked example — checkout abandonment.** The hardest of the three to fit, because
there is no decline to interpret. It works because:

- `FailureEvidence` carries no `error_code`, only a description
- The diagnoser has a *structural* rule: an abandonment with no error code is
  `ABANDONED_NO_ATTEMPT` at deterministic confidence, no model call needed
- The taxonomy marks it `retryable_same_instrument=False` — nothing was ever
  charged, so there is nothing to retry
- Its ladder escalates straight to payment links

Total cost: one 73-line adapter, one taxonomy entry, one ladder. No pipeline change.

---

## The diagnoser's three tiers

`diagnose.py` tries the cheapest reliable thing first:

1. **Deterministic** — a recognised Razorpay error code is authoritative.
   Confidence 0.97. This handles ~92% of cases, which is the whole argument for
   why the taxonomy matters more than the model.
2. **Structural** — leak-type-specific rules that need no model (the abandonment
   case above).
3. **LLM fallback** — free-text evidence with no machine-readable code. Output is
   constrained to the `RootCause` enum and validated on return; anything outside
   the enum is discarded rather than coerced.

Everything else lands at `UNKNOWN` with confidence 0.0, which the policy engine
turns into a stop and the report lists as an exception.

**The LLM never sees `injected_cause`.** It is not in the evidence payload. This is
guarded by `test_diagnoser_never_reads_injected_ground_truth`, which sets a
*deliberately contradictory* ground truth and asserts the diagnoser still reads the
error code.

**Failure modes degrade, they do not crash.** A missing API key flips `use_llm` off
at construction. A network error, auth failure or rate limit returns `UNKNOWN` with
a rationale naming the exception type. An offline run with `--no-llm` behaves
identically to a run with no credentials.

---

## The policy engine is a table, not a function

`PLAYBOOK` maps each `RootCause` to a **list** of `Intervention` objects, indexed by
`attempts_made`. The list is an escalation ladder: the third action on a case is a
different action from the first, and running off the end is a stopping rule.

```python
RootCause.INSUFFICIENT_FUNDS: [
    Intervention(RETRY_PAYDAY_WINDOW),      # attempt 1
    Intervention(RETRY_PAYDAY_WINDOW),      # attempt 2 — a different payday
    Intervention(SEND_PAYMENT_LINK, WHATSAPP),  # attempt 3
]
```

`decide()` stops — with a stated reason, never silently — when:

- the case is already stopped or recovered
- diagnosis confidence is below threshold
- the cause has no ladder
- the ladder is exhausted
- the selected action cannot cover its own cost (`economics.is_worth_firing`)
- **the selected step is a retry the taxonomy says cannot succeed**

That last one is a guard against the table contradicting itself. If a future edit
adds a retry to the `INSTRUMENT_INVALID` ladder, `decide()` refuses it at runtime
*and* the test suite fails.

### Economics as a stopping rule

`economics.py` holds action costs per channel and the engine's *own* estimate of
per-intervention uplift — deliberately a different object from the harness's truth
table, so the policy is not built on perfect knowledge of the simulated customer.

The threshold is break-even: never fire an action whose expected gain is below its
own cost. It was briefly set higher, which improved the efficiency ratio and
destroyed net value; see [`05-worklog.md`](05-worklog.md) for the sweep.

### Payday timing

`seconds_to_next_payday()` exists because "retry when the customer has money" is a
real intervention and "retry again immediately" is not. Salary credit in India
clusters at month start and mid-month, so the ladder targets the 1st and 15th.

`MIN_PAYDAY_GAP_SECONDS = 12h` enforces that consecutive payday retries land in
*different* windows. This was a real bug — see [`05-worklog.md`](05-worklog.md).

---

## The gate is the only path to money

Nothing calls Razorpay without `mandate.check()` returning `allowed=True` first.
The gate runs checks in severity order and returns on the first failure:

| Order | Check | Refusal code |
|---:|---|---|
| 1 | case not permanently stopped | `CASE_PERMANENTLY_STOPPED` |
| 2 | mandate not expired | `MANDATE_EXPIRED` |
| 3 | merchant scope matches | `MERCHANT_SCOPE_MISMATCH` |
| 4 | attempt cap not reached | `ATTEMPT_CAP_REACHED` |
| 5 | amount ≤ per-action ceiling | `PER_ACTION_CEILING_EXCEEDED` |
| 6 | velocity window not breached | `VELOCITY_CAP_EXCEEDED` |
| 7 | channel consented | `CHANNEL_NOT_CONSENTED` |
| 8 | DLT template present | `CHANNEL_NOT_CONSENTED` |
| 9 | customer contact budget | `CUSTOMER_CONTACT_BUDGET_EXHAUSTED` |
| 10 | inside RBI contact window | `OUTSIDE_CONTACT_WINDOW` |

Checks 5–6 apply only to money-moving interventions; 7–10 only to
customer-contacting ones. Every `GateResult` carries `checks_performed`, so the
ledger records what was verified and not merely the verdict.

**Deferral, not refusal.** `OUTSIDE_CONTACT_WINDOW` is the one refusal the runner
treats as recoverable: it advances the case clock to the next open window and
re-gates, up to `MAX_DEFERRALS`. A message that would have violated the RBI window
gets sent at 08:00 rather than dropped.

---

## The runner's virtual clock

Each case carries its own clock, starting at `detected_at` and advancing by each
intervention's delay. This matters more than it first appears:

An action scheduled with a 6-hour backoff from a case detected at 16:00 lands at
22:00 — outside the contact window — even though the batch was started at noon. So
the compliance gate is exercised by **ordinary cases**, not only by the scripted
demo. Seeding deliberately spreads `detected_at` across 72 hours including
night-time, so this happens naturally.

Control-arm cases skip the loop entirely: they are recorded, drawn against the
response model, and closed. They never reach the diagnoser.

---

## Store and ledger

Two tables with different mutability rules, deliberately.

**`cases`** is mutable working state — attempts, stopped, recovered.

**`ledger`** is append-only, enforced by triggers:

```sql
CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE is forbidden'); END;
```

Every entry answers five questions: which actor, under which mandate, under which
policy version, what was checked before it fired, and what happened. That set is
the design target, and `razor-pay audit <case_id>` renders it directly.

Idempotency keys sit under a **partial** unique index (`WHERE idempotency_key IS NOT
NULL`), so the many non-execution entries can leave it null without colliding.

---

## Executors

One `Protocol`, two implementations. `RazorpayExecutor` asserts test mode in its
constructor, before a client exists. `SimulatedExecutor` produces stable fake ids
from a hash of the idempotency key.

`build_executor()` picks based on settings and the `--simulated` flag, so the same
runner code drives both. Tests always use the simulated one, which is why the suite
runs offline in 0.4 seconds.

**Payment links are created with notifications off.** The compliance story is
demonstrated by the gate and the ledger; no synthetic customer should actually be
messaged.

---

## Module map

| Module | Lines | Role |
|---|---:|---|
| `schemas.py` | 143 | Every type crossing a stage boundary |
| `taxonomy.py` | 145 | Razorpay error codes → causes, with retryability |
| `economics.py` | 118 | Action costs, the engine's believed uplift, break-even stopping |
| `diagnose.py` | 179 | Three-tier classification |
| `policy.py` | 245 | Escalation ladders and stopping rules |
| `mandate.py` | 191 | The gate |
| `execute.py` | 143 | Razorpay clients, idempotency |
| `store.py` | 169 | SQLite schema and case persistence |
| `ledger.py` | 144 | Append-only audit API |
| `config.py` | 61 | Settings and the test-mode interlock |
| `cli.py` | 304 | Six commands |
| `adapters/` | 391 | Three leaks + registration plumbing |
| `harness/` | 1050 | Assignment, response model, runner, metrics, replication, scenarios |
| `tests/` | 700 | 130 tests |
