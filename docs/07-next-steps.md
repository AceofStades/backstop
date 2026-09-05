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

### 6. ~~Measure the uplift parameters instead of asserting them~~ — DONE

Built as `razor-pay uplift-sensitivity`. The parameter still cannot be measured
without production data, so the sensitivity to it is stated instead — the same move
already made for the control baseline.

8 of 18 ladder steps turn out to be sensitive to the belief, and all 8 are steps
that cost money to fire; all 10 free steps are stable. Belief is load-bearing only
where there is a cost to weigh it against.

**It also talked us out of item 2's remaining half.** Ranking stability under
independent perturbation runs 72–86% for six of seven ladders. A computed ranking
built on numbers that unstable would be worse than a stated policy choice, so
EV-ranking is now *deliberately not doing* rather than pending. See
[`05-worklog.md`](05-worklog.md).

### 7. ~~Diagnoser confusion matrix~~ — DONE

The prediction was right and understated. The deterministic path scores 100% — it
is a lookup table on a documented error code — and the entire error budget belongs
to the ambiguous slice.

The matrix showed something the accuracy figure hid: **every misclassification lands
on `UNKNOWN`, never on another cause.** No case is diagnosed confidently and wrongly.
The residual is abstention, routed to the exception list where a human sees it,
rather than a wrong intervention fired with confidence. Headline accuracy therefore
understates the safety property.

---

## Deliberately not doing

**B2B receivables adapter.** Shares almost nothing with the existing three — no
error codes, different channels, different timescales. Would be a second pipeline
wearing an adapter's clothes.

**A web dashboard.** The CLI plus generated markdown reports is the right surface
for an architecture review. A dashboard is demo polish that competes for time with
the verification work above.

*Amended:* `report --html` was built, and it is not a dashboard. The distinction is
load-bearing. A dashboard is a second system with its own state, queries and refresh
path; this is a formatter that takes the metrics dict `compute()` already produced and
lays it out so it can be read from across a room. It computes nothing, so it cannot
disagree with the markdown report sitting next to it. The problem it solves is a
presentation problem — five minutes, a projector, an audience that cannot read a
300-line markdown file while someone talks over it — not an architecture one.

**Real DLT registration.** Out-of-band telecom process. The enforcement path is what
matters and is demonstrated; swapping placeholder ids for real ones is config.

**Multi-merchant support.** The mandate is already merchant-scoped and the gate
checks it. Actually running several merchants adds surface without adding evidence.

**Expected-value ranking of ladder steps.** Moved here from "high value" once the
sensitivity analysis measured how unstable such a ranking would be (72–86% for six
of seven ladders). A computed order built on beliefs that shaky is harder to defend
than an explicit policy choice, not easier. Revisit only with production data behind
the uplift estimates.

**A second Razorpay account to reset the payment-link quota.** The cap counts
creations over the lifetime of a business and is not reclaimable by deleting links
or rotating keys — verified, see [`05-worklog.md`](05-worklog.md). A fresh account
would work and is the wrong move: this is a submission tied to a real identity, and
30 more links would not change the statistic, which comes from `replicate` anyway.

---

## If time is very short

The blocking item is done, so what remains is polish rather than credibility
repair. In order:

1. **Rehearse the honest split out loud** — live run proves integration,
   `replicate` provides the statistic. It is the single most likely thing to be
   probed, and the answer is strong if it arrives unprompted.
2. **Ask Razorpay support to raise the payment-link cap.** Cheap, and it would let
   the live run cover link-type interventions too.
3. **Webhook ingestion** (#4) if there is genuine time left. It is the only
   remaining item that changes the architecture rather than the reporting.

Do not spend the remaining time on new leak adapters. Three is already enough to
demonstrate the abstraction, and a fourth adds surface without adding evidence.
