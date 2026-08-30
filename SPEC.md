# RECON-4 — Build Specification
## AI Finance Controller · Track 04 · Four-way payment reconciliation agent

**Audience of this document:** an implementation model (e.g. Claude Code running a smaller model) plus Vyankatesh as reviewer. All design decisions are already made. Do not redesign; implement. Where this spec is silent, choose the simplest option that keeps every gate passing.

---

## 0. Rules for the implementing model

1. **Money is integer paise everywhere.** Column type `BIGINT`, variable suffix `_p`. Never `float`, never `double`, no arithmetic on rupee decimals. Convert to `₹x,xxx.xx` strings only at display time.
2. **Every random draw goes through one seeded RNG** (`random.Random(seed)` passed explicitly). Same seed ⇒ byte-identical dataset and byte-identical run report. This is tested (T-1).
3. **The pipeline must run end-to-end at every commit.** `make demo` (generate → load → run → report) must never be broken on `main`. Build a walking skeleton first, deepen later.
4. **Phase gates are mandatory.** Do not start phase N+1 until phase N's gate script passes. Gates are listed per phase and implemented in `tests/gates/`.
5. **The LLM is never load-bearing for correctness.** Every LLM-dependent feature must have a `--llm off` mode where the system still runs and reports honestly (items the LLM would have handled become exceptions). Develop against `MockLLM` first; wire the real API last.
6. **When matching is uncertain, raise an exception. Never guess.** A false match is a bug of the highest severity; a false exception is cosmetic. All tie-breaking logic must prefer refusal.
7. Python 3.12, PostgreSQL 16 (docker-compose), FastAPI, Pydantic v2, `typer` CLI, `pytest`. No ORM — plain SQL via `psycopg`, migrations as numbered `.sql` files in `db/migrations/`. No other frameworks without necessity.
8. Timezone: all timestamps IST, stored as `DATE` where only dates matter (most places). Business-day math uses `WEEKENDS = {SAT, SUN}` and `HOLIDAYS: set[date]` from `config.py` (leave it empty for the demo dataset; the function must still exist).

---

## 1. What we are building and what winning looks like

A reconciliation agent that closes the loop **order → gateway payment → bank settlement → general-ledger entry** over a synthetic-but-realistic batch, and reports:

- per-hop and **full-chain** match rates,
- **false-match rate** measured against a generated answer key,
- an exception queue with typed reason codes, severity, ₹-at-risk and suggested action,
- a **clearing-account control**: GL receivable residual computed independently must equal the exception queue's unreconciled value **to the paisa**,
- a grounded Q&A agent that answers questions about the data by calling tools, citing record IDs.

### The seven demo beats (these are the acceptance criteria for the whole project)

| # | Beat | Feature that serves it |
|---|------|------------------------|
| 1 | Show batch + answer key; state openly what synthetic data does/doesn't prove | Generator + `ground_truth.json` |
| 2 | Live run finishes in seconds | Deterministic tiers; LLM only on residue |
| 3 | Metrics incl. false-match rate said out loud | Scorer |
| 4 | Hard case: truncated-UTR settlement reconstructed by subset-sum, arithmetic shown | Hop-2 engine + evidence rendering |
| 5 | Honest refusal: identical twin settlements → AMBIGUOUS, human action named | Uniqueness rule + exception queue |
| 6 | Clearing account: ₹0.00 reconciled residual; residual == exception total | Hop-3 + control check |
| 7 | Live judge question answered with record IDs | Q&A tools |

Anything not serving these beats is out of scope. Explicit non-goals: FX/cross-border, rolling reserve, TDS 194-O, chargeback lifecycle beyond first appearance, multi-currency, multi-merchant, GST-return matching, auth/void flows.

---

## 2. Repository layout

