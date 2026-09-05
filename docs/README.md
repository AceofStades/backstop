# Documentation

Working notes for the revenue-recovery agent. These are written for two readers:
a future maintainer picking the project up cold, and an interviewer asking why a
particular thing is built the way it is.

| Doc | What it covers |
|---|---|
| [`01-decisions.md`](01-decisions.md) | Why Track 03, why these three leaks, what we rejected and why |
| [`02-architecture.md`](02-architecture.md) | The pipeline, module by module, and the seams between them |
| [`03-measurement.md`](03-measurement.md) | The control arm, the response model, and what the numbers do and do not prove |
| [`04-compliance.md`](04-compliance.md) | RBI and TRAI constraints, and why they live in code rather than prompts |
| [`05-worklog.md`](05-worklog.md) | What was built in what order, and the sixteen bugs found — including four that only the live API exposed — plus what the sensitivity and confusion analyses changed |
| [`06-panel-prep.md`](06-panel-prep.md) | Anticipated architecture-interview questions and honest answers |
| [`07-next-steps.md`](07-next-steps.md) | What is done, what is unfinished, and what to do first |

## The one-paragraph version

Money slips away from merchants in ways that look identical from the outside (a
payment did not arrive) but need completely different responses. This agent reads
the *reason* a payment failed, picks an intervention that could plausibly work for
that specific reason, refuses to act at all when it is not confident, executes
inside hard limits that it cannot talk itself past, writes an append-only record of
everything it did and everything it checked first, and then measures what it
recovered against a held-out control group that it deliberately left alone.

The measurement is the point. An agent that reports gross recovery is reporting the
weather.
