# Compliance

Indian recovery communication is regulated. This document records which rules the
engine enforces, where in the code they live, and why they are structural
constraints rather than prompt instructions.

---

## Why this lives in code

A prompt instruction is a request. A gate is a constraint.

"Do not contact customers outside 8am–7pm" in a system prompt is a preference the
model will usually honour and can be argued out of — by a cleverly worded case, by
a long context, by a fine-tuning drift, by a jailbreak in a customer-supplied field.
The same rule in `mandate.check()` is not negotiable, because the model is never
asked. The check runs in deterministic code between the decision and the executor.

This is the concrete form of the project's organising principle: **the LLM proposes,
deterministic code disposes.** Compliance is where that principle earns its keep,
because it is where being talked past has legal consequences rather than merely
wrong ones.

It is also domain knowledge an Indian payments panel will recognise on sight. A
generic recovery agent has no contact window.

---

## RBI — contact hours

**The rule.** RBI's recovery conduct rules bar contacting a borrower or guarantor
outside **08:00–19:00** local time. It covers all modalities: voice calls (human and
IVR), digital messaging including SMS and WhatsApp, and physical visits. Under the
July 2026 recovery guidelines the burden of conduct sits with the regulated entity,
not the agent acting on its behalf — which is precisely why an automated agent
needs this enforced rather than assumed.

**Where it lives.** `mandate.py`:

```python
IST = ZoneInfo("Asia/Kolkata")
CONTACT_WINDOW_START = time(8, 0)
CONTACT_WINDOW_END = time(19, 0)
```

**How it is checked.** Against the time the action would **fire**, not the time it
was decided:

```python
fires_at = now + timedelta(seconds=step.delay_seconds)
if not in_contact_window(fires_at):
    return refuse(RefusalCode.OUTSIDE_CONTACT_WINDOW, ...)
```

This distinction is the whole point. A decision made at 16:00 with a 6-hour backoff
fires at 22:00. Checking the decision time would pass; checking the fire time
correctly refuses.

**Deferral, not refusal.** `OUTSIDE_CONTACT_WINDOW` is the one refusal the runner
treats as recoverable. `next_allowed_contact_time()` computes the next open window,
the runner advances the case clock, and the action is re-gated. A message that would
have violated the window gets sent at 08:00 rather than dropped.

The deferral is logged with both the original and rescheduled times, so the audit
trail shows the constraint being honoured rather than merely a gap.

**Boundary handling.** The window is half-open: 08:00 is inside, 19:00 is outside.
`test_contact_window_boundaries` pins 07:00, 08:00, 12:00, 18:00, 19:00 and 23:00.
`test_deferral_always_lands_inside_the_window` asserts that deferring from any of
the 24 hours produces a time inside the window — including the wrap-around case
where deferring from 23:00 must land at 08:00 the *next day*.

**Timezone.** Everything converts to IST before comparison. The seeder produces
`detected_at` values in UTC across a 72-hour spread, so cases naturally arrive at
all hours and the gate is exercised by ordinary traffic. On the current batch, 40
contacts were deferred without any scripted scenario being involved.

---

## TRAI — DLT templates

**The rule.** TRAI's TCCCPR framework requires every A2P (application-to-person)
message to go through DLT (Distributed Ledger Technology) registration. A sender
registers as a Principal Entity, registers headers (sender IDs), and registers every
message template before sending. Templates separate transactional from promotional
traffic, which have different consent rules.

**Where it lives.** Template ids are declared in `policy.py` and attached to every
contacting intervention:

```python
TEMPLATE_INSTRUMENT_UPDATE   = "DLT_RZP_RCVR_INSTR_001"
TEMPLATE_PAYMENT_LINK        = "DLT_RZP_RCVR_LINK_002"
TEMPLATE_SOFT_NUDGE          = "DLT_RZP_RCVR_NUDGE_003"
TEMPLATE_COLLECT_REPRESENT   = "DLT_RZP_RCVR_COLLECT_004"
```

The gate refuses any contacting action arriving without one:

```python
if not step.template_id:
    return refuse(RefusalCode.CHANNEL_NOT_CONSENTED,
        "TRAI TCCCPR requires a DLT-registered template id for A2P messaging; "
        "none was supplied.")
```

**Two-layer enforcement.** `test_every_contacting_step_carries_a_dlt_template` walks
the entire playbook and asserts no ladder step contacts a customer without a
template. So a ladder missing one fails at build time *and* would be refused at
runtime.