```
recon4/
├── docker-compose.yml          # postgres:16 only
├── Makefile                    # setup, generate, load, run, report, serve, check, demo
├── config.py                   # seed, tolerances, fee card, dates, holidays
├── db/migrations/              # 001_schema.sql, 002_indexes.sql, ...
├── recon/
│   ├── cli.py                  # typer app: generate|load|run|report|serve
│   ├── moneymath.py            # paise helpers, fee+gst computation, rounding
│   ├── busdays.py              # business-day arithmetic
│   ├── generator/
│   │   ├── world.py            # clean-world builder
│   │   ├── defects.py          # defect injectors (one function per code)
│   │   └── truth.py            # ground_truth.json writer
│   ├── engine/
│   │   ├── hop1.py  hop2.py  hop3.py
│   │   ├── subsetsum.py
│   │   ├── verifier.py         # invariants V1..V6
│   │   └── pipeline.py         # orchestration, run record
│   ├── llm/
│   │   ├── client.py           # RealLLM (Anthropic API), MockLLM (canned)
│   │   ├── adjudicator.py      # tier-4
│   │   └── qa.py               # tool-calling Q&A loop
│   ├── scoring/scorer.py       # metrics vs ground truth
│   └── report/report.py        # terminal + JSON + HTML report
├── web/                        # single static dashboard page (phase 6 only)
├── data/                       # generated CSVs + ground_truth.json (gitignored)
└── tests/
    ├── gates/                  # gate_p1.py ... gate_p6.py
    └── unit/
```

---

## 3. Phase plan with gates

Time estimates assume one focused builder; total ≈ 24h. **The submission exists from the end of P2 onward** — every later phase is upside on a working system.

| Phase | Scope | Est. | Gate (script must pass) |
|-------|-------|------|-------------------------|
| P0 | Repo, compose, migrations, CLI skeleton, `make demo` prints stub report | 2h | `make demo` exits 0 |
| P1 | Generator (clean world + defects) + ground truth + loader | 4h | G1 below |
| P2 | Hop 1, Hop 2 (incl. subset-sum), verifier V1–V3, scorer, run report | 6h | G2 below |
| P3 | Hop 3 decomposition + clearing-account control (V4–V5) | 3h | G3 below |
| P4 | Tier-4 adjudicator behind verifier (MockLLM then real) | 3h | G4 below |
| P5 | Q&A tools + FastAPI endpoint | 3h | G5 below |
| P6 | Dashboard page, README, demo dry-run | 3h | G6: full demo script executable in <5 min |

**G1:** (a) record counts exactly match §5.4; (b) *clean mode* (`--defects off`) produces a world where a brute-force checker proves every order chains fully — the world reconciles by construction; (c) two runs with seed 42 produce byte-identical files.
**G2:** on seed 42 with defects: zero false matches vs truth; every clean record matched at tier 1 or 2; report renders; runtime < 10 s.
**G3:** `clearing_residual_p == sum(exception.amount_at_risk_p for open, non-informational exceptions)` exactly. If unequal, the pipeline itself must abort with a loud error — this control guards our own bugs.
**G4:** with `MockLLM` scripted to return a *wrong* match for the ambiguous-twins case, the verifier rejects it and the exception stands. The truncated-UTR case resolves. `--llm off` still runs.
**G5:** five canned questions (§9.3) answered with correct numbers and record IDs, verified against the DB by the gate script.
**G6:** demo script beats 1–7 all demonstrable; README quickstart works on a clean machine.

---

## 4. Data model (DDL summary — write full SQL in migrations)

```sql
-- All money BIGINT paise. All ids TEXT.

orders(order_id PK, customer TEXT, amount_p, method TEXT,        -- card|upi|nb|cod
       status TEXT,                                              -- confirmed|cancelled
       created_on DATE)

gw_payments(payment_id PK, order_id NULL, kind TEXT,             -- capture|refund|chargeback|adjustment
       amount_p,               -- signed: refunds/chargebacks negative
       fee_p, gst_p,           -- zero for non-captures
       method TEXT, captured_on DATE,
       settlement_id NULL, utr NULL)                             -- filled when settled

bank_lines(line_id PK, value_date DATE, narration TEXT,
       credit_p, debit_p, utr_extracted NULL)                    -- utr_extracted filled by parser, may be NULL

gl_entries(voucher_no TEXT, line_no INT, entry_date DATE,
       account TEXT, debit_p, credit_p, memo TEXT,
       PRIMARY KEY (voucher_no, line_no))

match_link(link_id PK, hop SMALLINT,                             -- 1|2|3
       src_a TEXT, id_a TEXT, src_b TEXT, id_b TEXT,
       tier SMALLINT,                                            -- 1..4
       confidence NUMERIC(4,3), status TEXT,                     -- proposed|accepted|rejected
       reason TEXT, evidence JSONB, run_id TEXT)

exceptions(exc_id PK, run_id, code TEXT, severity TEXT,          -- info|warn|critical
       hop SMALLINT NULL, records JSONB,                         -- [{src,id},...]
       amount_at_risk_p BIGINT, age_days INT,
       explanation TEXT, suggested_action TEXT, status TEXT)     -- open|resolved

runs(run_id PK, seed INT, started_at, finished_at, llm_mode TEXT,
       metrics JSONB)
```

