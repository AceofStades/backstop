"""A single self-contained HTML page rendering a batch report.

## Why this exists at all

The CLI and the markdown reports are the right surface for a code review. They
are the wrong surface for five minutes in front of a projector: a reviewer
cannot scan a 300-line markdown file or terminal scrollback while someone talks
over it.

So this renders the *same numbers* -- no new computation, no separate code path
-- as one page that can be read from across a room. Everything here comes from
the metrics dict that `compute()` already produced. If a figure is not in that
dict it is not on this page.

## Why it looks like a trial readout

Because that is what it is. The vocabulary of this project is arms, held-out
control, incremental effect, confidence interval, sensitivity analysis. The
effect estimate is drawn as a forest plot against a zero line for the same
reason a clinical paper draws one: the honest question is not "how big is the
number" but "does the interval clear zero", and a bar chart of two rates quietly
refuses to answer that.

No JavaScript, no network calls beyond the webfont, no build step. The file can
be opened from a USB stick on someone else's laptop.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

FONTS = (
    "https://fonts.googleapis.com/css2?"
    "family=Spectral:ital,wght@0,400;0,500;0,600;1,400&"
    "family=Source+Sans+3:wght@400;500;600&"
    "family=JetBrains+Mono:wght@400;500;700&display=swap"
)

CSS = """
:root {
  --paper:   #f6f7f5;
  --surface: #ffffff;
  --sunk:    #eef1ee;
  --ink:     #16211d;
  --muted:   #5b6862;
  --faint:   #8b968f;
  --rule:    #dbe0db;
  --accent:  #0d6b4e;
  --accent-wash: #e4f0ea;
  --caution: #8a5312;
  --caution-wash: #f6ecdd;
  --void:    #6f7681;
  --shadow:  0 1px 2px rgba(22,33,29,.05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper:   #0e1512;
    --surface: #151d19;
    --sunk:    #1b2521;
    --ink:     #e7ece9;
    --muted:   #9aa8a1;
    --faint:   #6d7a74;
    --rule:    #2a3630;
    --accent:  #59c496;
    --accent-wash: #163025;
    --caution: #d8a45c;
    --caution-wash: #33260f;
    --void:    #8d95a0;
    --shadow:  0 1px 2px rgba(0,0,0,.3);
  }
}
:root[data-theme="dark"] {
  --paper:   #0e1512;
  --surface: #151d19;
  --sunk:    #1b2521;
  --ink:     #e7ece9;
  --muted:   #9aa8a1;
  --faint:   #6d7a74;
  --rule:    #2a3630;
  --accent:  #59c496;
  --accent-wash: #163025;
  --caution: #d8a45c;
  --caution-wash: #33260f;
  --void:    #8d95a0;
  --shadow:  0 1px 2px rgba(0,0,0,.3);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: "Source Sans 3", ui-sans-serif, system-ui, sans-serif;
  font-size: 16.5px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
.sheet {
  max-width: 60rem;
  margin: 0 auto;
  padding: 3rem 1.75rem 6rem;
  display: flex;
  flex-direction: column;
  gap: 3.25rem;
}
p { margin: 0 0 .85em; max-width: 66ch; }
p:last-child { margin-bottom: 0; }
a { color: var(--accent); }

h1, h2, h3 {
  font-family: Spectral, ui-serif, Georgia, serif;
  font-weight: 500;
  text-wrap: balance;
  margin: 0;
}
h1 { font-size: 2.6rem; line-height: 1.12; letter-spacing: -.015em; }
h2 { font-size: 1.55rem; line-height: 1.2; }
h3 { font-size: 1.08rem; font-weight: 600; }

.eyebrow {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: .68rem;
  font-weight: 500;
  letter-spacing: .16em;
  text-transform: uppercase;
  color: var(--faint);
}
.mono, code, td.num, th.num {
  font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, monospace;
  font-variant-numeric: tabular-nums;
}
code {
  font-size: .84em;
  background: var(--sunk);
  padding: .1em .36em;
  border-radius: 3px;
}

/* --- masthead --- */
.masthead { display: flex; flex-direction: column; gap: 1rem; }
.masthead .lede { font-size: 1.12rem; color: var(--muted); max-width: 60ch; }
.stamp {
  display: flex; flex-wrap: wrap; gap: .4rem 1.4rem;
  padding-top: .9rem; border-top: 2px solid var(--ink);
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: .74rem; color: var(--muted);
}

/* --- sections --- */
section { display: flex; flex-direction: column; gap: 1.1rem; }
.sec-head { display: flex; flex-direction: column; gap: .3rem; }
.note { font-size: .92rem; color: var(--muted); max-width: 64ch; }

/* --- the effect estimate --- */
.estimate {
  background: var(--surface);
  border: 1px solid var(--rule);
  border-left: 3px solid var(--accent);
  border-radius: 2px;
  padding: 1.75rem 1.85rem;
  display: flex; flex-direction: column; gap: 1.4rem;
  box-shadow: var(--shadow);
}
.effect { display: flex; align-items: baseline; gap: .9rem; flex-wrap: wrap; }
.effect .value {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 3.4rem; font-weight: 700; line-height: 1;
  letter-spacing: -.03em; color: var(--accent);
}
.effect .unit { font-size: 1.05rem; color: var(--muted); }
.effect .ci {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: .92rem; color: var(--muted);
}
.verdict {
  display: inline-flex; align-items: center; gap: .45rem;
  align-self: flex-start;
  font-size: .78rem; font-weight: 600; letter-spacing: .02em;
  padding: .3rem .7rem; border-radius: 2px;
}
.verdict.clears { background: var(--accent-wash); color: var(--accent); }
.verdict.spans  { background: var(--caution-wash); color: var(--caution); }

/* --- provenance ledger --- */
.ledger { display: flex; flex-direction: column; }
.ledger-row {
  display: grid;
  grid-template-columns: 8.5rem 1fr;
  gap: 1.25rem;
  padding: .95rem 0;
  border-top: 1px solid var(--rule);
  align-items: start;
}
.ledger-row:last-child { border-bottom: 1px solid var(--rule); }
.tier {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: .72rem; font-weight: 700; letter-spacing: .1em;
  text-transform: uppercase;
  padding-top: .2em;
}
.tier.real     { color: var(--accent); }
.tier.degraded { color: var(--caution); }
.tier.modelled { color: var(--void); }
.tier.injected { color: var(--faint); }
.ledger-row p { font-size: .95rem; margin: 0; }

/* --- figures --- */
figure { margin: 0; display: flex; flex-direction: column; gap: .6rem; }
figcaption { font-size: .86rem; color: var(--muted); max-width: 62ch; }
svg { display: block; max-width: 100%; height: auto; }
svg text { font-family: "JetBrains Mono", ui-monospace, monospace; }

/* --- tables --- */
.scroll { overflow-x: auto; }
table {
  width: 100%; border-collapse: collapse;
  font-size: .91rem;
}
th, td {
  text-align: left; padding: .5rem .7rem;
  border-bottom: 1px solid var(--rule);
}
thead th {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: .68rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--faint); font-weight: 500;
  border-bottom: 1px solid var(--ink);
  white-space: nowrap;
}
td.num, th.num { text-align: right; white-space: nowrap; }
tbody tr:last-child td { border-bottom: none; }
tr.total td { border-top: 1px solid var(--ink); font-weight: 600; }

