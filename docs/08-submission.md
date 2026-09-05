# Submission

Draft answers for the twelve application fields, plus the plan for the five-minute
video. Everything factual here is checked against the repo; everything personal is
marked `[FILL]` because it cannot be.

The form says the last field — *what broke, and how you got out* — is read first.
That answer is drafted at length below and is worth more attention than the rest
combined.

---

## About you

| Field | Answer |
|---|---|
| Full name | `[FILL]` |
| College | `[FILL]` |
| Graduation year | `[FILL]` |
| In-person from September | `[FILL: yes / no]` |
| 6 or 12 months | `[FILL]` — 12 signals commitment and is the better answer unless an academic requirement makes it untrue. Do not say 12 and then negotiate. |
| Resume file | `[FILL]` — attach the existing one. They take it and do not screen on it. Do not spend an hour rewriting it. |

---

## About the build

### Track

**Track 03 — AI Revenue Recovery.**

### Project name

**Backstop.**

The repo directory stays `razor-pay`; this is the product name for the form, the
video title and the pitch. It says what the system is — the thing behind the
payment that catches what falls through — without being a pun on the company's
name, which every second submission will be.

Alternatives if it does not sit right: *Second Attempt*, *Recovered*, *Held Out*
(a nod to the control arm, which is the actual differentiator).

### What it solves

Failed payments all look the same from outside — money did not arrive — and are
treated the same way by most recovery tooling: retry, then retry again, then email.
But the reasons are not the same, and two of them are opposites. A payment that
failed on `insufficient_funds` wants a retry at the right *moment*, near payday.
A payment that failed on `invalid_vpa` can never succeed on a retry, at any moment,
because the instrument is dead — retrying it only spends attempts and customer
patience on something that cannot work.

Backstop reads the failure reason, diagnoses a root cause, and picks an intervention
matched to that cause from an escalation ladder — or refuses to act and routes the
case to an exception list when it is not confident enough to choose. Every
money-moving or customer-contacting action passes a mandate gate first, which
enforces per-action caps, attempt limits, per-customer contact budgets, and the RBI
08:00–19:00 contact window in deterministic code the model cannot argue its way
past. Everything lands in an append-only ledger, so any case can be replayed:
what fired, under which mandate, on which policy version, and what was checked
before it fired.

The part that matters most is the measurement. **40% of every batch is held out as
an untouched control arm**, assigned at intake before any policy sees a case. The
reported number is *incremental* recovery — treatment minus control — not gross,
because a real customer whose payment fails will often top up and retry without
anybody doing anything, and an agent that counts those is measuring the world
rather than itself.

Measured: **+24.7 percentage points incremental (95% CI +22.8 to +26.6)**, pooled
across 12 independent 400-case batches.

### GitHub repo URL

`[FILL — must be public before submitting]`

Pre-flight before pasting the link:

```bash
git remote -v                    # confirm it points where you think
gh repo view --json visibility   # must be PUBLIC
git log --oneline -1             # the tip is what they will read
```

Confirm `.env` is absent from the repo and `.gitignore` still lists it. The keys in
it are test-mode only, but shipping credentials in a submission is its own answer to
a question they did not ask.

### Pitch video URL

`[FILL — unlisted YouTube]` — plan below.

---

## What broke, and how you got out

*They read this first. Written long here; trim to whatever the field allows, keeping
the first paragraph and the last intact.*

> The worst thing that broke was my own headline.
>
> I had +30.4 percentage points in the README. Re-running the pipeline from clean, a
> week later, I got +17.0. Nothing had changed in the code. That is a 13-point gap on
> the number the entire project exists to produce.
>
> My first instinct was that I had introduced a bug. I had not. I ran twelve seeds and
> got a mean of +26.1 with a standard deviation of 4.4, ranging from +17.4 to +31.7.
> Both numbers were honest draws from the same process. The confidence interval on
> each individual batch was fine — correctly computed, about the right width for the
> spread I was seeing. The error was upstream of the statistics: I had quoted a point
> estimate from one batch as though a single batch were the result. I had run it
> several times during development, seen a good one, and written that down.
>
> That is the failure mode I would not have caught by testing, because nothing was
> broken. Every component did exactly what it was written to do.
>
> The fix was `razor-pay replicate`, which runs independent batches and pools them,
> reporting the mean, the between-batch spread, and a confidence interval on the
> mean. The headline is now +24.7 pp (95% CI +22.8 to +26.6), and it ships with the
> range +18.8 to +29.8 stated next to it, so a reviewer who re-runs one batch and
> lands at +19 sees the number they got, in print, before they get suspicious.
>
> Two others worth naming, briefly.
>
> I optimised the wrong metric. Tightening the expected-value threshold improved
> actions-per-recovery from 6.60 to 6.31 — and destroyed Rs 2,522 of net value,
> because a ratio improves when you cut its denominator, which is not the same as
> making money. I swept the thresholds, moved it back to break-even, and made net
> value the headline cost metric instead. The comment in `economics.py` records the
> whole thing so nobody re-tunes it later.
>
> And the first time I ran against live credentials, a run reported +15.0 pp — below
> the entire simulated range. It was not the policy. Razorpay test mode caps payment
> links at 30 per business for the lifetime of the account, and I had spent them, so
> link-type interventions were failing and being scored as failed recoveries. An
> account quota was wearing the costume of a result. The executor now records a
> flagged placeholder that is never counted as real, and the report discloses the
> count — which is the general fix: make infrastructure failures look like
> infrastructure failures rather than letting them contaminate the measurement.
>
> The through-line is that all three were errors of *interpretation*, not of code.
> The tests passed the whole time. What caught them was re-running things I had
> already believed, and asking what a number would look like if it were wrong.

