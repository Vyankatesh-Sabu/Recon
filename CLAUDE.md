# CLAUDE.md — RECON-4 standing rules

Full design is in `SPEC.md`. These are the hard rules that override anything
in SPEC.md where the two conflict (notably: this project runs on SQLite, not
Postgres/docker-compose). Read SPEC.md for the "why" and the phase plan;
treat this file as the non-negotiable "how".

1. **Money is ALWAYS integer paise.** Python `int`, variable suffix `_p`.
   Never `float`, never `Decimal`, no arithmetic on rupee decimals. Format to
   `"₹x,xxx.xx"` only at display time (report/HTML/CLI output), never before.

2. **Database is SQLite** (stdlib `sqlite3`) at `data/recon.db`. Plain SQL,
   no ORM. Schema lives in `db/migrations/NNN_*.sql`, applied in filename
   order. SQLite partial unique indexes (`CREATE UNIQUE INDEX ... WHERE ...`)
   are used and are load-bearing — they encode invariant V2 (no double
   claims). Do not replace them with application-level checks.

3. **All randomness goes through one explicitly-passed `random.Random(seed)`.**
   No bare `random.random()`/`random.choice()` calls against the global
   instance anywhere. Same seed must produce byte-identical output files
   (generator CSVs, `ground_truth.json`, run reports modulo timestamps).

4. **`make demo` must work on `main` at every commit.** Build the walking
   skeleton first, deepen later — never leave `main` in a state where `make
   demo` fails.

5. **The LLM is never load-bearing for correctness.** Every LLM-dependent
   feature needs a `--llm off` path where the system still runs and reports
   honestly — items the LLM would have handled become exceptions, not
   silent gaps or crashes.

6. **When a match is uncertain, raise an exception. NEVER guess.** A false
   match is a critical bug; a false exception is cosmetic. All tie-breaking
   logic must prefer refusal over a guess.

7. **Only `verifier.py` may set `match_link.status = 'accepted'`.** No other
   code path — not a hop engine, not the LLM adjudicator, not a script — may
   flip that status.

8. **Stack: Python 3.12, stdlib + pydantic v2 + typer + pytest + fastapi.**
   Nothing else without asking first.

9. **Phase gates in `tests/gates/` are mandatory.** Never start phase N+1
   before phase N's gate script passes. Never weaken a gate to make it pass.
