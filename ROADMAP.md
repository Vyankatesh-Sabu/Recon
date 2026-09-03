# ROADMAP.md — execution record. All engineering steps complete.

Written 2026-09-03 as a handoff to be executed top to bottom; rewritten
the same day, after execution, as the record of what was actually built.
**Everything in §2 is done, on `main`, tagged `v2.0` and pushed.** The only
remaining work is the video (`SUBMISSION.md` §5).

Read order for a fresh session: `CLAUDE.md` → `SPEC.md` → `UI_SPEC.md` →
this file → `SUBMISSION.md` §2. Nothing here overrides CLAUDE.md.

---

## 0. How to work in this repo

**Run things with** `.venv/bin/python` (never bare `python`). Backend:
`make serve` → http://127.0.0.1:8000. Frontend: `make ui` →
http://localhost:5173 (Vite proxies `/api` and `/report` to :8000). Fresh
data: `rm -rf data && make generate && make load`. Seed 42 is *the* demo
seed; its reference values are in §4.

**Before every commit, all of these must pass:**
```
.venv/bin/python -m pytest -q tests/unit                      # 140 pass
for p in 1 2 3 4 5 6; do .venv/bin/python tests/gates/gate_p$p.py; done
cd web-app && npm run build && cd ..                           # clean tsc + vite
```
**Every UI change additionally requires** a real-browser check against a
real seeded run with the LLM off (UI_SPEC §4's gate). **No mock/fixture
data in `web-app/src`, ever.** The frontend never computes a number — if a
screen needs a figure the API doesn't return, add it to the API.

**Backend changes are additive only:** new columns (via a new migration
file), new fields, new endpoints. Never change what an existing field
means. Every new exception code gets a line in `verifier.py`'s
`V5_INCLUSION_MAP` comment block saying why it's included or excluded.

**Git:** pushed. `v2.0` tags `fd85bd6`; `main` is at `375efa5`, two commits
later — the merge described under Step 11, which changed no files, so the
two trees are byte-identical (`f1f7857`). **Ask the user before any
`git push`.** Never rewrite history — when a push is rejected because the
remote moved, merge, don't rebase (this happened once; see §2 Step 11).
Commit trailer per your session's attribution.

**Don't:** put an LLM inside hop1/hop2/hop3/verifier; weaken or skip a
gate; touch `check_v5_clearing_control`; add a dependency without amending
CLAUDE.md rule 8 first; build UI_SPEC §2.9.

---

## 1. Current state (end of 2026-09-03)

| Area | Status |
|---|---|
| P0–P5: generator (14 defects), loader, hop1/2/3, verifier V1–V5, scorer, tier-4 adjudicator, Q&A tools, `/ask` | done, gated |
| P6: `/api/run` · SSE stream · `/metrics` · `/exceptions` · `/api/match` · `/api/order/{id}/chain` · `/api/control/clearing` · `/api/ask` · `--pace` | done, gated |
| P6+: `/api/run/latest` · `/api/eval` · reconstruction detail on `/api/match` · T-account + `difference_p` on `/api/control/clearing` | done, tested |
| P7: `web-app/` Vite+React+TS+Tailwind, tokens, ledger primitives, routes | done |
| P8: Run console (§2.1) + metrics band (§2.2), pace/LLM/narrate controls, rejection chips, adjudication panel | done, browser-verified |
| P9: reconstruction viewer (§2.3), exception queue (§2.4), refusal card (§2.5), clearing control (§2.6) | done, browser-verified |
| P10: chain explorer (§2.7), Q&A console (§2.8), robustness panel, README | done, browser-verified |
| Tier 4 reaches the verifier for real; `demo/llm_wrong_match.py` | done, tested |
| `tests/eval_multi_seed.py` + `docs/eval_report_500.json` | done — 344/500 worlds, 0.0% false-match on all, 0 aborts |
| Old plain-HTML dashboard (`web/index.html`, `/dashboard`) | deleted |
| Video (`SUBMISSION.md` §5) | **not started — the only thing left** |

**LLM reality:** Gemini (`gemini-3.6-flash`) verified live end to end —
`adjudicate`, `explain`, and `converse` (multi-turn Q&A, fixed this
session). `AnthropicLLM` is implemented against the same protocol but has
**never been run live**; say so, don't imply otherwise.

---

## 2. What was built (all steps complete)

Each step's commit is named. The acceptance criteria are the ones the
original plan set; every one was met unless noted.

### Step 0 — safety net ✅ `587442d`
Gemini `FunctionResponse` dict-wrapping fix committed; `main`
fast-forwarded; clean clone passed `make demo`; pushed.

### Step 1 — backend additions ✅ `b5ffb60`
Migration `003_exceptions_evidence.sql` plus all of 1a–1g: persisted
refusal evidence, hop-2 reconstruction detail on `/api/match`, the
PG_RECEIVABLE T-account with `difference_p`, the `narrate` flag
(`--no-narrate`), `rejected` SSE events, `/api/run/latest`, and
`verifier_outcome` on the tier-4 call log. Tests extended.

### Step 2 — refusal card (§2.5) ✅ `7cb079c`
Seed 42 shows exactly one card: `bl_d02_0810a` / `bl_d02_0810b`, both
₹50,738.58, both 2026-08-12.

*Beyond plan:* hop2 also stores `utr_extracted` on a tier-2 refusal, so
the card shows what the parser actually produced. Both sides read
`SETTLEMENT` — a token scraped from the narration, identical on both,
which makes the point better than the planned hardcoded `—` would have.

### Step 3 — reconstruction viewer (§2.3) ✅ `be6f827`
`setl_0812` reconstructs from PAY-0043/44/45/46 + refund PAY-0059 to
delta ₹0.00. Rows stream at 180 ms via `setTimeout` + CSS, not
framer-motion; `prefers-reduced-motion` renders all at once.

*Beyond plan:* the API also returns a running `subtotal_p` per row plus
each row's `settlement_id` and `utr`. The plan said "no summing in the
browser" but also asked for a running subtotal — a partial sum *is* a
sum, so the server computes it. The header's "no UTR recovered · no
settlement id" is asserted off real null values rather than hardcoded.

### Step 4 — clearing account control (§2.6) ✅ `55dc441`
T-account closes at ₹1,36,400.00 == both control numbers; difference
₹0.00 in `--verified`. `difference_p` comes from the API.

### Step 5 — exception queue (§2.4) ✅ `b7d1457`
17 matching (15 queued, 2 lifted into the refusal card); filters work;
"critical only" narrows to 5; every expansion is a table, never JSON.

*Beyond plan:* `/api/run/{id}/exceptions` also returns
`evidence_link_hop`. Only a hop-2 link has settlement arithmetic behind
it, so that field decides whether embedding the reconstruction viewer
would show anything. Without it the queue embedded viewers that rendered
"there is no reconstruction behind this link".

### Step 6 — run console controls, rejections, adjudication log ✅ `84bd2e9`
Pace / LLM / narrate controls; `rejected` events render as `--flag` gutter
chips; adjudication panel under the metrics band. Verified both ways: LLM
off identical to before; LLM on with narrate off completed in 12 s against
real Gemini with three abstentions.

### Step 7 — tier-4 proposals made real ✅ `9c6de84`
Candidates for an unexplained credit now come from every unclaimed
tier-2-eligible row; `date_gap_bdays` and `narration_tokens_matched` are
derived, not hardcoded. With a first-candidate-always model: the twins are
never proposed, `bl_direct_neft` IS proposed and V1 rejects it,
false-match stays 0.0%, V5 ties, G4 unchanged. `make demo-llm-wrong`
prints all of it.

### Step 8 — clean-clone runnability and README ✅ `2c5ec6c`
`uvicorn` declared (CLAUDE.md rule 8 amended); `make ui` / `make eval` /
`make demo-llm-wrong`; `docs/eval_report_500.json` committed; README
rewritten from numbers regenerated that day.

*Correction forced by the rewrite, in README **and** `SUBMISSION.md`:* the
"56.1%" sentence claimed all 25 non-chaining orders hadn't settled by the
cutoff. **Only 11 have that reason.** See §4 for the real breakdown. The
old wording would have been a false claim about our own output on camera.

### Step 9 — Q&A ✅ `b62e0bd` (9a passed well inside its 90-minute box)
**9a:** Gemini's turn-2 `thought_signature` failure is fixed —
`converse()` returns its raw parts, `qa.py` carries them on the assistant
message, and they are replayed verbatim. Required one restructure:
`qa.py` had emitted an assistant message *per tool call*; a turn with two
calls is one turn, so it now emits one per model turn. Verified live with
the exact question that used to fail.
**9b:** `tool_results` (server-computed one-liners) + the §2.8 console.

*Two things the live screen caught:* the `list_exceptions` summary was
summing every severity (₹5,18,664.76) where the metrics band for the same
run reads ₹3,97,605.76 — "at risk" excludes info severity. And the model
answered in markdown, which rendered literally; the system prompt now asks
for plain prose.

### Step 10 — chain explorer + robustness panel ✅ `9ddcc5f`
`ORD-1017` (intact) beside `ORD-1058` (orphan, exception inline under a
dashed break). `GET /api/eval` runs the multi-seed harness in-process; the
panel runs 500 worlds live in ~5 s and renders 344 / 0.0% / 0 aborts.

### Step 11 — cleanup and ship ✅ `fd85bd6`, merged, tagged `v2.0`
`web/index.html`, the `/dashboard` mount and its test deleted; CLAUDE.md
rule 8's frontend note corrected. Full check + `make eval` + a browser
pass over every route. Merged to `main`; fresh clone passes `make demo`,
`make ui`, `make eval`, `make demo-llm-wrong`, 140 tests, 6 gates.
Tagged `v2.0`; `v1.0` left in place.

*Not in the plan:* the push was rejected because PR #1 had merged
`p6-api-layer` into `origin/main` on GitHub. Every commit it carried was
already in local `main`'s linear history — `origin/main`'s tree was
identical to the merge base. Resolved with `git merge origin/main`
(`375efa5`), **not** a rebase or a force: the diff between the pre- and
post-merge commits is empty, not one file changed.

---

## 3. Known issues (current)

| Issue | Where | Status |
|---|---|---|
| `AnthropicLLM` has never been run live | `recon/llm/providers/anthropic_llm.py` | Open. README says so; don't claim otherwise. |
| ~27% of seeds can't generate (D-01 needs a 5–7 row batch with exactly one refund) | `generator/defects.py` | Documented, counted in every reported figure. Not a bug. |
| `_reconstruction_evidence` is best-effort: it follows any match_link touching an exception's records, which is *related to* but not always an *explanation of* that exception | `recon/api.py` | By design, but the UI must keep captioning it as "linked match evidence" rather than as the exception's own. Don't quietly relabel it. |
| UI_SPEC §2.9 (cash position) | — | Deliberately not built. |

**Fixed this session** (kept for the record, since several are the story):
Gemini turn-2 `thought_signature`; tier-4 payload fields hardcoded `0`/`[]`;
no tier-4 proposal could reach the verifier; browser subtracting
residual − exposure; run console hardcoding LLM off and pace 0; `uvicorn`
undeclared; README describing the old dashboard; CLAUDE.md saying `web/`;
the old dashboard still mounted; the 56.1% claim.

---

## 4. Seed-42 reference values (LLM off) — verify screens against these

198 records · precision 100% · recall 99.1% · **false-match 0.0%** ·
full chain **32/57 = 56.1%** · hop match H1 57/57, H2 47/47, H3 6/6 ·
17 open (5 critical / 10 warn / 2 info) · at risk ₹3,97,605.76 ·
reconciled ₹4,51,064.46 · **residual_p = exposure_p = 13,640,000p (₹1,36,400.00)**.

**The 25 orders that don't fully chain — never quote 56.1% without this:**

| Reason | Orders |
|---|---|
| Hasn't settled by the cutoff (2 in-transit batches, ₹1,21,059.00 gross receivable) | 11 |
| The refused ambiguous pair, `PAY-0033`–`PAY-0037` — the engine declines to attribute them | 5 |
| Settled into `setl_0803`, whose GL voucher was deleted (D-05) → `GL_MISSING`, ₹79,551.00 | 9 |

Refusals: `bl_d02_0810a`, `bl_d02_0810b` (₹50,738.58 each, 2026-08-12,
both `utr_extracted = SETTLEMENT`).
Unexplained: `bl_direct_neft` (₹18,000.00, 2026-08-04, "NEFT CR HDFC
KALYANI ENTERPRISES").
Tier-2 batch: `setl_0812` ← PAY-0043, 0044, 0045, 0046 + refund PAY-0059,
reconstructing to ₹37,697.36, delta ₹0.00.
LLM on (Gemini, narrate off, ~12 s): 3 calls → `insufficient_evidence`,
`insufficient_evidence`, `no_match`.
`make demo-llm-wrong`: 3 calls → twins overridden (never proposed),
`bl_direct_neft` proposed → **V1 rejected**; 17 exceptions survive.
Multi-seed `--count 500`: 344 complete, 0.0% false-match on every one,
0 aborts, ~5 s.

---

## 5. Definition of done — met

`main` at `375efa5`, tagged `v2.0` (`fd85bd6` — same tree), pushed: a fresh
clone passes `make demo`,
`make serve`, `make ui`, `make eval`, `make demo-llm-wrong`, all gates,
all 140 unit tests; every route renders from real data with the LLM off;
`demo/llm_wrong_match.py` shows one proposal rejected by V1 and the twins
never proposed; README matches the shipped UI and leads with the 344-world
table; `.env` ignored, untracked, absent from history; `v1.0` still on the
remote as the fallback.

**Outstanding:** the video, per `SUBMISSION.md` §5. Every beat it calls for
now has a working screen behind it — including beat 8's Q&A variant, since
Gemini multi-turn works. Regenerate the numbers with `recon.cli report`
and `make eval` before recording; never type one from memory.
