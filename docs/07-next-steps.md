# Next steps

Ordered by what most threatens the project's central claim, not by what is most
interesting to build.

---

## Blocking

### ~~1. Verify against real `rzp_test_` credentials~~ — DONE

Credentials verified. `verify` passes the test-mode interlock, reaches the API, and
the loop has run end to end against real Razorpay test mode. Orders are real
entities with live ids.

Four bugs surfaced on first contact, all invisible in simulation: unhandled rate
limiting (124 responses on one seed), an intervention type the executor could not
perform, a permanent error misclassified as transient, and the payment-link quota.
See [`05-worklog.md`](05-worklog.md), bugs 12–16.

**What remains from this item:** Razorpay test mode caps Payment Links at 30 per
business for the lifetime of the account, and the first 400-case run spent them.
Contacting Razorpay support to raise the cap would allow a larger live run. Not
blocking — the honest split (live run proves integration, `replicate` provides the
statistic) is defensible as it stands, and is arguably the better story anyway.

---

## High value

### 2. ~~Optimise the cost ratio~~ — DONE, with a surprise

Built `economics.py`, then measured that optimising the ratio *destroyed* net value:
6.60 → 6.31 on the ratio, −Rs 2,522 on the money. Threshold now sits at break-even
and the report leads with net value instead. Full sweep in
[`05-worklog.md`](05-worklog.md).

**What remains:** the engine only *refuses* actions that cannot cover their own
cost. It does not *rank* remaining ladder steps by expected value, or reorder a
ladder so the highest-EV step fires first. That is the real version of this work
and is still open.

### 3. ~~Per-customer contact budgets~~ — DONE

`max_contacts_per_customer` (4 per 7 days), counted from the ledger across every
case belonging to that customer. Refuses 8 contacts in the current batch.

Building it also exposed that the seeder drew customer refs from a 9,000-wide pool,
making repeat customers vanish — an unrealistic population that flattered the
engine. Replaced with a 120-customer pool and a heavy tail.

**What remains:** the budget is per customer *per merchant*. A customer transacting
with several merchants on one platform could still be contacted by each.

### 4. Webhook ingestion

Detection is currently batch: seed, then run. Real recovery is event-driven —
`payment.failed`, `subscription.charged` and `order.paid` webhooks arriving
continuously.

Adapters already emit uniform `RecoveryCase` objects, so this is a new detector
front-end rather than a pipeline change. It would also let the abandonment adapter
detect genuine absence-of-capture rather than synthesising it.

---

## Worth doing

### 5. Alternate-rail routing for issuer downtime

`ISSUER_DOWNTIME` currently backs off and retries the same rail. Razorpay's Downtime
API reports provider status; a smarter ladder would route to an alternate method
when the original issuer is known-down. The taxonomy already carries a `transient`
flag that nothing reads yet.

### 6. Measure the uplift parameters instead of asserting them

`UPLIFT` encodes beliefs about which interventions work. Everything downstream ranks
on those numbers. They cannot be measured without production data, but the sweep
approach used for the baseline could be extended: report how the *ranking* of
interventions changes across plausible uplift ranges, and identify which policy
choices are robust to the assumption and which are not.

That converts an unmeasurable parameter into a stated sensitivity, which is the same
move already made for the baseline.

### 7. Diagnoser confusion matrix

Accuracy is reported as a single 95.4%. A confusion matrix would show *which* causes
get confused, which is more actionable — and would likely reveal that essentially
all error comes from the LLM-fallback slice, since the deterministic path is a
lookup table.

---

## Deliberately not doing

**B2B receivables adapter.** Shares almost nothing with the existing three — no
error codes, different channels, different timescales. Would be a second pipeline
wearing an adapter's clothes.

**A web dashboard.** The CLI plus generated markdown reports is the right surface
for an architecture review. A dashboard is demo polish that competes for time with
the verification work above.

**Real DLT registration.** Out-of-band telecom process. The enforcement path is what
matters and is demonstrated; swapping placeholder ids for real ones is config.

**Multi-merchant support.** The mandate is already merchant-scoped and the gate
checks it. Actually running several merchants adds surface without adding evidence.

---

## If time is very short

The blocking item is done, so what remains is polish rather than credibility
repair. In order:

1. **Rehearse the honest split out loud** — live run proves integration,
   `replicate` provides the statistic. It is the single most likely thing to be
   probed, and the answer is strong if it arrives unprompted.
2. **Ask Razorpay support to raise the payment-link cap.** Cheap, and it would let
   the live run cover link-type interventions too.
3. **Webhook ingestion** (#4) if there is genuine time left.

Do not spend the remaining time on new leak adapters. Three is already enough to
demonstrate the abstraction, and a fourth adds surface without adding evidence.
