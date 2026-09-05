# The five-minute pitch, word for word

Everything below is checked against the repo: the commands run, the line numbers
are real, and `order_TVb6JcrhitauVX` is a live entity in the Razorpay test account.

Narration is in `>` quotes. Screen directions are in **bold brackets**. Timings are
cumulative.

**Budget, measured not guessed.** The narration below is **801 words**. At a
confident 160 words per minute that is exactly 5:00 of speech — with zero room for
screen transitions, which means as written it *will* overrun.

So the three cuts listed under **If you run long** are the default, not the
fallback. Taking all three leaves ~683 words, about 4:16 of speech, and roughly 45
seconds of silence for switching windows and for the one deliberate pause this
video depends on. Record the trimmed version first; restore a cut only if you come
in under time.

Do not add words to fill the gaps. The gaps are the point.

---

## Before you record

```bash
uv run pytest -q                 # green, so the last frame can show it
uv run razor-pay verify          # confirms credentials still work
git status --short               # clean
```

Have these open, in this order:

| # | Window | What is on it |
|---|---|---|
| 1 | Terminal, 18pt+ | Nothing yet. This is where you type. |
| 2 | Editor | `src/razor_pay/policy.py`, scrolled to **line 48** |
| 3 | Browser tab A | Razorpay dashboard → **Test Mode** → Orders → `order_TVb6JcrhitauVX` |
| 4 | Browser tab B | `reports/<batch>.html` from `razor-pay report --html` |

Terminal font at 18pt or larger. Anything smaller is unreadable after YouTube
compresses it.

**The audit case is `pf_131151_0000`.** Already in `data/recovery.db`, and the best
single case in the batch: three escalating actions, the expected-value ratio visibly
collapsing, the gate widening its checks when the channel becomes WhatsApp, the
ladder exhausting into a stop, and an honest attribution at the end. Do not swap in
another case id without walking it first.

---

## 0:00 — 0:30 · The problem

**[Screen: terminal, empty. No slides, no title card.]**

> Two payments fail. Both look identical to the merchant: the money didn't arrive.
>
> The first failed on `insufficient_funds`. Thin balance. That one wants a retry —
> but not now. Near payday.
>
> The second failed on `invalid_vpa`. The UPI ID doesn't exist. That one can never
> succeed on a retry — every attempt spends customer patience on something
> structurally impossible.
>
> Most recovery tooling treats these the same. Retry, retry, email. That's the
> baseline I'm measuring against.

---

## 0:30 — 1:05 · What it does

**[Screen: editor, `policy.py` line 48. Scroll slowly from 48 to 80.]**

> The policy table. Every root cause gets an escalation ladder, indexed by how many
> attempts the case has already had.

**[Point at 48–56, then 67–80.]**

> `insufficient_funds` gets two payday retries, then a payment link.
> `instrument_invalid` gets no retry at all — straight to an instrument-update
> link. That comment on line 67 is the design in one sentence.

**[Jump to line 242.]**

> And this makes it structural rather than a good intention: a retry is only
> selectable where the taxonomy says the instrument could work. A test asserts it
> across every cause.
>
> The rule I'd put on a whiteboard — **the model does judgment, deterministic code
> moves money.** The LLM only produces a root cause from a fixed enum. It never
> picks an action and never touches the API.

---

## 1:05 — 1:50 · Real, not simulated

**[Screen: terminal.]**

```bash
uv run razor-pay audit pf_131151_0000
```

