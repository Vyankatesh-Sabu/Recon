PY := .venv/bin/python

.PHONY: setup generate load run report check demo

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

check: setup
	$(PY) -m pytest -q

# demo = generate -> load -> run -> report (SPEC §1). Must exit 0 on main
# at every commit (CLAUDE.md rule 4). As of P3: generate/load/run/report run
# the full V3 -> hop1 -> hop2 -> hop3 -> verifier -> V5 -> scorer -> report
# pipeline, including the clearing-account control; tier4 lands in P4.
demo: generate load run report
	@echo "demo: complete (P3 pipeline: hop1+hop2+hop3+verifier+V5+scorer; tier4 lands in P4)"
