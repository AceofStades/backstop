# Next steps

Ordered by what most threatens the project's central claim, not by what is most
interesting to build.

---

## Blocking

### 1. Verify against real `rzp_test_` credentials

**Status: unverified.** Every run so far has used the simulated executor. The
day-one assumption — that test keys scope correctly and order/payment-link creation
behaves as documented — has never been exercised.

This is the single largest risk. The README claims "every Order and Payment Link is
a genuine Razorpay test-mode entity with a live id." That claim is currently
unbacked.

```bash
cp .env.example .env        # add rzp_test_ credentials
uv run razor-pay verify     # interlock, live order creation, ledger triggers
uv run razor-pay seed --cases 400   # without --simulated
uv run razor-pay run
```

`verify` checks the interlock refuses non-test keys, creates a real Rs 1 test order
to confirm reachability, and confirms the ledger triggers fire.

**What could break.** Rate limits on 400 sequential order creations (the seeder has
no backoff). Payment-link creation may require fields the code does not send.
Receipt-length limits are truncated at 40 chars but untested against the real API.

**Until this passes, do not claim the numbers are backed by real API artifacts.**

---

## High value

### 2. Optimise the cost ratio

6.60 actions per attributable recovery is the weakest number in the report, and a
panel will ask about it.

The engine currently walks a ladder to exhaustion regardless of expected value. It
should stop when expected recovery falls below action cost. That needs a cost model
per channel (an API retry is nearly free; a WhatsApp message is not) and an expected
uplift per remaining ladder step — the latter already exists in `UPLIFT`, though as
an assumption rather than a measurement.

Concretely: add an early-stop rule in `policy.decide()` comparing
`amount_at_risk × remaining_uplift` against `cumulative_action_cost`. Report the
ratio before and after.

### 3. Per-customer contact budgets

The attempt cap is per **case**. A customer with three failed payments at one
merchant can receive three separate contact sequences. That is the most obvious
compliance gap and the one a regulator would notice first.

Needs a customer-level ledger query in `mandate.check()` — the data is already there
via `customer_ref`, so this is a new check rather than new plumbing.

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

Do only #1. An unverified test-mode path undermines the central claim in a way no
amount of additional feature work compensates for.

Second priority is naming #2 as a known weakness in the pitch rather than fixing it
— the panel discounts a hidden weakness far more than an acknowledged one.