**[Let it print. Don't scroll yet.]**

> This case is real. Let me prove it with something that isn't mine.

**[Screen: browser tab A — Razorpay dashboard, Test Mode, Orders.]**

> Razorpay's own dashboard. Order `order_TVb6JcrhitauVX` — twelve hundred and
> twenty-two rupees.

**[Point at the receipt field. Then pause — two full seconds.]**

> Receipt: `seed_pf_131151_0000`.

**[Switch back to the terminal, point at the DETECT line.]**

> That's the case I just pulled the trail for. Same amount, two systems, and one
> of them is theirs.
>
> I picked this track because the outcomes are refereed by someone else. I could
> build a dashboard that says anything. I can't make theirs say it.

---

## 1:50 — 2:40 · The audit trail

**[Screen: terminal, scrolled to entry 3.]**

> Every case is fully replayable.

**[Point at entry 3, then 7, then 11.]**

> Entry 3 — step one of three, payday retry. Expected gain twenty-four thousand
> paise against fifty paise of cost. Four eighty-eight to one.
>
> Step two: two sixty-eight to one. Step three, a WhatsApp link: **one point five
> to one.** You can watch the economics decay in the log. That third action barely
> clears its own cost — and if it didn't, the engine would refuse to fire it.

**[Point at the `checked:` line on entry 4, then on entry 12.]**

> This is the part I'd look at if I were you. Entry 4 is a silent retry: the gate
> checked six things. Entry 12 sends a WhatsApp message: eight — it added the DLT
> template, the contact budget, and the RBI window. The checks widen because
> contacting a human engages rules a rail-side retry doesn't.

**[Point at entries 15–17.]**

> Ladder exhausted, stop, closed. And the outcome says `recovered=True via=self`.
> The case came back — and the agent takes no credit, because the harness says this
> customer would have recovered anyway.

---

## 2:40 — 3:25 · The number

**[Screen: browser tab B — HTML report, forest plot.]**

> Forty percent of every batch is a held-out control arm, assigned at intake — so
> the engine's behaviour can't decide who lands where.
>
> Plus twenty-four point seven percentage points incremental. Interval twenty-two
> point eight to twenty-six point six.

**[Point at the zero line.]**

> Drawn against zero deliberately. A bar chart answers "which bar is taller." The
> question you're actually asking is whether the interval clears no-effect — this
> way an underpowered result looks underpowered.
>
> Gross recovery on a typical batch was one lakh sixty-seven thousand rupees. But
> sixty-four thousand of that arrives anyway, because customers top up and retry on
> their own. So I report incremental. An agent that reports gross is reporting the
> weather.

---

## 3:25 — 4:00 · A refusal

**[Screen: terminal.]**

```bash
uv run razor-pay demo-refusals
```

**[Point at scenario 3.]**

> Four refusals, each a structured value with a reason code. Not an exception.
>
> The third is the one I care about. A WhatsApp message due at eleven at night.
> RBI's contact window is eight to seven. It isn't dropped and it isn't sent — it's
> deferred to eight the next morning, with the reason logged.

**[Switch to editor: `mandate.py` lines 36–37.]**

> That window is two constants in deterministic code. It's not in a prompt. There's
> no phrasing that gets the model past it, because the model is never asked.

---

## 4:00 — 4:30 · What's real, and what's one draw

**[Screen: HTML report, provenance ledger.]**

> Four tiers, kept apart on purpose. **Real** — orders, gate, ledger, idempotency.
> **Degraded** — actions recorded after the test account's thirty-link lifetime
> quota ran out; flagged, disclosed, never counted. **Modelled** — whether a
> customer pays after a nudge. **Injected** — the decline reason, which only the
> scoring harness may read.
>
> And two claims I won't let blur. The live run proves the *integration*. The
> pooled simulated runs provide the *statistic*. A live run at the scale that quota
> permits has an interval spanning zero — so it isn't evidence of an effect, and I
> don't present it as one.

---

## 4:30 — 5:00 · Close

**[Screen: terminal.]**

```bash
uv run pytest -q
```

> A hundred and forty-eight tests.
>
> The worst thing that broke was my own headline. I had plus thirty point four in
> the README. A clean re-run gave plus seventeen, no code changed. Nothing was
> broken — I'd quoted one lucky batch as though a single batch were a result.
>
> So it's pooled across twelve batches now, and ships with the spread printed next
> to it. Anyone who re-runs one and gets nineteen sees that number in print before
> they get suspicious.
>
> That's the project. Thanks.

**[Hold on the green test output for two seconds. End.]**

---

## If you run long

**Take all three of these by default** — see the budget note above. Each is
self-contained, so removing one does not break what follows.

1. **The `policy.py` line 242 guard** (0:30 beat) — saves ~12s. The ladder scroll
   already makes the point.
2. **The `mandate.py` constants** (3:25 beat) — saves ~10s. Saying it is enough.
3. **Entries 15–17 of the audit** (1:50 beat) — saves ~14s.

Do **not** cut the dashboard cross-reference at 1:05, the incremental-versus-gross
sentence at 2:40, or the two-claims sentence at 4:00. Those three are why this
pitch differs from every other Track 03 submission.

## If you come in under time

Restore the cuts above first, in reverse order. Only then add:

1. The efficiency-ratio story — tightening the threshold improved
   actions-per-recovery from 6.60 to 6.31 and destroyed Rs 2,522 of net value.
2. `uv run razor-pay uplift-sensitivity` — eight of eighteen ladder steps are
   sensitive to the engine's beliefs, and all eight cost money to fire.

## Delivery notes

- **Pause after the receipt reveal at 1:50.** Strongest moment in the video. It
  needs two seconds of silence to land. Resist filling it.
- **Don't apologise for the response model.** Say "modelled, with a control arm
  attached" once, in the provenance beat, and move on. Hedging invites the question
  the control arm already answers.
- **Say numbers as words.** "Plus twenty-four point seven" carries. "Plus two four
  point seven" does not.
- **Don't run `replicate` live.** Under three seconds, but three seconds of a
  progress bar is dead air. The report is already open.
- **One take per beat, not one take for the video.** Record each section
  separately and cut them together. A five-minute unbroken take will cost you an
  evening.
