# RECON-4 — UI, Feature and Video Specification
### Supplement to SPEC.md. Covers phases P6–P10. Does not change P0–P5.

---

## 0. Position

The video is the only artifact judges see. So presentation quality is not decoration here — it is the delivery mechanism for the rigour built in P0–P5. Every screen in this document exists to make one of the seven demo beats legible on camera.

**The filter for every feature:** can it be shown working, on screen, in under thirty seconds, and does it make the correctness story *more* believable? If no to either, it does not get built. "More features" is not the goal; "more of the existing rigour made visible" is.

**Hard boundary:** the API is the contract. The frontend reads from endpoints and never computes a number. Any figure on screen must be traceable to a value the pipeline produced, because the moment the UI computes something, the UI can lie — and the entire pitch is that this system doesn't.

---

## 1. Design direction

Reconciliation UI has a strong default look — the SaaS admin dashboard: rounded cards, soft grey shadows, a blue accent, sparklines. Every fintech tool ships it. Deliberately going elsewhere.

### The concept: **the ledger sheet**

Draw from the actual material of the domain — the accountant's columnar pad. Ruled horizontal lines, hairline vertical column rules, figures in a tabular-lining face aligned on the decimal, red for the unreconciled. Not skeuomorphic paper; the *structure* of a ledger applied to a modern dark interface. It makes the app look like it belongs to finance rather than to a startup template, and it gives the numbers — which are the point — visual primacy over chrome.

### Tokens

```
--ink        #0E1116   page ground (near-black, cool, not tinted warm)
--rule       #1E242C   hairline column and row rules
--paper      #151A21   raised surfaces (tables, panels)
--figure     #E8EAED   primary numerals and text
--muted      #7C8794   labels, secondary
--verified   #4EA88A   accepted match, control passing (desaturated green, not neon)
--flag       #C9564B   critical exception, unreconciled residual
--caution    #C79A45   warn-severity exception
--trace      #5B8DEF   active/highlighted record in a trace
```

One accent family, used semantically only. No decorative gradients anywhere.

### Type

- **Figures and tables:** `IBM Plex Mono` — tabular by default, so columns of rupees align without effort, and it reads as machine output rather than marketing.
- **Prose and headings:** `Inter`, tight tracking at display sizes.

Two families, sharply distinct in role: mono means *this is data the system produced*, sans means *this is a human explaining*. That distinction is doing real work when the Q&A agent's prose sits next to cited record IDs.

Avoid: all-caps eyebrow labels, one-word colour accents inside headlines, `→` glued to button text.

### Motion

Exactly one orchestrated moment: the pipeline run (§2.1). Records visibly flow through the hops. Everything else is static or responds only to a click. No hover lifts on cards, no scroll-triggered fades. Restraint everywhere else is what makes the run sequence land on video.

`prefers-reduced-motion` respected; the run then steps rather than animates.

---

## 2. Screens

Priority-ordered. **If time runs short, build 1, 2, 3 and 6 and skip the rest** — those four carry five of the seven beats.

### 2.1 Run console — *the hero* (beat 2, 3)

The landing screen. A ledger-ruled canvas with four labelled columns (Orders · Gateway · Bank · Ledger) and records travelling left to right as the pipeline executes.

- Records enter as small rows; on match they snap into a linked group and the group's rule turns `--verified`.
- Tier badge appears per match: `T1` `T2` `T4`.
- Failed items drop into an exceptions gutter along the bottom, tinted by severity.
- Live counters across the top: records processed, hop rates, elapsed time, LLM calls.
- On completion the metrics band (§2.2) slides up beneath.

**Backend:** `GET /api/run/stream` (Server-Sent Events). The pipeline emits an event per record decision. Add `--pace <ms>` to the CLI so events can be spaced for the camera — deterministic *and* paced is the combination that makes a clean single-take recording possible.

### 2.2 Metrics band (beat 3)

Not a card grid. A single horizontal ledger strip, hairline-ruled between figures:

```
FULL CHAIN    LINK PRECISION   FALSE MATCH   RECONCILED    AT RISK       LLM
  51 / 56         100.0%          0.00%      ₹4,71,208.00  ₹41,236.00   3 calls
   91.1%                                                    14 open      1 kept
```

`FALSE MATCH 0.00%` gets the largest type on the screen. It is the number the brief is really asking for, and making it the visual anchor is the whole argument in one glance.

