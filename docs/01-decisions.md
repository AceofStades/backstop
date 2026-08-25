# Decisions and rejected alternatives

Every significant choice, with the reasoning that produced it and the option it
displaced. Written so a reader can disagree with a decision on its merits rather
than guess at whether it was considered.

---

## 1. Track 03 over Track 01

**Decision.** Build revenue recovery, not agentic commerce.

**Context that shaped it.** The buildathon is a hiring funnel, not a prize
hackathon: no resume screen, no aptitude test, and the last two stages are a
5-minute pitch and an architecture panel. The optimization target is therefore
*surviving technical questioning*, not demo flash.

Razorpay shipped **Agent Studio** in March 2026 at FTX'26, built on Anthropic's
Claude Agent SDK. Its prebuilt agents map near-verbatim onto the track example
directions:

| Agent Studio product | Track example direction |
|---|---|
| Dispute Responder | 02 — Chargeback evidence responder |
| RTO Shield / RTO Insights | 02 — Return-risk scorer |
| Subscription Recovery | 03 — Failed-subscription recovery |
| Abandoned Cart Conversion | 03 — Checkout drop-off recovery |
| Settlement Insights | 04 — Settlement Q&A agent |
| Cashflow Forecaster | 04 — Forward cash forecaster |

The example directions are a list of things the panel has already built and knows
the hard parts of. Building one shallowly is the worst available outcome.

**The alternative considered seriously.** Track 01, specifically the *sell-side*
half — "make a merchant transactable by an AI buyer end to end." The case for it is
genuinely strong: the agentic-commerce protocol stack has settled into layers (MCP
and A2A for discovery, ACP and UCP for checkout, AP2 for authorization, x402 for
settlement), and **none of those layers were designed for UPI**. NPCI's Unified
Agent Protocol is still unlaunched and pending RBI approval. That gap is real and
unfilled.

**Why we did not take it.** The deciding argument is evidentiary, not thematic:

> Track 01's mandate layer is **self-refereed**. Track 03's numbers are
> **externally refereed**.

In the Track 01 build, you design the mandate schema, define the caps, write the
verifier, and then demo your verifier rejecting a mandate you crafted to be
invalid. It is a closed loop. The work can be entirely correct and still have
nothing outside your own code validate it. The panel question that hurts is *"you
invented this schema — why would NPCI adopt your format, and what happens when UAP
ships specifying something different?"* The honest answer is "it's a proposal,"
which is fine for a research artifact and weak as evidence that you ship things
that work.

In Track 03 you also construct the batch, but the *outcome* is decided by
Razorpay's API. Whether an order was created, whether a payment link exists, what
error code came back — none of that is yours to declare.

A second risk stacked on top: Razorpay shipped agentic UPI with NPCI on Claude in
February 2026. There is a live chance someone on the panel works on exactly this
and knows precisely where an outside mandate proposal is naive about UPI Circle
delegation semantics or additional-factor-authentication requirements. High
variance — excellent if you nail it, brutal if you do not.

**What we kept from it.** The gating architecture, repointed. Track 03's bar
already demands "compliant escalation, stopping rules, and an audit trail" — that
*is* a mandate layer by another name. So `mandate.py` is the Track 01 design idea
applied to recovery actions instead of purchases: signed bounded authority,
per-action ceilings, velocity caps, expiry, scope, and clean structured refusals.
Design sophistication *and* externally-measured outcomes.

**The other alternative.** Track 04 (Finance Controller) was the contrarian pick —
least crowded, and the one track where deterministic ground truth is legitimately
constructible rather than fabricated. Rejected because its panel question is *"why
is this AI at all?"* Reconciliation is ~80% solvable with fuzzy string matching and
no model, so the AI has to earn its keep entirely in the exception-resolution
layer. Higher floor, lower ceiling. Track 03 has both a real AI role and real
outcomes.

**A note on crowding.** An early draft of this reasoning leaned on "Track 01 will be
the most crowded." That argument was dropped: nobody has data on track
distribution, and the Track 01 proposal under consideration sat in the *thin* half
of that track anyway. Crowding is not load-bearing here in either direction.

---

## 2. Multiple leaks, one engine

**Decision.** Three leak adapters (payment failure, subscription dunning, checkout
abandonment) behind one pipeline.

**Reasoning.** All four Track 03 leaks share the same skeleton: detect money at
risk → diagnose why → choose an intervention under policy → execute inside limits →
measure against control. What differs is the *cause taxonomy* and the *intervention
catalog*, and those are data, not code.

Building one engine with pluggable adapters means the second leak costs an adapter
file rather than a second pipeline. That is a claim, so the codebase has to
demonstrate it: `subscription_dunning.py` is 108 lines and reuses the diagnoser,
policy engine, gate, executor and ledger unchanged.

**Priority order.** Payment failure and subscription dunning are primaries.
Abandonment was a stretch that landed — it is worth having precisely because it is
*structurally* different: there is no decline to interpret, because no charge was
ever presented. Detection is the absence of a capture after a dwell threshold. It
proves the abstraction is not quietly assuming an error code exists.

**Rejected: B2B receivables.** Shares almost nothing with the others — no error
codes, different channels, different timescales — and a believable synthetic batch
of B2B invoices is hard to construct honestly. Adding it would have been four
shallow leaks instead of three real ones.

---

## 3. The failure reason picks the intervention