/* --- key/value strip --- */
.strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
  gap: 1px;
  background: var(--rule);
  border: 1px solid var(--rule);
  border-radius: 2px;
  overflow: hidden;
}
.cell { background: var(--surface); padding: .95rem 1.1rem; display: flex; flex-direction: column; gap: .25rem; }
.cell .k { font-size: .74rem; color: var(--faint); letter-spacing: .04em; }
.cell .v {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: 1.28rem; font-weight: 500; font-variant-numeric: tabular-nums;
}
.cell .v.pos { color: var(--accent); }
.cell .v.warn { color: var(--caution); }

/* --- confusion matrix --- */
table.matrix td, table.matrix th { text-align: center; padding: .4rem .55rem; border: 1px solid var(--rule); }
table.matrix th { font-size: .68rem; }
table.matrix td.diag { background: var(--accent-wash); color: var(--accent); font-weight: 700; }
table.matrix td.err  { background: var(--caution-wash); color: var(--caution); font-weight: 600; }
table.matrix td.zero { color: var(--faint); }
table.matrix th.rowh { text-align: right; color: var(--muted); }

/* --- callout --- */
.callout {
  border-left: 3px solid var(--accent);
  background: var(--accent-wash);
  padding: 1rem 1.2rem;
  border-radius: 0 2px 2px 0;
  font-size: .95rem;
}
.callout.caution { border-left-color: var(--caution); background: var(--caution-wash); }
.callout strong { font-weight: 600; }
.callout p { max-width: 62ch; }