### 2.3 Reconstruction viewer (beat 4) — *the best thirty seconds in the video*

Click any tier-2 match. A panel renders the subset-sum arithmetic as a worked ledger column:

```
BANK LINE   2026-08-12   ₹42,251.29   narration: NEFT CR AXIS BANK SETTLEMENT
                                      no UTR recovered  ·  no settlement id

  pay_9x1a   card    12,000.00   −240.00  −43.20    11,716.80
  pay_9x1b   upi      8,500.00     −0.00   −0.00     8,500.00
  pay_9x1c   card    15,000.00   −300.00  −54.00    14,646.00
  pay_9x1d   upi      3,200.00     −0.00   −0.00     3,200.00
  pay_9x1e   nb       6,300.00    −94.50  −17.01     6,188.49
  rfnd_7k2c  refund  −2,000.00                     −2,000.00
                                          ────────────────────
                                          reconstructed  42,251.29
                                          bank line      42,251.29
                                          delta               0.00   ✓
```

Rows stream in one per ~180ms with the running subtotal counting up, then the delta resolves to `0.00` and turns `--verified`. This is the single most persuasive artifact in the project: it shows a matched record where *no shared key existed*, with every rupee accounted for and nothing hidden.

### 2.4 Exception queue (beat 3, 5)

Ledger table, sorted by ₹ at risk descending. Columns: code · severity rule (a coloured left edge, not a pill) · records · ₹ at risk · age · suggested action. Row expands to the full evidence blob rendered as a table, not raw JSON.

Filters: hop, severity, code. A "critical only" toggle for the video.

### 2.5 The refusal card (beat 5) — *build this deliberately*

`AMBIGUOUS_SETTLEMENT` gets its own presentation, not a normal row. Two settlement candidates side by side, every field identical, rendered as a mirrored pair:

```
        CANDIDATE A                    CANDIDATE B
        setl_0810_a                    setl_0810_b
        value date  2026-08-12         value date  2026-08-12
        net         ₹23,417.50         net         ₹23,417.50
        rows        4                  rows        4
        utr         —                  utr         —

              These are indistinguishable from the available data.
              No match proposed. Resolve by confirming the settlement
              ID in the gateway dashboard.
```

Nobody demos a refusal. This screen is the direct visual answer to *"one cherry-picked match proves nothing"* and it should get a beat of silence in the video.

### 2.6 Clearing account control (beat 6)

A running T-account for `PG_RECEIVABLE`, ledger-ruled, with the balance column tracking down the page. Then the control line:

```
GL residual, computed from journal entries alone     ₹41,236.00
Exception exposure, computed from the queue alone    ₹41,236.00
                                                     ─────────
Difference                                                0.00   ✓
```

Two numbers, derived by completely independent paths, equal to the paisa. Say on camera that if these ever disagree the pipeline aborts, because the control exists to catch the system's own bugs.

### 2.7 Chain explorer (beat 7 support)

Search an order ID; render the four-hop chain horizontally with per-hop status, tier and evidence link. Broken hops show the exception inline. Good for answering a live question and for showing an intact chain next to a broken one.

### 2.8 Q&A console (beat 7)

Chat panel where **tool calls are visible**, not hidden:

```
you    Why is the credit on 12 Aug ₹2,000 short?

       ⟶ explain_settlement(ref="UTIB0004417238")
       ⟵ 5 captures, 1 refund, net 42,251.29

claude The 12 Aug credit nets a ₹2,000.00 refund (rfnd_7k2c) issued
       against ORD-0987 on 10 Aug. Gross captures were ₹45,000.00;
       after ₹634.50 fees, ₹114.21 GST and that refund, ₹42,251.29
       settled. Records: setl_0810, bank line BL-0007.
```

Showing the tool call is the point — it demonstrates the model retrieved rather than recalled. Prose in Inter, IDs and figures in Plex Mono, so the grounding is visible typographically.

### 2.9 Cash position (optional, build last)

Horizontal stacked bar: cleared · in transit · disputed · unreconciled, with the unreconciled segment in `--flag` and labelled with the same number as §2.6. Beneath it, the next five business days' expected inflow from the settlement pipeline. Only worth building if P5 finished early.

---

## 3. Stack and API additions

**Frontend:** Vite + React + TypeScript + Tailwind. `recharts` only if §2.9 is built. `framer-motion` for the run console only. No component library — the ledger aesthetic is simpler hand-built than fought out of a kit.

