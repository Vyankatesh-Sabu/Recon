# ROADMAP.md — engineering handoff. Execute top to bottom.

Written 2026-09-03 for a fresh session with no memory of how this repo got
here. Everything you need is in the repo. **Read, in this order, before
touching anything:** `CLAUDE.md` → `SPEC.md` → `UI_SPEC.md` → this file.
Then read `SUBMISSION.md` §2 (the narrative) once, so you know *why* the
steps are in this order. Nothing here overrides CLAUDE.md.

---

## 0. How to work in this repo

**Run things with** `.venv/bin/python` (never bare `python`). Backend:
`.venv/bin/python -m recon.cli serve` → http://127.0.0.1:8000. Frontend:
`cd web-app && npm run dev` → http://localhost:5173 (Vite proxies `/api`
and `/report` to :8000). Fresh data: `rm -rf data && make generate && make load`.
Seed 42 is *the* demo seed; its reference values are in §5.

**Before every commit, all of these must pass:**
```
.venv/bin/python -m pytest -q tests/unit                      # 130+ pass
for p in 1 2 3 4 5 6; do .venv/bin/python tests/gates/gate_p$p.py; done
cd web-app && npm run build && cd ..                           # clean tsc + vite
```
**Every UI step additionally requires** a real-browser check against a real
seeded run with the LLM off (UI_SPEC §4's gate). Use the browser tools if
you have them (`claude-in-chrome`: navigate, screenshot, `get_page_text`,
`read_console_messages`); if not, drive the API with curl and the UI with
`npm run build` + a manual check by the user. **No mock/fixture data in
`web-app/src`, ever.** The frontend never computes a number — if a screen
needs a figure the API doesn't return, add it to the API.

**Backend changes are additive only:** new columns (via a new migration
file), new fields, new endpoints. Never change what an existing field
means. Every new exception code gets a line in `verifier.py`'s
`V5_INCLUSION_MAP` comment block saying why it's included or excluded.

**Git:** you're on branch `p6-api-layer`. Commit per step with the title
given. **Ask the user before any `git push`** (a push was denied earlier).
Never rewrite history. Commit trailer per your session's attribution.

**Don't:** put an LLM inside hop1/hop2/hop3/verifier; weaken or skip a
gate; touch `check_v5_clearing_control`; add a dependency without amending
CLAUDE.md rule 8 first; spend more than the stated timebox on any step
marked timeboxed; build UI_SPEC §2.9.

---

## 1. Current state (2026-09-03)

| Area | Status |
|---|---|
| P0–P5: generator (14 defects), loader, hop1/2/3, verifier V1–V5, scorer, tier-4 adjudicator, Q&A tools, `/ask` | done, gated |
| P6: `/api/run` (bg thread) · `/api/run/{id}/stream` (SSE) · `/metrics` · `/exceptions` · `/api/match/{link_id}` · `/api/order/{id}/chain` · `/api/control/clearing` · `/api/ask` · `--pace` | done, gated (gate_p6) |
| P7: `web-app/` Vite+React+TS+Tailwind, tokens, `LedgerTable/Money/SeverityRule/TierBadge/LedgerPanel`, routes `/` `/exceptions` `/clearing` `/chain` `/qa` | done |
| P8: Run console (§2.1) w/ live SSE four-column canvas + gutter + counters; metrics band (§2.2) | done, browser-verified |
| `tests/eval_multi_seed.py` | done — 344/500 worlds, 0.0% false-match on all, 0 aborts |
| `.env` + `python-dotenv` | done; user's `.env` has `GEMINI_API_KEY` + `RECON_LLM_PROVIDER=gemini`; no Anthropic key |
| P9 §2.3 §2.4 §2.5 §2.6 | **stubs only** (`Exceptions.tsx`, `ClearingControl.tsx` render raw data) |
| P10 §2.7 §2.8, README, video | **not started**; README still describes the deleted-in-spirit old dashboard |

**Git:** `p6-api-layer` is 6 commits ahead of `main` (`main` = old P6, tag
`v1.0`). Pushed through `edbd212`; the last four commits are local only.
**One uncommitted change:** `recon/llm/providers/gemini_llm.py` wraps
non-dict tool results in `{"result": ...}` for Gemini's `FunctionResponse`
— a real fix; commit it in Step 0. `PLAN.md` no longer exists (superseded
by this file + `SUBMISSION.md`).

**LLM reality:** Gemini single-shot (`adjudicate`, `explain`) works live —
model `gemini-3.6-flash`. Gemini multi-turn (`converse`, used only by Q&A)
fails on turn 2 (see §4). `AnthropicLLM` has never been run live.

---

## 2. Steps

### Step 0 — safety net (30 min)
1. `git add recon/llm/providers/gemini_llm.py && git commit` —
   title: `Gemini: wrap non-dict tool results for FunctionResponse`.
2. `git checkout main && git merge --ff-only p6-api-layer`. If ff fails,
   `git merge p6-api-layer` (no rebase).
3. In a temp dir: `git clone <repo> /tmp/recon-check && cd /tmp/recon-check && make demo` — must exit 0.
4. Ask the user; then `git push origin main`. Then `git checkout -b p9-screens`.

**Accept:** `main` is a working, honest submission before any new work starts.

---

### Step 1 — backend additions every P9 screen needs (2h, one commit)
Do all of these together, with tests, so the screens can be built purely
against real endpoints.

**1a. Persist refusal evidence.** `db/migrations/003_exceptions_evidence.sql`:
`ALTER TABLE exceptions ADD COLUMN evidence TEXT;` (SQLite supports it;
`recon.db.migrate` picks it up by filename order). In `hop2.py`, give
`add_exception(...)` an optional `evidence: dict | None = None` parameter
that stores `json.dumps(evidence)` in the new column, and pass the
already-built `evidence` dict for `AMBIGUOUS_SETTLEMENT` (both the
`Multiple` branch and the cross-collision branch) and
`UNEXPLAINED_BANK_CREDIT`. Add to that dict, before storing:
`"bank_line": line_id, "value_date": value_date.isoformat(), "narration": narration`.
In `api.py`, `get_run_exceptions` and `get_report` select the new column
and use `json.loads(stored)` when present, else fall back to
`_reconstruction_evidence(...)`. Also make `_reconstruction_evidence`
return the `link_id` it found, exposed on each exception row as
`evidence_link_id` (null for refusals) — the exception queue needs it to
open the viewer.

**1b. Reconstruction detail.** Extend `GET /api/match/{link_id}` (additive)
— for `hop == 2` links add:
- `bank_line: {line_id, value_date, credit_p, narration, utr_extracted}` from `bank_lines`;
- `rows: [{payment_id, kind, method, amount_p, fee_p, gst_p, net_p}]` for every
  `match_link` row in this run with the same `id_b`, `hop = 2`, status in
  (`accepted`,`proposed`), joined to `gw_payments`; `net_p` computed
  server-side exactly as `hop2._contribution_p` (capture → `moneymath.net_p`,
  else raw signed `amount_p`);
- `reconstructed_p` (Σ `net_p`) and `delta_p` (`reconstructed_p - credit_p`).

**1c. Clearing T-account.** Extend `GET /api/control/clearing` (additive)
with `difference_p` (`residual_p - exposure_p`) and
`entries: [{voucher_no, entry_date, account, debit_p, credit_p, memo, balance_p}]`
— every `gl_entries` row with `account = 'PG_RECEIVABLE'`, ordered by
`entry_date, voucher_no, line_no`, `balance_p` a server-computed running
`Σ(debit_p - credit_p)`. Its final value must equal `residual_p`; assert
that in the endpoint (it's the same table — a mismatch is a bug).

**1d. Run request options.** `RunRequest` gains `narrate: bool = True`;
thread a `narrate: bool = True` parameter into `pipeline.run_pipeline` and
skip `adjudicator.narrate_exceptions` when false (`narrated = 0`). Add
`--no-narrate` to `recon.cli run`. (The 17 narration calls are what make an
LLM-on run take ~2 min; adjudication alone is ~15 s.)

**1e. `rejected` SSE event.** `verifier.reject_v1` gains a `hop: int`
parameter (all three call sites have it in scope) and, if `on_event`, emits
`{"kind": "rejected", "hop", "link_id", "tier", "reason"}`. Update the
`events.py` docstring, `web-app/src/lib/api.ts`'s `RunEvent` union, and the
two assertions that say `kinds <= {"match", "exception"}`
(`tests/unit/test_api.py`, `tests/gates/gate_p6.py`) to include `"rejected"`.

**1f. Latest run.** `GET /api/run/latest` → same body as
`/api/run/{id}/metrics` for `recon_db.latest_run_id`, 404 if none.

**1g. Verifier outcome on the call log.** `adjudicator.finalize_tier4_stats`
also sets `call_log[i]["verifier_outcome"] = status` (+ `reason`) for any
call whose `line_id` has a tier-4 `match_link`; leave it absent when the
model abstained. Add `llm_call_log` to `PipelineMetrics` in `api.ts`.

**Tests:** extend `tests/unit/test_api.py` — stored evidence present on
seed 42's two `AMBIGUOUS_SETTLEMENT` rows and on `UNEXPLAINED_BANK_CREDIT`;
`/api/match/{tier2 link}` returns 5 rows summing to `reconstructed_p` with
`delta_p == 0`; `/api/control/clearing` last `balance_p == residual_p ==
13_640_000` and `difference_p == 0`; `/api/run/latest` works and 404s on an
empty DB.
**Commit:** `P9 backend: refusal evidence, reconstruction detail, T-account, narrate flag, rejected events`.

---

### Step 2 — Refusal card (UI_SPEC §2.5) (1.5h)
In the exception queue (`Exceptions.tsx`, or a new `RefusalCard.tsx`),
every `AMBIGUOUS_SETTLEMENT` exception is pulled out of the table and
rendered as §2.5's mirrored pair. Pair them by `(evidence.value_date,
amount_at_risk_p)`. Per side: line id · value date · net (`Money`) ·
"candidate subsets: N (sizes a, b)" from the stored evidence · utr `—`.
Beneath, verbatim: *"These are indistinguishable from the available data.
No match proposed. Resolve by confirming the settlement ID in the gateway
dashboard."* No control on this card offers a way to pick one.
**Accept:** seed 42 shows exactly one card — `bl_d02_0810a` / `bl_d02_0810b`,
both ₹50,738.58, both 2026-08-12. Browser-verified, LLM off.
**Commit:** `P9: refusal card (§2.5)`.

### Step 3 — Reconstruction viewer (UI_SPEC §2.3) (2h)
`ReconstructionViewer.tsx`, a panel opened by (a) clicking any T2-badged
row in the run console — store the hop-2 `link_id` from the SSE match
event on `ChainRow` (`link2Id`) in `runChains.ts`; (b) the exception
queue's `evidence_link_id`. Fetch `GET /api/match/{link_id}`. Render §2.3's
mock exactly: header line (bank line id · value_date · credit · narration;
second line "no UTR recovered · no settlement id" when both are null),
then one row per `rows[]` (id, method, amount, −fee, −gst, net), a rule,
then reconstructed / bank line / delta, delta turning `--verified` at 0.
Rows appear one per ~180 ms with the running subtotal — implement with
React state + `setTimeout` + CSS transitions, **not framer-motion**
(CLAUDE.md rule 8 approves it for the run console only). Honour
`prefers-reduced-motion` (render all at once). All figures come from the
API (`net_p`, `reconstructed_p`, `delta_p`) — no summing in the browser.
**Accept:** seed 42's tier-2 batch `setl_0812` (PAY-0043/44/45/46 + refund
PAY-0059) reconstructs to delta ₹0.00 on screen. Browser-verified.
**Commit:** `P9: reconstruction viewer (§2.3)`.

### Step 4 — Clearing account control (UI_SPEC §2.6) (1h)
Rewrite `ClearingControl.tsx`: a ledger-ruled T-account of `entries[]`
(date · voucher · debit · credit · balance), then the three-line block
(GL residual from journal entries alone / exception exposure from the queue
alone / difference ✓). Difference comes from `difference_p` — delete the
`data.residual_p - data.exposure_p` expression (UI_SPEC §0 violation). Keep
the per-code breakdown beneath.
**Accept:** last balance = ₹1,36,400.00 = both control numbers; difference
₹0.00 in `--verified`. Browser-verified.
**Commit:** `P9: clearing account control (§2.6)`.

### Step 5 — Exception queue (UI_SPEC §2.4) (1.5h)
Filters: hop, severity, code (`/api/run/{id}/exceptions` already takes all
three), plus a "critical only" toggle. Columns: code (severity left edge) ·
records · ₹ at risk · age · suggested action. Row expands to evidence
**rendered as a table**: for rows with `evidence_link_id`, embed the
reconstruction viewer; for GL exceptions, an expected-vs-found table from
the match_link evidence; for refusals, the stored candidate subsets. Use
`/api/run/latest` for the run id.
**Accept:** 17 rows on seed 42, filters work, every expand shows a table,
never raw JSON. Browser-verified.
**Commit:** `P9: exception queue (§2.4)`.

---

### Step 6 — Run console: controls, rejections, adjudication log (1.5h)
- Controls beside "Start run": pace (`0 / 150 / 400 ms`), LLM (`off / on`),
  narrate (`on / off`, only enabled when LLM is on). Pass to `startRun`.
- Gutter renders `rejected` events as `--flag`-edged chips: `REJECTED · T{tier} · {reason}`.
- After completion, if `metrics.llm_call_log` is non-empty, render an
  **Adjudication** panel under the metrics band: one row per call —
  bank line · candidates offered · model decision · verifier outcome
  (`—` when abstained). Figures in mono, prose in sans.
**Accept:** LLM off run identical to before; LLM on (`narrate` off) run
with Gemini completes in ~20 s and shows three abstentions in the panel.
**Commit:** `P8+: run console controls, rejected events, adjudication log`.

---

### Step 7 — Make tier-4 proposals real, and prove the verifier catches them (2h)
Today a tier-4 "match" can never reach the verifier: the ambiguous twins are
overridden to abstention before proposal (correct — keep it), and for
`tier2_unexplained_credit` `_build_payload` offers **zero candidates**, so a
"match" resolves to nothing. Gate G4 passes because nothing is proposed.
Make the path real so "the verifier catches the model" is a true sentence:

**7a. Candidates for unexplained credits** (`adjudicator.py`). In
`run_tier4`, build the candidate pool for a `tier2_unexplained_credit`
entry from **unclaimed** tier-2-eligible rows in the DB (not the ±3-bday
window — that pool was empty, that's why the line is unexplained):
`gw_payments WHERE utr IS NULL`, not in-transit
(`busdays.add_bdays(captured_on, SETTLEMENT_LAG_BDAYS) <= DATE_TO`), and
`payment_id NOT IN (SELECT id_a FROM match_link WHERE run_id=? AND hop=2 AND status='accepted')`.
Each row is one candidate labelled `row:<payment_id>` with `net_p`,
`delta_p = net_p - credit_p`, **real** `date_gap_bdays =
busdays.bday_diff(captured_on, value_date)`, **real**
`narration_tokens_matched` = uppercase alphanumeric tokens of the bank
narration ∩ tokens of `{payment_id, order_id, settlement_id, utr}`. Keep
the `MAX_CANDIDATES = 5` nearest by `|delta_p|`. Keep a
`label → [{"id", "net_p"}]` map on the entry so `_resolve_candidate_rows`
can resolve `row:*` labels; keep `candidate_a/b` resolution for the
ambiguous case. Compute the two real fields for the ambiguous candidates
too. Delete both hardcoded placeholders.
**7b. Test** (`tests/unit/test_adjudicator.py`, new or extend): a tiny
`WrongLLM` (implements `adjudicate` returning `match` on
`candidates[0]["batch"]` with confidence 0.95 whenever candidates exist,
else `insufficient_evidence`; `explain` returns the template) on seed 42 →
exactly one tier-4 `match_link` (on `bl_direct_neft`), `status='rejected'`,
`reason` starting `V1_failed (tier4/llm proposal rejected)`; the twins never
proposed; `AMBIGUOUS_SETTLEMENT` ×2 and `UNEXPLAINED_BANK_CREDIT` still
open; `false_match_rate == 0.0`; V5 ties. Gate G4 must still pass unchanged.
**7c. `demo/llm_wrong_match.py`.** Runs the full pipeline on seed 42 with
that `WrongLLM`, then prints, per tier-4 call: the line, the candidates
offered with their deltas, the model's decision, and what happened —
`overridden: ambiguous refusal is absolute` for the twins, `proposed →
verifier REJECTED: <reason>` for the unexplained credit — then the surviving
open exceptions, `false_match_rate`, and the V5 tie. This is a real run of
real code with a deliberately wrong model stand-in, shown in a terminal
(video beat 6b in `SUBMISSION.md`); it is not mock data in the frontend.
**Commit:** `Tier 4: real candidates for unexplained credits; verifier-rejection demo`.

---

### Step 8 — runnable from a clean clone, and the README (1.5h)
- `requirements.txt`: add `uvicorn` pinned (check `.venv/bin/pip show uvicorn`
  for the installed version). Amend CLAUDE.md rule 8 with one line. `make
  serve` must work on a clean clone without a manual pip install.
- `Makefile`: `ui` → `cd web-app && npm install && npm run dev`;
  `eval` → `$(PY) tests/eval_multi_seed.py --start 1 --count 500`;
  `demo-llm-wrong` → `$(PY) demo/llm_wrong_match.py`. Add to `.PHONY`.
- Rewrite `README.md` to the section spec in `SUBMISSION.md` §6. Every
  number in it must be one you have just regenerated from `recon.cli report`
  / `make eval`, not copied from this file.
**Accept:** fresh clone → `make demo`, `make serve`, `make ui`, `make eval` all work.
**Commit:** `README for the new UI; make ui/eval targets; uvicorn as a dependency`.

---

### Step 9 — Q&A (UI_SPEC §2.8). **Timeboxed: 90 min on 9a, hard stop.**
**9a. Gemini multi-turn.** Failure (verbatim, from a live call):
`400 INVALID_ARGUMENT ... Function call is missing a thought_signature in
functionCall parts ... function call default_api:list_exceptions, position 2`.
Gemini requires the `thought_signature` it attached to its own function-call
part to be echoed back when that model turn is replayed. Fix in
`gemini_llm.py::converse()`: when reading `response.candidates[0].content.parts`,
keep the raw parts — return `"raw_parts": [p.model_dump() for p in parts]`
alongside `tool_calls`; in `qa.py`, copy any `raw_parts` onto the assistant
message; when rebuilding the model turn, if `raw_parts` is present replay
them verbatim (`types.Part.model_validate(...)`) instead of
`Part.from_function_call`. Confirm the field shape against
https://ai.google.dev/gemini-api/docs/thought-signatures before writing.
**Verify** with the exact question that failed: *"Which two settlements
can't you tell apart, and why?"* → prose that cites `bl_d02_0810a` and
`bl_d02_0810b`. **At 90 min without that, stop**; Q&A ships in its honest
"could not reach the LLM" state and is cut from the video (the bar never
mentions Q&A).
**9b. Console** (1h, only if 9a passed): `qa.answer_question` also returns
`tool_results: [{name, summary}]` (server-side one-liner per call, e.g.
"17 open exceptions, ₹3,97,605.76 at risk"). `AskConsole.tsx` renders
§2.8's log: `you` / `⟶ tool(args)` / `⟵ summary` / `claude`, IDs and
figures in mono, prose in sans, record IDs under the answer.
**Commit:** `Q&A: Gemini thought_signature replay; console (§2.8)`.

### Step 10 — only if everything above is done and the video is cut
- Chain explorer (§2.7): horizontal four-hop strip from `/api/order/{id}/chain`,
  per-hop status/tier, inline exception on broken hops. Show `ORD-1017`
  (intact) beside `ORD-1058` (orphan).
- `GET /api/eval?count=100` running `eval_multi_seed` in-process + a small
  Robustness panel with the table.

---

### Step 11 — cleanup and ship (1h)
1. Delete `web/index.html`, the `/dashboard` `StaticFiles` mount in
   `api.py`, and `test_dashboard_is_served_at_slash_dashboard`. Keep
   `/ask` and `/report`. Fix CLAUDE.md rule 8's frontend note: `web/` →
   `web-app/`, old dashboard removed.
2. Full check (§0) + `make eval` + browser pass over every route.
3. Merge to `main`. Fresh clone → `make demo`, `make ui`, all gates.
4. `git tag v2.0`. Ask the user; push `main` and tags. `v1.0` stays as the
   fallback.
5. `git check-ignore .env` → ignored. `git ls-files | grep -c '^\.env$'` → 0.

---

## 3. Cut lines (by hours available when you start)
- **< 8 h:** Steps 0, 1 (a, b, e only), 2, 3, 6 (controls only), 8, 11. Then video.
- **8–14 h:** everything except Steps 9b and 10.
- **> 14 h:** everything, in order.
Whatever you cut, the video (`SUBMISSION.md` §5) is not cut.

## 4. Known issues reference
| Issue | Where | Handled in |
|---|---|---|
| Gemini turn-2 `thought_signature` 400 | `gemini_llm.py::converse` | Step 9a |
| Tier-4 payload fields hardcoded `0` / `[]` | `adjudicator.py::_build_payload` | Step 7a |
| No tier-4 proposal can reach the verifier | `adjudicator.py` (no candidates for unexplained) | Step 7a |
| Browser subtracts residual − exposure | `ClearingControl.tsx` | Step 4 |
| Run console hardcodes LLM off, pace 0 | `RunConsole.tsx::handleStart` | Step 6 |
| `uvicorn` not a declared dependency | `requirements.txt` | Step 8 |
| README describes old dashboard | `README.md` | Step 8 |
| CLAUDE.md says `web/`, code is `web-app/` | `CLAUDE.md` rule 8 | Step 11 |
| Old plain-HTML dashboard still mounted | `web/index.html`, `api.py` | Step 11 |
| `AnthropicLLM` never run live | — | Say so in README; don't claim otherwise |
| ~27% of seeds can't generate (D-01 needs a 5–7 row batch with exactly one refund) | `generator/defects.py` | Documented. Not a bug. Leave it. |

## 5. Seed-42 reference values (LLM off) — verify screens against these
198 records · precision 100% · recall 99.1% · **false-match 0.0%** ·
full chain **32/57 = 56.1%** · hop match H1 57/57, H2 47/47, H3 6/6 ·
17 open (5 critical / 10 warn / 2 info) · at risk ₹3,97,605.76 ·
reconciled ₹4,51,064.46 · **residual_p = exposure_p = 13,640,000p (₹1,36,400.00)**.
Refusals: `bl_d02_0810a`, `bl_d02_0810b` (₹50,738.58 each, 2026-08-12).
Unexplained: `bl_direct_neft` (₹18,000.00, 2026-08-04, "NEFT CR HDFC KALYANI ENTERPRISES").
Tier-2 batch: `setl_0812` ← PAY-0043, 0044, 0045, 0046 + refund PAY-0059.
LLM on (Gemini): 3 calls → `insufficient_evidence`, `insufficient_evidence`, `no_match`; 17 narrated.
Multi-seed: `--count 500` → 344 complete, 0.0% false-match on every one, 0 aborts, ~5 s.

## 6. Definition of done
`main` at `v2.0`: fresh clone passes `make demo`, `make serve`, `make ui`,
`make eval`, all gates, all unit tests; every route renders from real data
with the LLM off; `demo/llm_wrong_match.py` shows one proposal rejected by
V1 and the twins never proposed; README matches the shipped UI and leads
with the 344-world table; `.env` untracked; video recorded per
`SUBMISSION.md` §5 with every on-screen number traceable to an API response.
