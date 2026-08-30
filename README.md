# RECON-4 — four-way payment reconciliation agent

Closes the loop **order → gateway payment → bank settlement → general-ledger
entry** over a synthetic-but-realistic batch, and reports per-hop and
full-chain match rates, a false-match rate measured against a generated
answer key, a typed exception queue, a clearing-account control that must
tie to the paisa, and a grounded Q&A agent. See `SPEC.md` for the full
design; `CLAUDE.md` for the standing engineering rules.

## Quickstart

```bash
git clone <this repo> && cd <this repo>
make demo
```

That's `generate → load → run → report` — a fresh seed-42 world, loaded into
a local SQLite database, reconciled end to end (`--llm off` by default, so
no API key is required), with a report printed to the terminal and written
to `data/report.json` / `data/report.html`.

To also see the dashboard and ask it questions:

```bash
pip install uvicorn   # not a project dependency — only serve needs it
make serve             # http://127.0.0.1:8000/dashboard/
```

To run tier 4 (the LLM adjudicator) for real instead of `--llm off`:

```bash
export ANTHROPIC_API_KEY=...   # or GEMINI_API_KEY, with --llm-provider gemini
.venv/bin/python -m recon.cli run --llm on
```

Without a key configured, `--llm on` still runs the whole pipeline and
reports honestly — every LLM-dependent decision just abstains instead of
guessing (CLAUDE.md rule 5).

### Gates

Each phase's acceptance gate is a standalone script:

```bash
for p in 1 2 3 4 5; do .venv/bin/python tests/gates/gate_p$p.py; done
.venv/bin/python -m pytest -q tests/unit
```

## The seven demo beats

Run these in order against a clean `data/recon.db` (`rm -rf data && make
generate && make load` first). Beat 1 states plainly what this synthetic
run does and doesn't prove — see [What synthetic data proves](#what-synthetic-data-proves-and-doesnt) below.

**1 — Show the batch and the answer key.**
```bash
cat data/ground_truth.json | python -m json.tool | head -40
```
This is the generator's own answer key — every link, every expected
exception, every legitimately-unsettled batch — written *before* the engine
ever runs, so scoring against it means something.

**2 — Live run finishes in seconds.**
```bash
time .venv/bin/python -m recon.cli run --llm off
```
Deterministic tiers (hop1/hop2/hop3) do essentially all the work; with
`--llm on`, tier 4 only ever gets a turn on the residue tiers 1–2 couldn't
resolve — not the whole batch.

**3 — Metrics, false-match rate said out loud.**
```bash
.venv/bin/python -m recon.cli report
```
Read the `FALSE-MATCH RATE` line — it's printed as its own line, deliberately
not buried in a table.

**4 — Hard case: a truncated-UTR settlement reconstructed by subset-sum.**
```bash
.venv/bin/python -c "
import json, sqlite3
conn = sqlite3.connect('data/recon.db')
row = conn.execute(\"SELECT evidence FROM match_link WHERE reason='tier2_subset_sum_unique' LIMIT 1\").fetchone()
print(json.dumps(json.loads(row[0]), indent=2))
"
```
The evidence blob shows the actual reconstruction arithmetic: every
candidate row's net, the subtotal, the target, the delta — not just a
"trust me, it matched."

**5 — Honest refusal: identical twin settlements.**
```bash
.venv/bin/python -c "
from recon.llm import tools
import sqlite3
conn = sqlite3.connect('data/recon.db')
for e in tools.list_exceptions(conn, code='AMBIGUOUS_SETTLEMENT'):
    print(e['explanation'])
"
```
Two settlements, same value_date, identical net to the paisa — the engine
names both bank lines and refuses to guess which gateway rows belong to
which. Nothing in this codebase ever breaks that tie, not even tier 4
(enforced unconditionally in `recon/llm/adjudicator.py`, not just by prompt).

**6 — Clearing account: residual ties to the paisa.**
```bash
.venv/bin/python -m recon.cli report | grep "Clearing control"
```
`GL residual == exception exposure`, computed two structurally independent
ways (straight from `gl_entries`, and from the exceptions queue's own
inclusion map) — if they ever disagree, the run aborts loudly instead of
printing a wrong number (`recon/engine/verifier.py`, V5).

**7 — Live question answered with record IDs.**
```bash
make serve &   # needs uvicorn (pip install uvicorn) and an LLM key for a live answer
curl -s -X POST localhost:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "Trace ORD-1017 end to end."}' | python -m json.tool
```
Or use the dashboard's chat box at `/dashboard/` — same endpoint. Every
figure in the answer is quoted with the record ID it came from
(`recon/llm/qa.py`'s system prompt requires it, and `record_ids` is returned
alongside the answer for exactly this reason).

## What synthetic data proves — and doesn't

This batch is generated with a fixed seed and a known set of 14 injected
defects, each with a hand-written expected outcome in `ground_truth.json`.
That lets us prove something real: on this batch, the engine's false-match
rate against a known-correct answer key is exactly 0.0%, every clean record
chains through all three hops, the ambiguous case is genuinely refused
rather than guessed, and the clearing-account control ties to the paisa.
That is a meaningful claim about the matching *logic* — the tiering, the
subset-sum reconstruction, the refusal rule, the verifier's independent
re-checks — because those are exercised exactly as they would be on real
data of the same shape.

It does **not** prove the system handles real-world mess it was never shown:
genuinely adversarial or malformed bank statement formats beyond the two
data-quality fixtures here, defect *combinations* beyond the ones injected,
the non-goals below, or scale (this is ~200 records, not millions). A
synthetic answer key is only as honest as the defects it encodes — this one
encodes 14, not "all of them."

**Non-goals** (verbatim from SPEC.md §1): FX/cross-border, rolling reserve,
TDS 194-O, chargeback lifecycle beyond first appearance, multi-currency,
multi-merchant, GST-return matching, auth/void flows.