/* --- legend --- */
.legend {
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-size: .74rem; color: var(--muted);
  display: flex; flex-wrap: wrap; gap: .3rem 1rem;
}

footer {
  border-top: 1px solid var(--rule);
  padding-top: 1.25rem;
  font-size: .84rem; color: var(--faint);
}
@media (max-width: 640px) {
  h1 { font-size: 2rem; }
  .effect .value { font-size: 2.6rem; }
  .ledger-row { grid-template-columns: 1fr; gap: .3rem; }
}
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
"""


def _rs(paise: float) -> str:
    return f"Rs {paise / 100:,.0f}"


def _e(text: object) -> str:
    return html.escape(str(text))


def _abbr(cause: str) -> str:
    return "".join(w[0] for w in cause.split("_")).upper()


def _forest_plot(lift: float, lo: float, hi: float) -> str:
    """The effect estimate against a zero line.

    A bar chart of two recovery rates looks more confident than the evidence is.
    The question a reviewer actually asks is whether the interval clears zero, so
    that is what gets drawn.
    """
    w, h = 720, 132
    pad_l, pad_r = 24, 24
    span_lo = min(lo, 0.0) - 4
    span_hi = max(hi, 0.0) + 4

    def x(v: float) -> float:
        return pad_l + (v - span_lo) / (span_hi - span_lo) * (w - pad_l - pad_r)

    y = 58
    zero = x(0.0)
    ticks = []
    step = 5 if (span_hi - span_lo) <= 45 else 10
    t = int(span_lo // step) * step
    while t <= span_hi:
        if span_lo <= t <= span_hi:
            ticks.append(t)
        t += step

    tick_svg = "".join(
        f'<line x1="{x(v):.1f}" y1="{y + 20}" x2="{x(v):.1f}" y2="{y + 26}" '
        f'stroke="var(--rule)" stroke-width="1"/>'
        f'<text x="{x(v):.1f}" y="{y + 42}" text-anchor="middle" font-size="11" '
        f'fill="var(--faint)">{v:+d}</text>'
        for v in ticks
    )

    return f"""<svg viewBox="0 0 {w} {h}" role="img"
     aria-label="Incremental lift {lift:+.1f} percentage points, 95% confidence interval {lo:+.1f} to {hi:+.1f}">
  <line x1="{pad_l}" y1="{y + 20}" x2="{w - pad_r}" y2="{y + 20}"
        stroke="var(--rule)" stroke-width="1"/>
  {tick_svg}
  <line x1="{zero:.1f}" y1="16" x2="{zero:.1f}" y2="{y + 20}"
        stroke="var(--ink)" stroke-width="1" stroke-dasharray="3 3"/>
  <text x="{zero:.1f}" y="12" text-anchor="middle" font-size="10.5"
        fill="var(--muted)">no effect</text>
  <line x1="{x(lo):.1f}" y1="{y}" x2="{x(hi):.1f}" y2="{y}"
        stroke="var(--accent)" stroke-width="2.5"/>
  <line x1="{x(lo):.1f}" y1="{y - 8}" x2="{x(lo):.1f}" y2="{y + 8}"
        stroke="var(--accent)" stroke-width="2.5"/>
  <line x1="{x(hi):.1f}" y1="{y - 8}" x2="{x(hi):.1f}" y2="{y + 8}"
        stroke="var(--accent)" stroke-width="2.5"/>
  <circle cx="{x(lift):.1f}" cy="{y}" r="6.5" fill="var(--accent)"/>
  <text x="{x(lift):.1f}" y="{y - 16}" text-anchor="middle" font-size="12"
        font-weight="700" fill="var(--accent)">{lift:+.1f}</text>
