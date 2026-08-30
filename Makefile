PY := .venv/bin/python

.PHONY: setup generate load run report serve check demo

# Idempotent: safe to re-run on an existing .venv (CLAUDE.md rule 4 — make
# demo must work on main at every commit, including a clean checkout).
setup:
	python3.12 -m venv .venv
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	@echo "setup: venv ready at .venv, dependencies installed"

generate: setup
	$(PY) -m recon.cli generate

load: setup
	$(PY) -m recon.db
	$(PY) -m recon.cli load

run: setup
	$(PY) -m recon.cli run

report: setup
	$(PY) -m recon.cli report

serve: setup
	$(PY) -m recon.cli serve

check: setup
	$(PY) -m pytest -q

# demo = generate -> load -> run -> report (SPEC §1). Must exit 0 on main
# at every commit (CLAUDE.md rule 4). `run` defaults to --llm off (CLAUDE.md
# rule 5: never load-bearing) so demo never depends on an API key; the full
# V3 -> hop1 -> hop2 -> hop3 -> verifier -> [tier4] -> V5 -> scorer pipeline
# is complete as of P4. `serve` (P5) additionally needs uvicorn installed —
# see recon/cli.py's serve command for the fallback message if it's not.
demo: generate load run report
	@echo "demo: complete (full P5 pipeline; run with --llm on for tier4; 'make serve' for the Q&A endpoint)"