Required constraints (these encode invariant V2 in the database itself):
```sql
CREATE UNIQUE INDEX one_claim_b ON match_link (src_b, id_b, hop) WHERE status='accepted';
CREATE UNIQUE INDEX one_claim_a ON match_link (src_a, id_a, hop) WHERE status='accepted'
  -- EXCEPT hop 2, where many payments (side a) map to one bank line (side b):
  -- implement hop-2 as payments on side a, bank line on side b; the one_claim_a index
  -- applies to hops 1 and 3 only (use partial index WHERE hop <> 2).
```

Chart of accounts (fixed strings, `config.py`): `BANK`, `PG_RECEIVABLE`, `SALES`, `SALES_RETURNS`, `FEE_EXPENSE`, `INPUT_GST`, `BANK_CHARGES`, `CHARGEBACK_LOSS`, `SUSPENSE`.

---

## 5. Synthetic world generator (P1) — the most important module

### 5.1 Fixed parameters (`config.py`)

```python
SEED = 42
DATE_FROM, DATE_TO = 2026-08-03 (Mon), 2026-08-14 (Fri)   # 10 business days
SETTLEMENT_LAG_BDAYS = 2                                   # T+2
FEE_BPS = {"card": 200, "upi": 0, "nb": 150}               # basis points
GST_BPS_ON_FEE = 1800
AMOUNT_TOL_P = 100                                         # ₹1 hop-2 tolerance
DATE_WINDOW_BDAYS = 3
SUBSET_MAX_ITEMS = 12
METHOD_MIX = {"card": 0.45, "upi": 0.40, "nb": 0.15}
AMOUNT_RANGE_P = (150_00, 25_000_00)                       # ₹150 – ₹25,000
```

Fee math (`moneymath.py`), fixed and unit-tested:
```
fee_p  = round_half_up(amount_p * FEE_BPS[method] / 10_000)
gst_p  = round_half_up(fee_p * 1800 / 10_000)
net_p  = amount_p - fee_p - gst_p          # per capture
settlement_net_p = Σ net_p(captures) + Σ amount_p(refunds, negative)
```

### 5.2 Clean-world construction (`world.py`)

1. 60 orders across the 10 days (4–9/day, seeded), amounts drawn then **rounded to whole rupees** (realism), method from mix. 2 orders are `cod` (no payment expected).
2. One capture per non-COD order, same day.
3. 4 refunds: full-amount refunds against 4 earlier captures, initiated 1–5 days after capture, netted into the settlement batch of their initiation day.
4. Settlement batching: all gateway rows (captures + refunds) of day D form batch `setl_<D>`; paid on `add_bdays(D, 2)`; one bank line per batch, narration `"RAZORPAY SETTLEMENT <setl_id> <utr>"`, UTR = `"UTIB0"+10 seeded digits`.
5. GL, per day D: one capture journal (Dr PG_RECEIVABLE / Cr SALES, totals of D), one journal per refund (Dr SALES_RETURNS / Cr PG_RECEIVABLE); per settlement: Dr BANK + Dr FEE_EXPENSE + Dr INPUT_GST / Cr PG_RECEIVABLE. Amounts derived from the same moneymath — never re-typed.
6. Two non-gateway bank lines: a ₹236.00 `ACCOUNT MAINTENANCE CHARGE` debit (with GL entry Dr BANK_CHARGES) and a direct customer NEFT credit (defect D-07 below repurposes this).
7. Captures on the final 2 business days settle *after* DATE_TO ⇒ legitimately unsettled. **These must end the run as `UNSETTLED_IN_TRANSIT` informational entries, never failures.**