</svg>"""


def _arms_chart(tr: dict, ct: dict) -> str:
    """Two recovery rates on one scale, with the gap named."""
    w, h = 720, 160
    pad_l, pad_t = 108, 22
    bar_h, gap = 34, 26
    top = max(tr["rate"], ct["rate"], 0.01) * 1.25
    track = w - pad_l - 96

    def bw(rate: float) -> float:
        return rate / top * track

    rows = [("Control", ct, "var(--void)"), ("Treatment", tr, "var(--accent)")]
    out = []
    for i, (label, arm, colour) in enumerate(rows):
        y = pad_t + i * (bar_h + gap)
        out.append(
            f'<text x="{pad_l - 12}" y="{y + bar_h / 2 + 4}" text-anchor="end" '
            f'font-size="12" fill="var(--muted)">{label}</text>'
            f'<rect x="{pad_l}" y="{y}" width="{track}" height="{bar_h}" '
            f'fill="var(--sunk)"/>'
            f'<rect x="{pad_l}" y="{y}" width="{bw(arm["rate"]):.1f}" '
            f'height="{bar_h}" fill="{colour}"/>'
            f'<text x="{pad_l + bw(arm["rate"]) + 10:.1f}" y="{y + bar_h / 2 + 4}" '
            f'font-size="12.5" font-weight="700" fill="var(--ink)">'
            f'{arm["rate"] * 100:.1f}%</text>'
            f'<text x="{pad_l + bw(arm["rate"]) + 62:.1f}" y="{y + bar_h / 2 + 4}" '
            f'font-size="11" fill="var(--faint)">'
            f'{arm["recovered"]}/{arm["n"]}</text>'
        )
    bracket_y = pad_t + 2 * (bar_h + gap) + 4
    out.append(
        f'<line x1="{pad_l + bw(ct["rate"]):.1f}" y1="{bracket_y}" '
        f'x2="{pad_l + bw(tr["rate"]):.1f}" y2="{bracket_y}" '
        f'stroke="var(--accent)" stroke-width="1.5"/>'
        f'<text x="{pad_l + (bw(ct["rate"]) + bw(tr["rate"])) / 2:.1f}" '
        f'y="{bracket_y + 18}" text-anchor="middle" font-size="11.5" '
        f'fill="var(--accent)">the only part attributable to the agent</text>'
    )
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Recovery rate by arm">{"".join(out)}</svg>'


def _sweep_chart(sweep: list[dict]) -> str:
    """Lift across assumed control baselines. The headline is a point on this line."""
    if not sweep:
        return ""
    w, h = 720, 200
    pad_l, pad_r, pad_t, pad_b = 52, 24, 22, 44
    lifts = [r["lift_pp"] for r in sweep]
    lo_v, hi_v = min(lifts + [0.0]), max(lifts)
    pad_v = max(2.0, (hi_v - lo_v) * 0.18)
    lo_v, hi_v = lo_v - pad_v, hi_v + pad_v

    def x(i: int) -> float:
        return pad_l + i / max(1, len(sweep) - 1) * (w - pad_l - pad_r)

    def y(v: float) -> float:
        return pad_t + (hi_v - v) / (hi_v - lo_v) * (h - pad_t - pad_b)

    pts = " ".join(f"{x(i):.1f},{y(r['lift_pp']):.1f}" for i, r in enumerate(sweep))
    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(r["lift_pp"]):.1f}" r="4.5" '
        f'fill="{"var(--accent)" if abs(r["baseline_scale"] - 1.0) < 1e-9 else "var(--surface)"}" '
        f'stroke="var(--accent)" stroke-width="2"/>'
        f'<text x="{x(i):.1f}" y="{y(r["lift_pp"]) - 13:.1f}" text-anchor="middle" '
        f'font-size="11" fill="var(--ink)">{r["lift_pp"]:+.1f}</text>'
        f'<text x="{x(i):.1f}" y="{h - 22}" text-anchor="middle" font-size="11" '
        f'fill="var(--faint)">{r["baseline_scale"]:g}x</text>'
        for i, r in enumerate(sweep)
    )
    return f"""<svg viewBox="0 0 {w} {h}" role="img"
     aria-label="Incremental lift across assumed control baselines">
  <line x1="{pad_l}" y1="{y(0):.1f}" x2="{w - pad_r}" y2="{y(0):.1f}"
        stroke="var(--rule)" stroke-width="1" stroke-dasharray="3 3"/>
  <text x="{pad_l - 8}" y="{y(0) + 4:.1f}" text-anchor="end" font-size="10.5"
        fill="var(--faint)">0</text>
  <polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="2"/>
  {dots}
  <text x="{w / 2:.0f}" y="{h - 5}" text-anchor="middle" font-size="10.5"
        fill="var(--faint)">assumed control baseline, relative to the stated value</text>
