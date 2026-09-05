# Panel preparation

The buildathon ends in a 5-minute pitch and an architecture interview. This is the
question list, with honest answers.

The governing rule: **volunteer the limitations.** A panel that finds a weakness you
hid discounts everything else you said. A panel that hears you name it first reads
you as someone who knows what their own numbers mean.

---

## The 5-minute pitch

Structure, roughly a minute each:

**1. The problem, concretely.** Money slips away in ways that look identical from
outside — a payment did not arrive — but need completely different responses.
`insufficient_funds` and `invalid_vpa` both show up as a failed payment. One wants a
retry at the right moment. The other can never succeed on a retry, at any moment.

**2. What the agent does.** Reads the failure reason, picks an intervention that
could plausibly work for that specific reason, refuses to act when not confident,
executes inside limits it cannot talk itself past, writes an append-only record of
what it did and what it checked first.

**3. The number.** "+24.7 percentage points incremental, pooled across 12
independent batches, each with a held-out control arm — between-batch spread is
about 3 points either side." Then immediately: "gross recovery would have been
Rs 167,251 on a typical batch, but Rs 64,381 of that arrives anyway, so I report
incremental."

**4. One failure, live.** `demo-refusals` — a contact that would fire at 23:00 gets
deferred to 08:00 with the reason logged, not dropped, not sent.

**5. What is real and what is not.** The three-layer split, in fifteen seconds.

The gross-vs-incremental line in step 3 is the highest-value sentence in the pitch.
It signals immediately that you know what a recovery number is worth.

---

## Questions to expect

### "How do I know these numbers aren't made up?"

The action layer is real and verified against live credentials: Orders are genuine
Razorpay test-mode entities with live ids, verifiable in the dashboard. The customer
response layer is modelled, and I say so in the README and in the report itself.

Worth separating two claims that are easy to blur. The **integration** is
established by a live test-mode run. The **statistic** — +24.7 pp — comes from
`replicate`, which is simulated and pooled over 12 batches. A live run at the scale
Razorpay's test-mode quotas allow gives a 95% CI of −12.8 to +38.2, which is not
evidence of anything. I would rather say that than imply one run gave me both.

What that buys: the policy engine, the gate, idempotency, and the ledger are real
code paths under test. What it does not buy: any claim about real-world recovery
rates. The control arm exists precisely because a modelled response layer would
otherwise make any intervention look effective.

### "Why should I believe the control arm means anything?"

Three specifics. Assignment happens at intake, before any policy sees the case, by
deterministic hash — so the engine's behaviour cannot influence who is held out.
The response model uses common random numbers keyed on `case_id`, so the same case
makes the same self-recovery draw in either arm. And the control baseline is
non-zero for every root cause — a control group that recovers 0% by construction
makes any uplift look spectacular and prove nothing.

### "I re-ran your batch and got a different number."

Expected, and the README says so before you run it. Single-batch lift ranges +18.8
to +29.8 pp across seeds — ordinary sampling variation at n=400. That is why the
headline is pooled across 12 batches rather than taken from whichever batch ran
last, and why the between-batch spread is printed next to the mean.

I got this wrong initially: an earlier README quoted +30.4 pp from one batch, and a
clean re-run gave +17.0. Both were honest draws. The fix was `razor-pay replicate`,
not a better single batch.

### "Your baseline is invented. Why is the lift real?"

It is invented, which is why the headline ships with a sensitivity sweep rather than
alone. Across 0.5× to 1.5× the assumed baseline, the lift ranges +33.0 to +25.6 pp on a
representative batch.
It survives every baseline tested. That is a weaker claim than a measured baseline
and a stronger one than a point estimate.

### "What broke when you first pointed it at the real API?"

Four things, and they are the most useful part of the build.

Rate limiting: a 400-case seed drew 124 "Too many requests" responses. Backoff
absorbed them; I then added pacing so it avoids the limit rather than recovering
from it 124 times.

An intervention type — `soft_nudge` — that the executor could not actually perform.
It had been broken since the day the ladder was written, and the simulator hid it
by classifying any unknown type as an order and returning a plausible fake id. A
permissive stub is worse than a strict one.

A permanent error classified as transient: Razorpay returns its payment-link quota
as a 5xx, so every quota failure burned all its retries.

And the one that mattered: **Razorpay test mode allows 30 Payment Links per
business for the lifetime of the account.** My first run spent them, every
subsequent link case was retired as a failure, and the reported lift dropped to
+15.0 pp — below the entire simulated range. The number looked like a policy result
and was actually an account quota. The executor now degrades and flags those
artifacts explicitly rather than letting the quota contaminate the measurement.

### "Your uplift numbers are made up. How do you know any of this holds?"

They are. `economics.BELIEVED_UPLIFT` is an estimate, and it cannot be measured
without production data — nobody knows P(recovery | cause, intervention) for a
merchant that has never run these interventions.