---

## The five-minute video

### Should there be a new frontend for it?

**No — and the reason is the interesting part.**

A UI built for this demo would prove nothing about the integration, because it would
be my code showing my own numbers. The panel has no reason to trust a dashboard I
wrote. The convincing artifact already exists and is not mine: **the Razorpay
dashboard**, showing the actual test-mode Orders this project created, with the same
ids that appear in the ledger.

So the video shows Razorpay's own dashboard next to `razor-pay audit`, matching an
order id in their UI to the audit trail in mine. That is externally refereed — the
whole reason Track 03 was chosen over a track whose outputs only I could validate.
A frontend would replace evidence with decoration.

Two things already fill the visual gap a frontend would have covered:

- `razor-pay report --html` renders the saved metrics as a page, with the effect
  estimate drawn as a forest plot against a zero line. That is the slide.
- `razor-pay audit pf_0000` prints the full append-only trail for one case, which
  reads well on screen and is the thing a payments engineer actually wants to see.

Building a web app in the days before submission would also cost the one thing worth
protecting: the ability to answer questions about every line in the repo.

### Structure

The full word-for-word script, with screen directions and measured
timings, is in [`09-pitch-script.md`](09-pitch-script.md). Summary:

| Time | Beat | On screen |
|---|---|---|
| 0:00–0:45 | The problem, concretely: `insufficient_funds` and `invalid_vpa` look identical from outside and want opposite responses — one wants better timing, the other can never work at all | Two failure payloads side by side |
| 0:45–1:30 | What the agent does: diagnose → matched intervention → gate → execute → ledger. "The LLM does judgment; deterministic code moves money." | `docs/02-architecture.md` pipeline diagram |
| 1:30–2:15 | **Real, not simulated.** `razor-pay seed` creating live test-mode Orders, then the same ids in the Razorpay dashboard | Terminal, then Razorpay dashboard |
| 2:15–3:00 | The number, with the honesty built in: +24.7 pp incremental, pooled over 12 batches. Say gross-vs-incremental out loud | `report --html` page, forest plot |
| 3:00–3:45 | A refusal, live: a contact due at 23:00 is deferred to 08:00 with the reason logged — not dropped, not sent | `razor-pay demo-refusals` |
| 3:45–4:30 | The audit trail for one case, matched to the dashboard order | `razor-pay audit pf_0000` |
| 4:30–5:00 | What is real and what is modelled, in fifteen seconds. Then: "one batch is one draw, so the headline is pooled" | Provenance ledger on the HTML page |

### The two sentences that carry the video

Say both, unprompted:

1. *"Gross recovery on a typical batch was Rs 167,251 — but Rs 64,381 of that would
   have arrived anyway, so I report incremental."*
2. *"The live run proves the integration. The pooled simulated runs provide the
   statistic. Those are two different claims and I keep them apart."*

The second one pre-empts the sharpest question a payments panel can ask, and
answering it before it is asked is worth more than any number in the report.

### Recording notes

- Terminal at a large font. Commands are legible on video; 300-line markdown files
  are not.
- Do not run `replicate` live — it is three seconds, but three seconds of a progress
  bar is dead air. Have the report open.
- Do not apologise for the response model being simulated. State it as a design
  decision with a control arm attached, which is what it is.

---

## Pre-submission checklist

```bash
uv run pytest -q                              # 148 passing
uv run razor-pay verify                       # interlock, API reachability, ledger triggers
uv run razor-pay replicate --runs 12          # headline reproduces
git status --short                            # clean
```

- [ ] Repo is public, `.env` absent, `.gitignore` still lists it
- [ ] README headline matches what `replicate` currently prints
- [ ] Video unlisted, link tested in a private window
- [ ] Resume attached
- [ ] The "what broke" answer pasted in full, not trimmed to one line