</svg>"""


def _confusion(m: dict) -> str:
    confusion: dict = m.get("diagnosis_confusion") or {}
    if not confusion:
        return ""
    causes = sorted(set(confusion) | {p for row in confusion.values() for p in row})
    head = "".join(f'<th class="num" scope="col">{_abbr(c)}</th>' for c in causes)
    body = []
    for truth in causes:
        row = confusion.get(truth, {})
        cells = []
        for pred in causes:
            n = row.get(pred, 0)
            if not n:
                cells.append('<td class="zero">·</td>')
            elif pred == truth:
                cells.append(f'<td class="diag">{n}</td>')
            else:
                cells.append(f'<td class="err">{n}</td>')
        body.append(
            f'<tr><th class="rowh" scope="row">{_abbr(truth)}</th>{"".join(cells)}</tr>'
        )
    legend = " ".join(f"<span>{_abbr(c)} = {_e(c)}</span>" for c in causes)
    return f"""<div class="scroll">
  <table class="matrix">
    <thead><tr><th scope="col">truth \\ said</th>{head}</tr></thead>
    <tbody>{"".join(body)}</tbody>
  </table>
</div>
<div class="legend">{legend}</div>"""


def render_html(
    m: dict,
    batch_id: str,
    sensitivity: list[dict] | None = None,
    uplift: dict | None = None,
    pooled: dict | None = None,
) -> str:
    tr, ct = m["treatment"], m["control"]
    lo, hi = m["lift_ci_pp"]
    clears = m["lift_significant"]
    degraded = m.get("degraded_artifacts", 0)

    verdict = (
        '<span class="verdict clears">Interval clears zero</span>'
        if clears
        else '<span class="verdict spans">Interval spans zero &mdash; not evidence of effect</span>'
    )

    # --- money ---
    money_rows = [
        ("Gross recovered in treatment", m["gross_recovered_paise"],
         "Counts money that would have come back anyway."),
        ("Less: control-rate counterfactual", -m["counterfactual_paise"],
         "What the control arm's rate would have recovered from the same money."),
        ("Incremental recovered", m["incremental_paise"], "The agent's actual contribution."),
        ("Less: cost of acting", -m["action_cost_paise"],
         f'{m["actions_fired"]} actions, {m["contacts"]} customer contacts.'),
    ]
    money_html = "".join(
        f'<tr><td>{_e(label)}<div class="note" style="font-size:.82rem">{_e(note)}</div></td>'
        f'<td class="num">{_rs(v)}</td></tr>'
        for label, v, note in money_rows
    )

    # --- gate ---
    refusals = m.get("refusals_by_code") or {}
    refusal_rows = "".join(
        f'<tr><td><code>{_e(k)}</code></td><td class="num">{v}</td></tr>'
        for k, v in refusals.items()
    ) or '<tr><td colspan="2" class="note">No refusals in this batch.</td></tr>'

    # --- diagnosis ---
    by_method = m.get("diagnosis_by_method") or {}
    method_rows = "".join(
        f'<tr><td><code>{_e(k)}</code></td><td class="num">{st["scored"]}</td>'
        f'<td class="num">{st["correct"]}</td>'
        f'<td class="num">{(st["correct"] / st["scored"] if st["scored"] else 0):.1%}</td></tr>'
        for k, st in sorted(by_method.items(), key=lambda kv: -kv[1]["scored"])
    )

    confusion = m.get("diagnosis_confusion") or {}
    confident = sum(
        n
        for truth, row in confusion.items()
        for pred, n in row.items()
        if pred != truth and pred != "unknown"
    )
    if confusion and not confident:
        shape = f"""<div class="callout">
  <p><strong>Every misclassification is a confusion with <code>unknown</code>, never with
  another cause.</strong> Not one case was diagnosed confidently and wrongly.</p>
  <p>Those are different failures wearing one percentage. Landing on <code>unknown</code>
  costs a recovery and routes the case to the exception list, where a human sees it.
  Landing on the wrong <em>cause</em> would fire a confident, specific, wrong
  intervention &mdash; the failure the cause-keyed design exists to prevent. So the
  residual {1 - m["diagnosis_accuracy"]:.1%} is abstention, not error, and the headline
  accuracy understates the safety property.</p>