### 5.3 Defect injection (`defects.py`) — one function per defect, applied to the clean world

Each injector mutates the world AND appends the expected outcome to ground truth. Counts are exact.

| ID | Count | Mutation | Expected engine outcome (ground truth) |
|----|-------|----------|----------------------------------------|
| D-01 | 1 | Delete `settlement_id`+`utr` from gateway rows of one mid-size batch (5–7 rows incl. 1 refund) and scrub the UTR/setl_id from the bank narration (`"NEFT CR AXIS BANK SETTLEMENT"`) | Hop-2 resolves via subset-sum, tier 2, evidence shows the reconstruction |
| D-02 | 1 pair | Craft two settlements on the same value_date with **identical net amounts** (adjust one order's amount to force equality), both narrations scrubbed as D-01 | `AMBIGUOUS_SETTLEMENT` (critical). Any engine or LLM that picks one is wrong — truth marks *refusal* as correct |
| D-03 | 2 | Refunds whose GL journal is missing | `UNLINKED_REFUND` (warn) with amount at risk |
| D-04 | 1 | One settlement's GL journal lumps fee+GST into Dr `BANK_CHARGES` (single line) instead of FEE_EXPENSE + INPUT_GST | `GL_DECOMPOSITION_FAIL` (warn), explanation must mention lost input-tax credit |
| D-05 | 1 | Delete one settlement's GL journal entirely | `GL_MISSING` (critical) |
| D-06 | 2 | Gateway captures with no order (`order_id` NULL, memo "payment link") | `ORPHAN_PAYMENT` (warn) |
| D-07 | 1 | Bank credit `"NEFT CR HDFC KALYANI ENTERPRISES"` ₹18,000, no gateway record, no GL | `UNEXPLAINED_BANK_CREDIT` (critical, routed to invoice queue) |
| D-08 | 1 | Duplicate capture: same order paid twice (customer retry), second capture settles normally | `DUPLICATE_PAYMENT` (warn), refund suggested |
| D-09 | 1 | Order marked confirmed, capture exists with `status` failed → exclude from settlement | `ORPHAN_ORDER` (warn) — optimistic status |
| D-10 | 1 | Fee charged at 210 bps instead of 200 on one card capture (recompute batch net accordingly) | `FEE_VARIANCE` (warn) — detect by recomputation, do **not** auto-resolve |
| D-11 | 1 | Chargeback row (negative, references a week-1 order) netted into a late batch | `CHARGEBACK_UNRESOLVED` (critical) |
| D-12 | 1 | Partial capture: order ₹10,000, capture ₹8,000 | `PARTIAL_CAPTURE_MISMATCH` (warn) |
| D-13 | 1 | GL journal posted twice (duplicate voucher with suffix) | `GL_DUPLICATE` (warn) — clearing account goes over-credited |
| D-14 | 2 | Data quality: one bank narration with trailing whitespace + mixed case in UTR; one gateway CSV amount as `"12,000.00"` string | Loader normalises silently; truth expects **no** exception (tests the normaliser) |

Totals after injection: ~62 orders, ~64 gateway rows, ~13 bank lines, ~90 GL lines ⇒ >200 records, comfortably beyond "50+".

### 5.4 Outputs

`data/orders.csv`, `data/gateway.csv`, `data/bank.csv`, `data/gl.csv`, `data/ground_truth.json`:

```json
{ "seed": 42,
  "links": [ {"hop":1,"a":["orders","ORD-1001"],"b":["gw","pay_9x1a"]}, ... ],
  "exceptions": [ {"code":"AMBIGUOUS_SETTLEMENT","records":[...],"amount_at_risk_p":123400,
                   "note":"engine must refuse; selecting either is a false match"}, ... ],
  "in_transit": [ {"batch":"setl_0813","expected_settlement":"2026-08-17"}, ... ] }
```

---

## 6. Matching engine (P2–P3)

Run order: load → normalise → hop 1 → hop 2 → hop 3 → verifier finalisation → tier 4 on residue (if `--llm on`) → verifier again → score → report. Each hop writes `match_link` rows as `proposed`; **only the verifier flips rows to `accepted`.** No other code path may set `accepted`.

### 6.1 Normalisation (loader)
- Strip currency symbols/commas from amounts; parse to paise; reject floats that don't round exactly to paise.
- Trim/uppercase reference fields; extract UTR from narration by regex `[A-Z]{4}0?[A-Z0-9]{6,18}` into `bank_lines.utr_extracted` (NULL if no plausible token).
- Dates to ISO. Any row that fails normalisation → `DATA_QUALITY` exception, row quarantined, pipeline continues.

### 6.2 Hop 1 — orders ↔ gateway captures (tier 1 only)
Join on `order_id` where `kind='capture'`.
- 1:1 and `|order.amount_p − capture.amount_p| == 0` → accept, confidence 1.0.
- Amount differs → link `proposed` + `PARTIAL_CAPTURE_MISMATCH` (accept the link, flag the delta — the association is certain, the amount isn't).
- Order `cod` → skip silently. Order confirmed with no capture (or failed capture) → `ORPHAN_ORDER`. Capture with NULL/unknown order → `ORPHAN_PAYMENT`. Two captures, one order → link the first by time, `DUPLICATE_PAYMENT` on the second.

### 6.3 Hop 2 — gateway batches ↔ bank lines
Side a = the set of gateway rows in a batch; side b = one bank line. Evidence JSON must always contain the full reconstruction table (per-row net, subtotals, target, delta).

Tier 1: `settlement_id` known and `utr == bank.utr_extracted` → verify `Σ net == credit_p` within `AMOUNT_TOL_P`; accept. Amount off by more than tolerance → `FEE_VARIANCE`: recompute each row's fee from the rate card; if exactly one row explains the delta at a different bps, name it in the evidence; link stays `proposed`, exception raised (per D-10 we detect, not resolve).

Tier 2 (UTR unusable): candidate pool = unsettled gateway rows with `captured_on` within `DATE_WINDOW_BDAYS` business days before `value_date`, capped at `SUBSET_MAX_ITEMS`; refunds/chargebacks in-window included as negatives. Solve subset-sum to `credit_p ± AMOUNT_TOL_P`:

```
def reconstruct(target_p, items, tol_p, max_items=12):
    # items: [(row_id, net_p)] sorted desc by |net_p|
    # DFS with pruning: prefix sums bound remaining reachable range
    # MUST enumerate until TWO solutions found or space exhausted
    # returns: NoSolution | Unique(subset) | Multiple
```
- `Unique` → propose tier-2 link, confidence 0.98, evidence = the subset with arithmetic.
- `Multiple` → `AMBIGUOUS_SETTLEMENT` (critical), suggested action: "confirm settlement ID in gateway dashboard". **Do not pick. Not even by earliest date. This refusal is demo beat 5.**
- `NoSolution` → leave for tier 4; if still unresolved → `UNEXPLAINED_BANK_CREDIT` (credits) or `MISSING_IN_BANK` (batches with no line by `expected + DATE_WINDOW`).
- Batches whose expected settlement date > DATE_TO → `UNSETTLED_IN_TRANSIT` (**info**, excluded from failure metrics and from the residual control's exception sum only if you also exclude them from the clearing residual — see V5; simplest correct choice: include on both sides).

### 6.4 Hop 3 — bank lines ↔ GL journals
For each accepted settlement line, find journal(s) with a `BANK` debit equal to `credit_p` within tolerance in a ±3-bday window.
- Found: check decomposition — journal must contain `FEE_EXPENSE == Σfee_p` and `INPUT_GST == Σgst_p` and `PG_RECEIVABLE credit == Σ(amount_p) + Σ(refund amounts)` for the batch. Any component wrong/lumped → `GL_DECOMPOSITION_FAIL`, evidence lists expected vs found per account.
- No journal → `GL_MISSING`. Two journals → link one, `GL_DUPLICATE` on the other.
- Refund GL check: every refund row needs a `SALES_RETURNS/PG_RECEIVABLE` journal; missing → `UNLINKED_REFUND`.

### 6.5 Verifier (`verifier.py`) — the only authority
- **V1** per proposed link: amounts reconstruct within tolerance (re-run the arithmetic; do not trust the proposer).
- **V2** no double claim (DB partial unique indexes; catch `UniqueViolation` → reject the later proposal, raise `DUPLICATE_CLAIM`).
- **V3** every GL voucher internally balances (Σdebit == Σcredit) — run at load; imbalance → `DATA_QUALITY` critical.
- **V4** hop-3 decomposition as §6.4.
- **V5 — the clearing-account control (demo beat 6).** Independently compute `residual_p = Σ PG_RECEIVABLE debits − Σ credits` from `gl_entries` alone. Separately compute `exposure_p` = Σ amount_at_risk over open non-info exceptions that affect the receivable (maintain an explicit per-code inclusion map; document it in code comments). **If `residual_p != exposure_p`, abort the run with both numbers printed.** This control exists to catch OUR bugs; treat a failure as a stop-ship defect, never special-case it away.
- **V6** every tier-4 (LLM) proposal must pass V1+V2 like any other; rejection demotes to the original exception with `llm_rejected=true` noted in evidence.

---

## 7. Scoring (`scorer.py`) — runs only when ground truth exists

```
link_precision   = |accepted ∩ truth.links| / |accepted|
link_recall      = |accepted ∩ truth.links| / |truth.links|
false_match_rate = 1 − link_precision                  # headline number
full_chain_rate  = orders fully chained (h1,h2,h3 accepted) / orders truth says should chain
exc_detection    = truth exceptions raised with correct code / |truth.exceptions|
exc_code_accuracy= of raised-and-expected, fraction with exactly-correct code
tier_histogram, llm_calls, llm_accept/reject, runtime_s, records_processed
₹ metrics        = value reconciled vs value in exceptions (paise, print as ₹)
```
Special rule: for D-02 the *refusal* is the correct answer — an accepted link on those records counts as a false match; the `AMBIGUOUS_SETTLEMENT` exception counts toward detection.

### Run report (terminal, JSON, and one HTML page)
```
RECON-4 RUN 2026-08-2x · seed 42 · llm=on
Records: 204   Runtime: 3.8s   LLM calls: 3 (accepted 1, rejected 1, abstained 1)
Hop match:  H1 58/60  H2 11/13  H3 9/11     Full chain: 51/56 (91.1%)
Link precision 100.0% · recall 96.4% · FALSE-MATCH RATE 0.0%
Exceptions: 14 open (3 critical / 9 warn / 2 info) · ₹41,236.00 at risk
Clearing control: GL residual ₹41,236.00 == exception exposure ₹41,236.00 ✓
Top exceptions by ₹ at risk:
  E-003 AMBIGUOUS_SETTLEMENT  ₹23,417.50  crit  "Two identical candidates …" → check dashboard
  ...
```
(Numbers above are illustrative shape, not targets — real values come from the run.)

---

## 8. Tier-4 adjudicator (P4)

Interface first, model last: `LLMClient.adjudicate(payload) -> Adjudication`, with `MockLLM` (canned responses per test case, including a deliberately-wrong one) and `RealLLM` (Anthropic API, temperature 0, response parsed into the Pydantic schema; one retry on schema violation, then treated as abstention).

Input payload (built by us; the model never queries data):
```json
{ "task": "hop2_unresolved_bank_credit",
  "item": {"line_id": "...", "value_date": "...", "credit_p": 0, "narration": "..."},
  "candidates": [ {"batch": "...", "rows": 6, "net_p": 0, "delta_p": 0,
                   "date_gap_bdays": 1, "narration_tokens_matched": ["AXIS"]} ],
  "failed_checks": ["no unique subset within tolerance"],
  "instruction": "Choose a candidate only if evidence is decisive. Abstaining is a correct and rewarded outcome." }
```
Output schema (Pydantic, `extra="forbid"`):
```python
class Adjudication(BaseModel):
    decision: Literal["match","no_match","insufficient_evidence"]
    candidate: str | None
    reason_code: ExceptionCode | None
    explanation: str            # ≤ 2 sentences, plain language
    confidence: float           # 0..1
```
Hard rules: candidates capped at 5; the numeric deltas are computed by us and given to the model — it never does arithmetic; `decision=="match"` only creates a `proposed` link that must survive V1+V2+V5; every call logged to `runs.metrics` for the report line "LLM proposed N, verifier accepted A, rejected R".

Second LLM duty (cheap, safe): render `explanation` and `suggested_action` strings for every exception from a structured evidence dict. `--llm off` fallback: template strings.

---

## 9. Q&A agent (P5)

### 9.1 Tools (FastAPI internal functions; SQL inside; LLM sees JSON only)
```
trace_order(order_id)      -> {order, capture, settlement:{batch,utr,bank_line}, gl:{vouchers}, hops:{h1,h2,h3 status}, exceptions:[...]}
explain_settlement(ref)    -> ref = utr | settlement_id | line_id: constituent rows, fee/gst subtotals, reconstruction table, gl voucher
list_exceptions(hop?,code?,min_amount_p?) -> ordered by amount_at_risk desc
cash_position(as_of)       -> {cleared_p, in_transit:[{batch, expected_date, net_p}], disputed_p, unreconciled_p}
```
`cash_position.unreconciled_p` must equal the V5 exposure — same number, third surface.

### 9.2 Loop
System prompt (verbatim intent): *"Answer only from tool results. Quote record IDs for every figure. If tools return nothing, say the data does not show it. Never estimate, never compute arithmetic yourself — call a tool."* Standard tool-use loop, max 4 tool calls per question, temperature 0.

### 9.3 Gate questions (G5)
1. "Why is the credit on 12 Aug ₹2,000 short?" → names the netted refund + IDs.
2. "Trace ORD-1017 end to end." → full chain with voucher number.
3. "What can't you reconcile and what's it worth?" → exception list + total == V5 number.
4. "How much cash lands next Monday?" → in-transit batches with dates.
5. "Which two settlements can't you tell apart and why?" → the D-02 pair, refusal explained.

---

## 10. Phase 6 — dashboard, README, demo

- One static page (plain HTML + fetch, no framework): metrics header, exception table sorted by ₹ at risk with expandable evidence, chat box hitting the Q&A endpoint. Cap effort at 3h — **a plain table with correct numbers beats a pretty chart with soft ones.**
- README: quickstart (`docker compose up -d && make demo`), the seven beats as a demo script with exact commands, the non-goals list from §1 verbatim, and a short "what synthetic data proves and doesn't" paragraph (beat 1).
- Record a 2-min screen capture as backup against live-demo failure.

## 11. Test list (minimum)

- T-1 determinism: two `generate --seed 42` runs → identical SHA256 per file; two full runs → identical report JSON (minus timestamps).
- T-2 moneymath: fee/gst rounding table-driven cases incl. paisa edge cases.
- T-3 subset-sum: unique / multiple / none / >12-items-capped fixtures; multiple ⇒ refusal.
- T-4 clean world fully chains, zero exceptions except in-transit info.
- T-5 defect world: scorer shows false-match 0, each D-xx produces its expected code (parametrised over §5.3 table).
- T-6 verifier adversarial: MockLLM returns wrong match ⇒ rejected ⇒ exception preserved.
- T-7 V5 control: corrupt one GL amount in a fixture ⇒ pipeline aborts loudly.
- T-8 loader: D-14 fixtures normalise with no exception.

## 12. Order of implementation inside P2 (for the implementing model)

1. `moneymath.py` + `busdays.py` + unit tests (T-2).
2. Loader + normaliser (T-8).
3. Hop 1 complete with exceptions.
4. Hop 2 tier 1; then `subsetsum.py` standalone with T-3; then wire tier 2.
5. `verifier.py` V1–V3; flip-to-accepted logic; DB indexes.
6. `scorer.py` + report. Run G2. Only then touch P3.

*Spec ends. If a contradiction is found between sections, §1 beats everything, and refusal beats guessing.*