So I do not defend the numbers. I report the sensitivity to them.
`razor-pay uplift-sensitivity` sweeps them across a half-to-double band and asks
which decisions actually change. 8 of 18 ladder steps are sensitive. All 8 cost
money to fire. All 10 free steps are stable.

That is the mechanism, not a coincidence: belief only decides something when
there is a cost to weigh it against. A rail-side retry is close enough to free
that no plausible uplift refuses it. A WhatsApp message has to earn its place.

And be clear about what that does not prove — sensitivity is not accuracy. Those
decisions are stable because ticket sizes dwarf action costs, not because the
estimates are right. What it rules out is the sharper version of your question:
that the headline is an artifact of numbers picked to produce it.

### "Why don't you rank the ladder steps by expected value?"

Because I measured whether that would be trustworthy, and it is not. Perturbing
each belief independently, the top-ranked step holds only 72–86% of the time for
six of the seven multi-step ladders. Only `collect_expired` is stable, at 98.6%.

A computed ranking built on numbers that shaky is harder to defend than an
explicit policy choice, not easier — it launders a guess into something that
looks derived. So ladder order stays a stated policy decision, and the analysis
is in the repo showing why.

This is the one time the sensitivity work talked me out of building something
rather than into it.

### "95% accuracy on diagnosis — what about the other 5%?"

The interesting answer is that it is not error in the way the number implies.

Two splits matter. First, by method: the deterministic path scores 100%, because
it is a lookup table on a documented Razorpay error code and cannot be wrong. The
whole error budget belongs to the ambiguous fallback slice. Averaging them
produced a figure that described neither.

Second, and more important — the confusion matrix shows every misclassification
landing on `UNKNOWN`. Not one case was diagnosed as the wrong *cause*.

Those are different failures wearing the same percentage. Wrong-as-`UNKNOWN`
costs a recovery: the case routes to the exception list and a human sees it.
Wrong-as-another-cause would fire a confident, specific, wrong intervention — the
exact failure the cause-keyed design exists to prevent. That count is zero.

So the residual is abstention, not error, and the headline understates the safety
property. The report computes that distinction; if a confident misdiagnosis ever
appears, the prose flips and names the count.

### "Isn't this just a retry loop with extra steps?"

No, and the clearest case is `invalid_vpa`. The taxonomy marks it
`retryable_same_instrument=False`, so the policy engine never selects a retry for it
— it sends an instrument-update link instead. `test_never_retries_an_instrument_that_cannot_work`
asserts this across every root cause, so it holds even if someone edits a ladder
badly later.

The second case is `insufficient_funds`, where the intervention is *timing*: defer
to the next payday window rather than retry now. A retry loop cannot express that.

### "Where does the LLM actually do anything?"

Deliberately narrow. It classifies free-text failure evidence into a fixed enum when
no machine-readable error code exists — about 8% of cases. It never selects an
action, and its confidence is capped at 0.85 against the deterministic path's 0.97,
because a model's stated certainty is not the same class of evidence as a documented
error code from the processor.

If the model is unavailable the engine degrades to routing those cases to the
exception list. It does not guess.

### "So the AI is barely doing anything. Why is it AI at all?"

Fair challenge, and the honest answer is that the AI is doing the part that is
genuinely hard to specify — mapping unstructured operator text to a taxonomy — and
nothing else. I would rather have a small defensible model role than a large
indefensible one.

The alternative framing: the interesting engineering here is the *harness around*
the model — the confidence threshold, the enum constraint, the gate, the audit
trail. That harness is what makes an LLM safe to point at money, and it is
reusable regardless of how capable the model gets.

### "What happens when the model hallucinates a root cause?"

Three things stop it. The output is constrained to the `RootCause` enum and
validated on return — anything outside is discarded, not coerced. Its confidence is
capped below the deterministic path. And below 0.60 the policy engine refuses to act
at all and routes to the exception list.

A hallucinated-but-in-enum cause with high stated confidence would get acted on. The
mitigation is that the resulting action is still bounded by the gate and capped at 3
attempts, so the blast radius is one customer receiving up to three well-formed but
wrongly-chosen contacts.

### "Why can't the model be prompted past the contact window?"

Because it is never asked. The check runs in `mandate.check()`, in deterministic
code, between the policy decision and the executor. There is no code path from a
model output to a Razorpay call that does not pass through it.

### "Show me the audit trail."

`razor-pay audit pf_0000`. Every entry answers five questions: which actor, under
which mandate, under which policy version, what was checked before it fired, and
what happened. The `checks_performed` list is on every gate entry, so it records
what was verified rather than only the verdict.

And the ledger is append-only enforced by SQLite triggers — `UPDATE` and `DELETE`
raise. I can demonstrate that in ten seconds.

### "What is your false positive cost?"

On a representative batch, Rs 2,572 of action cost against Rs 102,869 of
incremental recovery, so net value is Rs 100,297; 101 of the 396 actions were
customer contacts. Mean net value across 12 replications is Rs 88,839.