**Decision.** `policy.PLAYBOOK` maps each root cause to an escalation ladder.

**Reasoning.** Razorpay publishes a failure taxonomy. It is the most useful signal
in the entire pipeline, and a blind retry loop throws all of it away. The
difference between `insufficient_funds` and `invalid_vpa` is the difference between
"try again on payday" and "this can never work, ask for a different instrument."

The taxonomy carries a `retryable_same_instrument` flag, and `policy.decide()`
refuses to select a retry where that flag is false — even if a ladder mistakenly
specified one. `test_never_retries_an_instrument_that_cannot_work` asserts this
across every root cause, so the guarantee survives future ladder edits.

**Rejected: a single "smart retry" with exponential backoff.** It is what the naive
baseline does, and it is measurably wrong for two of the most common causes.
Retrying a dead VPA has exactly zero probability of success at nonzero cost.

---

## 4. The LLM proposes, deterministic code disposes

**Decision.** The model produces only a `Diagnosis`, constrained to the `RootCause`
enum. It never selects an action.

**Reasoning.** This is the sentence that should survive the pitch. Action selection
is a deterministic table; the model's only job is classification, and only where
deterministic classification failed.

Two supporting constraints:

- **LLM confidence is capped below the deterministic path**
  (`LLM_MAX_CONFIDENCE = 0.85` < `DETERMINISTIC_CONFIDENCE = 0.97`). A model's
  stated certainty is not the same class of evidence as a documented error code
  from the payment processor, and the numbers should reflect that.
- **Below `CONFIDENCE_THRESHOLD` (0.60) the engine refuses to act** and routes to
  the exception list. `RootCause.UNKNOWN` is deliberately absent from the playbook,
  so an unclassifiable case stops rather than falling through to a default.

**Rejected: letting the model choose the intervention.** It would demo identically
and be indefensible under questioning. "What stops it from choosing a retry on a
dead instrument?" has no good answer if the model is choosing.

---

## 5. A held-out control arm

**Decision.** 40% of every batch is assigned to control at intake and never touched.

**Reasoning.** Real customers self-recover. They top up and retry, or the issuer
recovers on its own. An agent that reports gross recovery is claiming credit for
those. On the current 400-case batch, Rs 53,630 of Rs 138,951 gross would have
arrived anyway — 39% of the headline number is not the agent's work.

This is the single most likely thing to separate the project in an architecture
review, because almost nobody at a hackathon builds a control arm, and a payments
panel discounts gross recovery numbers reflexively.

Three properties make it defensible rather than decorative — see
[`03-measurement.md`](03-measurement.md) for the detail:

1. Assignment at intake, before any policy sees the case
2. Common random numbers, so the same case makes the same self-recovery draw in
   either arm
3. A non-zero control baseline, because a control group that recovers 0% by
   construction makes any uplift look spectacular and prove nothing

---

## 6. Compliance in code, not in prompts

**Decision.** RBI contact windows and TRAI DLT template requirements are enforced
in `mandate.check()`.

**Reasoning.** These are hard regulatory constraints on Indian recovery
communication:

- **RBI** bars customer contact outside 08:00–19:00 local time, across voice, SMS
  and instant messaging alike.
- **TRAI TCCCPR** requires every A2P message to carry a DLT-registered template id.

A prompt instruction is a request. A gate is a constraint. The model cannot be
argued past a closed contact window because it is never asked — the check runs in
deterministic code between the decision and the executor.

This is also local domain knowledge that an Indian payments panel will recognise
immediately, and that a generic "recovery agent" submission will not have.

---

## 7. Refusals as values, not exceptions

**Decision.** `mandate.check()` returns a `GateResult` carrying a `RefusalCode`. It
never raises.

**Reasoning.** A refusal is a legitimate outcome of a well-functioning system, not
an error condition. Modelling it as an exception means every caller has to remember
to catch it, and a missed catch becomes a crash in a money path.

Returning a structured value means the refusal carries its reason code *and* the
list of checks that ran before it, which is exactly what the audit trail needs.
`test_gate_never_raises_on_any_intervention_type` asserts this across the full
intervention × channel cross product.

---

## 8. Append-only enforced by the database

**Decision.** SQLite triggers on the ledger table make `UPDATE` and `DELETE` raise.

**Reasoning.** "Append-only by convention" is a code review away from not being
append-only. A trigger is a property of the data, survives any application-level
mistake, and can be demonstrated live in about ten seconds — which matters for an
architecture interview.

Corrections are new rows. Idempotency keys (`<case_id>:<attempt_no>`) sit under a
unique index, so a re-run cannot double-charge or double-contact even if the
application logic is wrong.

---

## 9. Batch size 400, control fraction 0.4

**Decision.** Defaults chosen for statistical power, not for looking impressive.

**Reasoning.** Measured directly during the build:

| n | control | 95% CI | Verdict |
|---:|---:|---|---|
| 100 | 30% | −3.2 to +39.6 pp | spans zero — not evidence |
| 200 | 40% | +15.6 to +40.8 pp | significant |
| 400 | 40% | +20.9 to +38.2 pp | significant, tighter |

At 100 cases with a 30% control arm the interval spans zero. Reporting a point
estimate from an underpowered experiment is precisely the error a panel is looking
for. The batch is powered from roughly 200 cases up; 400 is the default for margin.