**New endpoints** (all read-only except the run trigger):

```
POST /api/run                     -> {run_id}          start a run (params: seed, llm, pace)
GET  /api/run/{id}/stream         -> SSE               per-record decision events
GET  /api/run/{id}/metrics        -> §2.2 payload
GET  /api/run/{id}/exceptions     -> filterable list with evidence
GET  /api/match/{link_id}         -> reconstruction detail for §2.3
GET  /api/order/{order_id}/chain  -> §2.7
GET  /api/control/clearing        -> both numbers + per-code breakdown
POST /api/ask                     -> §2.8 (already specced in SPEC.md §9)
```

SSE event shape:
```json
{"seq":142,"kind":"match","hop":2,"tier":2,"a":["gw","setl_0810"],
 "b":["bank","BL-0007"],"confidence":0.98,"amount_p":4225129}
{"seq":143,"kind":"exception","code":"AMBIGUOUS_SETTLEMENT","severity":"critical",
 "amount_at_risk_p":2341750,"records":[...]}
```

---

## 4. Revised phase plan

P0–P5 unchanged. Tag `v0.1` after P2 as before — the fallback submission still exists.

| Phase | Scope | Est. |
|-------|-------|------|
| P6 | API layer: all §3 endpoints over the existing pipeline, SSE emitter, `--pace` flag | 3h |
| P7 | Frontend shell: tokens, type, layout, routing, ledger table primitives | 3h |
| P8 | Run console (§2.1) + metrics band (§2.2) — the hero | 4h |
| P9 | Reconstruction viewer (§2.3), exception queue (§2.4), refusal card (§2.5), clearing control (§2.6) | 5h |
| P10 | Chain explorer (§2.7), Q&A console (§2.8), README, video | 4h |

New total ≈ 43h. **Gate for each UI phase: the screen renders correctly from real API data with the LLM off.** No mock data in the frontend at any point — a screen that only works against fixtures will fail on camera.

---

## 5. Video production

### Recording principles

- **Record beat by beat, not one take.** Nine short clips assembled beats a nine-minute single take, and lets you retake beat 5 until the pause lands.
- **Determinism is a production asset.** Seed 42 gives an identical run every time, so a retake is free and the narration always matches the numbers on screen.
- **Use `--pace 150`** for the run console so the pipeline takes ~20 seconds instead of 3. Say on camera that it is paced for visibility and that the unpaced runtime is in the metrics band — do not let it look like the system is slow.
- 1080p minimum, browser zoom at 125%, no visible cursor hunting, no browser chrome.

### Shot list (target 4–5 minutes)

| # | Shot | Screen | Say |
|---|------|--------|-----|
| 1 | 25s | Data + ground truth file | "I generated this data, so here is exactly what that proves and what it doesn't." Pre-empting the attack is worth more than dodging it. |
| 2 | 30s | Run console, live | Records flowing, tier badges, exceptions dropping into the gutter |
| 3 | 20s | Metrics band | Say the false-match rate out loud. It is the number the brief is asking for. |
| 4 | 45s | Reconstruction viewer | "No UTR, no settlement ID. Here is how it matched, and here is every rupee." |
| 5 | 35s | Refusal card | "These two are indistinguishable. It refuses. A wrong match is worse than an honest gap." **Pause here.** |
| 6 | 30s | Clearing control | Two independent numbers, equal to the paisa. Mention the abort-on-mismatch behaviour. |
| 7 | 35s | Q&A console | Ask one question live, show the tool call and the cited IDs |
| 8 | 25s | Exception queue | Scroll the typed codes, ₹ at risk, suggested actions |
| 9 | 20s | Non-goals slide | Name the six things you deliberately did not build, precisely |

Shot 9 matters more than it looks. Naming FX, rolling reserve, TDS 194-O, chargeback lifecycle, multi-currency and GST-return matching as explicit exclusions signals that you know the domain's full shape and chose your scope. Teams that imply they handled everything fold under one specific question.

### The narration rule

Say the numbers on screen out loud, including the unflattering ones. If the full-chain rate is 91%, say 91% and say which nine percent didn't chain and why. The brief's phrase "an honest exception list" is not incidental language — it is the thing being tested, and a video that shows a system admitting what it couldn't do is the one that reads as built by someone who has thought about verification.