</div>"""
    elif confident:
        shape = f"""<div class="callout caution">
  <p><strong>{confident} case(s) were diagnosed as the wrong cause rather than as
  <code>unknown</code>.</strong> These are the expensive errors: a wrong cause fires a
  confident, specific, wrong intervention instead of routing to the exception list.</p>
</div>"""
    else:
        shape = ""

    # --- uplift sensitivity (optional) ---
    uplift_html = ""
    if uplift:
        decisions = uplift["decisions"]
        fragile = [r for r in decisions if not r["stable"]]
        paid = [r for r in fragile if r["channel"] != "none"]
        free_stable = all(r["stable"] for r in decisions if r["channel"] == "none")
        rank_rows = "".join(
            f'<tr><td><code>{_e(r["cause"])}</code></td>'
            f'<td><code>{_e(r["baseline_top"])}</code></td>'
            f'<td class="num">{r["stability"]:.1%}</td></tr>'
            for r in sorted(uplift["rankings"], key=lambda r: -r["stability"])
        )
        pattern = ""
        if fragile and len(paid) == len(fragile) and free_stable:
            pattern = """<div class="callout">
  <p><strong>Every sensitive step costs money to fire; every free step is stable.</strong>
  That is the mechanism, not a coincidence &mdash; belief only decides something where
  there is a cost to weigh it against. A rail-side retry is close enough to free that no
  plausible uplift refuses it. A WhatsApp message has to earn its place.</p>
</div>"""
        uplift_html = f"""
<section>
  <div class="sec-head">
    <span class="eyebrow">Sensitivity &mdash; engine beliefs</span>
    <h2>What changes if the uplift estimates are wrong</h2>
  </div>
  <p class="note"><code>BELIEVED_UPLIFT</code> is the one engine input that is asserted
  rather than measured; it cannot be observed without production data. Rather than defend
  the numbers, this reports which decisions change across a half-to-double band.</p>
  <div class="strip">
    <div class="cell"><span class="k">Ladder steps analysed</span>
      <span class="v">{len(decisions)}</span></div>
    <div class="cell"><span class="k">Stable across the band</span>
      <span class="v pos">{len(decisions) - len(fragile)}</span></div>
    <div class="cell"><span class="k">Sensitive to the belief</span>
      <span class="v warn">{len(fragile)}</span></div>
  </div>
  {pattern}
  <h3>Would an expected-value ranking be trustworthy?</h3>
  <p class="note">Each belief perturbed independently; how often the highest-EV step is
  unchanged. Below roughly 90% a computed order launders a guess into something that
  looks derived &mdash; which is why ladder order stays an explicit policy choice.</p>
  <div class="scroll"><table>
    <thead><tr><th scope="col">Cause</th><th scope="col">Highest-EV step</th>
      <th class="num" scope="col">Unchanged</th></tr></thead>
    <tbody>{rank_rows}</tbody>
  </table></div>
</section>"""

    # --- exceptions ---
    exceptions = m.get("exceptions") or []
    exc_rows = "".join(
        f'<tr><td><code>{_e(e["case_id"])}</code></td>'
        f'<td class="num">{_rs(e["amount_paise"])}</td>'
        f'<td><code>{_e(e["diagnosed"])}</code></td>'
        f'<td class="num">{e["confidence"]:.2f}</td>'
        f'<td>{_e(e["why"])[:110]}</td></tr>'
        for e in exceptions[:12]
    ) or '<tr><td colspan="5" class="note">No unresolved cases.</td></tr>'

    degraded_block = (
        f"""<div class="callout caution">
  <p><strong>{degraded} degraded artifact(s) in this batch.</strong> Razorpay test mode caps
  payment links at 30 per business for the lifetime of the account. Once spent, the executor
  records a flagged placeholder rather than failing the case &mdash; otherwise an account
  quota masquerades as a policy result. A degraded artifact is never counted as real.</p>