**Honest scope note.** These ids are placeholders. Real DLT registration is an
out-of-band process with a telecom operator producing real ids. What is demonstrated
is the *enforcement path* — that an unregistered template cannot reach a customer.
Swapping placeholders for real ids is a config change, not a code change.

---

## Channel consent

Mandates carry an explicit set of permitted channels:

```python
allowed_channels: set[Channel] = {Channel.NONE, Channel.WHATSAPP, Channel.SMS}
```

A contacting action on an unconsented channel is refused. `Channel.NONE` denotes a
rail-side action that never touches the customer — a retry, a re-presentment — and
is exempt from all three contact checks, because there is no contact.

That distinction matters for the numbers: a retry at 03:00 is fine, a WhatsApp
message at 03:00 is not, and the gate treats them differently rather than applying
the strictest rule to everything.

---

## Per-customer contact budgets

The attempt cap is per **case**. That is not enough on its own: a customer with
three failed payments at one merchant would receive three independent contact
sequences, each individually compliant and collectively harassment.

So the mandate carries a second, orthogonal cap:

```python
max_contacts_per_customer: int = 4
contact_budget_window_hours: int = 168   # 7 days
```

Counted from the **ledger**, across every case belonging to that customer, joined
on `customer_ref`. Not an in-memory tally — it survives a restart and it spans
cases, which is the entire point.

Silent rail-side actions do not consume the budget. A retry touches no customer, so
`Channel.NONE` actions are exempt; `test_contact_budget_does_not_block_silent_rail_actions`
pins that.

**This check was initially dead code**, and the reason is instructive: the seeder
drew customer refs from a 9,000-wide pool for 400 cases, so repeat customers were
essentially impossible and no customer ever owned enough cases to hit the cap. The
seeded population was unrealistic in a way that flattered the engine. With a
realistic 120-customer pool and a heavy tail — a customer who failed once is
disproportionately likely to fail again — the batch now has customers owning 15
failing cases, and the budget refuses 8 contacts. See
[`05-worklog.md`](05-worklog.md).

---

## Bounded authority

Beyond the two regulators, the mandate bounds what the agent may do at all:

| Bound | Default | What it prevents |
|---|---:|---|
| `per_action_cap_paise` | Rs 25,000 | One oversized action |
| `velocity_cap_paise` | Rs 50,00,000 / 24h | Sustained runaway spending |
| `max_attempts_per_case` | 3 | Over-contacting on one case |
| `max_contacts_per_customer` | 4 / 7 days | Harassing one customer across cases |
| `expires_at` | +30 days | Authority outliving its grant |
| `merchant_id` | scoped | Acting for the wrong merchant |

Velocity is computed from the ledger — actual executed volume in the window — not
from an in-memory counter. A restart cannot reset it.

**Two independent limits on attempts.** The policy ladder retires a case after 2–3
steps depending on cause; the mandate caps at 3 regardless. In normal operation the
ladder stops first, so the gate acts as a backstop rather than the primary control.
That is why the batch report shows zero hard refusals and 40 deferrals — the
expected shape, and the report says so explicitly rather than leaving it looking
untested.

---

## Demonstrating it

`uv run razor-pay demo-refusals` exercises all four refusal paths directly:

```
1. Action exceeds the mandate's per-action ceiling
   -> REFUSED  per_action_ceiling_exceeded
   checked: stopped, expiry, scope, attempt_cap, per_action_ceiling

2. Case has reached its attempt cap
   -> REFUSED  attempt_cap_reached

3. Contact would fire outside the RBI 08:00-19:00 window
   -> REFUSED  outside_contact_window
   defer: rescheduled to 2026-08-25T08:00:00+05:30

4. Mandate has expired
   -> REFUSED  mandate_expired
```

Each refusal is a structured value carrying its reason code and the checks that ran
before it, written to the append-only ledger. None raises, none retries blindly,
none moves money.

---

## What is not covered

- **Consent capture.** The mandate asserts which channels are consented; it does not
  model how consent was obtained or withdrawn. Production needs a consent store with
  an audit trail of its own.
- **DND / NCPR registry.** TRAI maintains do-not-disturb preferences. Not modelled.
- **Frequency caps across merchants.** The contact budget is per customer *per
  merchant*. A customer transacting with several merchants on the same platform
  could still be contacted by each independently.
- **Language and content rules.** Template *content* has requirements beyond
  registration. Only the presence of a registered id is enforced.
- **State-level variation.** Treated as uniformly national.