There is a better answer buried in that, and I would volunteer it: I originally
reported actions-per-attributable-recovery, 6.60, and set out to optimise it. A
stricter expected-value threshold improved it to 6.31 — and destroyed Rs 2,522 of
net value, because the recoveries I gave up were worth far more than the actions I
saved. A ratio improves when you cut its denominator; that is not the same as
making money.

So the threshold now sits at break-even, where it encodes a principle rather than a
tuning parameter, and the report leads with net value. The ratio is still printed,
labelled as reported rather than optimised against.

### "Isn't your engine cheating by knowing which interventions work?"

No, and the code is arranged to make that checkable. The harness's `UPLIFT` table
decides whether an intervention actually lands. The engine has its own separate
`BELIEVED_UPLIFT` table, and the two are allowed to disagree.

If the engine read the harness's table it would have perfect knowledge of customer
behaviour, and the expected-value rule would be measuring the simulation rather
than the policy. `test_engine_belief_is_not_harness_truth` asserts they stay
distinct.

### "Which of your cases failed?"

25 of 238 treatment cases are on the exception list, each with the amount, what the
diagnoser concluded, its confidence, how many actions fired, and why it stopped.
They land there when the diagnoser returns `UNKNOWN`, confidence falls below
threshold, the cause has no ladder, or the gate refused.

An agent that resolves 100% of cases is either misclassifying or lying.

### "Why didn't you build the agentic commerce track? That's the interesting one."

It is genuinely interesting, and the UPI gap is real — none of ACP, AP2 or x402 was
designed for UPI rails, and UAP has not launched. I decided against it on
evidentiary grounds: a mandate layer I design, whose verifier I write, that I demo
rejecting a mandate I crafted, is self-refereed. Nothing outside my own code
validates it.

Here the outcomes are decided by Razorpay's API. I also kept the mandate architecture
— `mandate.py` is that design applied to recovery actions rather than purchases.

### "What would you do with another two weeks?"

Ask Razorpay to raise the 30-payment-link test-mode cap, so the live run can cover
link-type interventions at scale instead of only orders. Then webhook ingestion, so
detection is event-driven rather than batch. Then ranking remaining ladder steps by
expected value rather than only refusing the ones that cannot cover their cost.

### "What is the weakest part of this?"

The uplift parameters. They encode a belief that a payday-timed retry beats an
immediate one for `insufficient_funds`. That belief is plausible and drawn from how
salary credit works in India, but it is not measured from production data, and the
whole ranking of interventions rests on it. I stated it explicitly in the response
model so it can be argued with rather than buried.

---

## Traps

**Do not say "recovered Rs 167,251."** Say incremental, every time. The gross figure
is in the report, labelled as overstating the agent.

**Do not imply the customer response is real.** The phrase is "the action layer is
real, the response layer is modelled."

**Do not let the two claims blur.** The live run proves the integration; the pooled
simulated runs provide the statistic. Say both, separately, in that order.

**Do not describe a degraded artifact as real.** Once the 30-link test-mode quota is
spent, link-type actions record a flagged placeholder. The report discloses the
count; do not round it away.

**Do not present actions-per-recovery as an optimisation target.** If asked, tell
the story of trying it and measuring that it destroyed value. That story is worth
more than a good-looking ratio.

**Do not oversell the DLT template ids.** They are placeholders; what is demonstrated
is the enforcement path, not real registration.

**Do not claim the uplift sensitivity validates the uplift numbers.** It does the
opposite kind of work: it says which decisions survive the numbers being wrong.
Sensitivity is not accuracy, and saying so first is stronger than being corrected.

**Do not let the page do the talking.** It renders the same numbers as the markdown
and computes none of them. If someone asks where a figure comes from, the answer is
`compute()` and the saved JSON, not the page. Say so — a presentation layer that could
disagree with the report would be a defect, and it is worth being the one to point out
that it cannot.

**Do not report diagnosis accuracy as a single number.** Split it — deterministic
100%, fallback much lower — and say that every error lands on `UNKNOWN` rather than
on another cause. The shape of the error is the point; the percentage is not.

---

## Live demo sequence

Six commands, about four minutes, with a story:

```bash
uv run razor-pay verify           # interlock refuses non-test keys; ledger triggers work
uv run razor-pay demo-refusals    # four refusals, each with a reason code
uv run razor-pay seed --cases 400 # real test-mode Orders created
uv run razor-pay run              # the loop, then this batch's numbers
uv run razor-pay replicate --runs 12   # the pooled headline
uv run razor-pay audit pf_0000    # the full trail for one case
uv run razor-pay uplift-sensitivity  # what changes if the beliefs are wrong
uv run pytest -q                  # 148 tests
```

Have `razor-pay report --html` open in a browser on the second screen. It renders the
same saved metrics as the markdown — nothing is recomputed — but the effect estimate is
drawn as a forest plot against a zero line, which is the one graphic worth putting in
front of a panel. It answers the question they are actually asking (does the interval
clear zero?) rather than the one a bar chart answers (which bar is taller?).

The sensitivity table is the slide worth lingering on.