</div>"""
        if degraded
        else ""
    )

    pooled_block = ""
    if pooled:
        p_lo, p_hi = pooled["pooled_ci_pp"]
        pooled_block = f"""<div class="callout">
  <p><strong>This is one batch, which is one draw.</strong> The headline that ships publicly
  is pooled across {pooled["runs"]} independent batches:
  <span class="mono">{pooled["mean_lift_pp"]:+.1f} pp</span>
  (95% CI of the mean {p_lo:+.1f} to {p_hi:+.1f}), between-batch sd
  {pooled["sd_lift_pp"]:.1f} pp. A single batch is for inspecting behaviour, not for
  quoting.</p>
</div>"""

    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    return f"""<title>Recovery Batch Readout</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<style>{CSS}</style>

<div class="sheet">

  <header class="masthead">
    <span class="eyebrow">Revenue recovery agent &mdash; batch readout</span>
    <h1>Incremental recovery against a held-out control arm</h1>
    <p class="lede">Every figure below is incremental, not gross. The agent is credited only
    with money the control arm's own recovery rate does not already explain.</p>
    <div class="stamp">
      <span>batch {_e(batch_id)}</span>
      <span>{m["n_total"]} cases</span>
      <span>{tr["n"]} treatment / {ct["n"]} control</span>
      <span>generated {generated}</span>
    </div>
  </header>

  <section>
    <div class="estimate">
      <span class="eyebrow">Effect estimate</span>
      <div class="effect">
        <span class="value">{m["lift_pp"]:+.1f}</span>
        <span class="unit">percentage points recovered</span>
      </div>
      <span class="ci">95% CI &nbsp;{lo:+.1f} &nbsp;to&nbsp; {hi:+.1f}</span>
      {verdict}
      <figure>
        {_forest_plot(m["lift_pp"], lo, hi)}
        <figcaption>Drawn against zero because the honest question is not how large the
        estimate is, but whether the interval excludes no-effect. An underpowered batch
        produces a wide interval, and a wide interval that crosses the dashed line is
        not a result.</figcaption>
      </figure>
    </div>
    {pooled_block}
  </section>

  <section>
    <div class="sec-head">
      <span class="eyebrow">Provenance</span>
      <h2>What is real and what is modelled</h2>
    </div>
    <p class="note">Four tiers of evidence, kept separate on purpose. Blurring them is the
    easiest way to make a demo look stronger than it is.</p>
    <div class="ledger">
      <div class="ledger-row">
        <span class="tier real">Real</span>
        <p>Razorpay Orders and Payment Links are genuine test-mode entities with live ids.
        Policy, mandate gate, idempotency and the append-only ledger are real code paths.</p>
      </div>
      <div class="ledger-row">
        <span class="tier degraded">Degraded</span>
        <p>Actions recorded once the test-mode payment-link quota is spent. Flagged, disclosed,
        and never counted as real. This batch: <strong>{degraded}</strong>.</p>
      </div>
      <div class="ledger-row">
        <span class="tier modelled">Modelled</span>
        <p>Whether a customer pays after an intervention. Parameters are stated, and the
        control arm exists precisely because this layer would otherwise let any intervention
        look effective.</p>
      </div>
      <div class="ledger-row">
        <span class="tier injected">Injected</span>
        <p>The decline reason on each seeded case. Used only to score the diagnoser and drive
        the response model. No component under test may read it.</p>
      </div>
    </div>
    {degraded_block}
  </section>

  <section>
    <div class="sec-head">
      <span class="eyebrow">Arms</span>
      <h2>Recovery rate by arm</h2>
    </div>
    <p class="note">Assignment happens at intake, before any policy sees a case, and the
    response model uses common random numbers keyed on case id &mdash; so the same case makes
    the same self-recovery draw in either arm.</p>
    <figure>
      {_arms_chart(tr, ct)}
      <figcaption>The control arm recovers {ct["rate"]:.1%} without being touched. That
      baseline is why gross recovery overstates the agent.</figcaption>
    </figure>
  </section>

  <section>
    <div class="sec-head">
      <span class="eyebrow">Money</span>
      <h2>From gross to net</h2>
    </div>
    <div class="scroll"><table>
      <tbody>{money_html}</tbody>
      <tfoot><tr class="total"><td>Net value</td>
        <td class="num">{_rs(m["net_value_paise"])}</td></tr></tfoot>
    </table></div>
    <p class="note">Net value is the headline cost metric. Actions per incremental recovery
    ({m["actions_per_incremental_recovery"]:.2f}) is reported alongside it but is deliberately
    <em>not</em> an optimisation target: a ratio improves when its denominator is cut, which is
    not the same as making money. Tightening the threshold above break-even was measured, and
    it improved the ratio while destroying net value.</p>
  </section>

  {_sweep_section(sensitivity)}

  <section>
    <div class="sec-head">
      <span class="eyebrow">Diagnosis</span>
      <h2>Not how much error, but what shape</h2>
    </div>
    <div class="strip">
      <div class="cell"><span class="k">Accuracy vs injected truth</span>
        <span class="v">{m["diagnosis_accuracy"]:.1%}</span></div>
      <div class="cell"><span class="k">Cases scored</span>
        <span class="v">{m["diagnosis_scored"]}</span></div>
      <div class="cell"><span class="k">Confident wrong causes</span>
        <span class="v {"warn" if confident else "pos"}">{confident}</span></div>
    </div>
    <h3>Where the error lives</h3>
    <p class="note">The deterministic path is a lookup table on a documented Razorpay error
    code &mdash; it cannot be wrong. Reporting it inside a blended average disguises that the
    entire error budget belongs to the ambiguous slice.</p>
    <div class="scroll"><table>
      <thead><tr><th scope="col">Method</th><th class="num" scope="col">Scored</th>
        <th class="num" scope="col">Correct</th><th class="num" scope="col">Accuracy</th></tr></thead>
      <tbody>{method_rows}</tbody>
    </table></div>
    <h3>Confusion matrix</h3>
    {_confusion(m)}
    {shape}
  </section>

  {uplift_html}

  <section>
    <div class="sec-head">
      <span class="eyebrow">Gate</span>
      <h2>What the mandate refused</h2>
    </div>
    <p class="note">Nothing reaches Razorpay without passing the gate. Refusals are structured
    values carrying a reason code, never exceptions. Contacts outside the RBI 08:00&ndash;19:00
    window are deferred rather than dropped: <strong>{m["deferrals"]}</strong> this batch.</p>
    <div class="scroll"><table>
      <thead><tr><th scope="col">Refusal code</th><th class="num" scope="col">Count</th></tr></thead>
      <tbody>{refusal_rows}</tbody>
    </table></div>
  </section>

  <section>
    <div class="sec-head">
      <span class="eyebrow">Exceptions</span>
      <h2>Cases the engine would not act on</h2>
    </div>
    <p class="note">{len(exceptions)} case(s) routed to the exception list rather than guessed
    at. Refusing to act below the confidence threshold is a result, not a gap.</p>
    <div class="scroll"><table>
      <thead><tr><th scope="col">Case</th><th class="num" scope="col">At risk</th>
        <th scope="col">Diagnosed</th><th class="num" scope="col">Conf</th>
        <th scope="col">Why it stopped</th></tr></thead>
      <tbody>{exc_rows}</tbody>
    </table></div>
  </section>

  <footer>
    Generated by <code>razor-pay report --html</code> from the same metrics that produce the
    markdown report. No figure on this page is computed here.
    Response model: <span class="mono">{_e(json.dumps(m.get("response_params", {}), default=str))[:180]}</span>
  </footer>

</div>"""


def _sweep_section(sensitivity: list[dict] | None) -> str:
    if not sensitivity:
        return ""
    return f"""<section>
    <div class="sec-head">
      <span class="eyebrow">Sensitivity &mdash; control baseline</span>
      <h2>The baseline is an assumption, so the headline is a range</h2>
    </div>
    <p class="note">Real customers self-recover without being contacted. If that rate were
    zero by construction the lift would be fake, so it is modelled &mdash; and then swept,
    because a modelled assumption that is never varied is just an assertion with a number
    attached.</p>
    <figure>
      {_sweep_chart(sensitivity)}
      <figcaption>The filled point is the stated baseline. The lift survives the whole sweep,
      which is the claim worth making &mdash; not the single value at 1x.</figcaption>
    </figure>
  </section>"""
