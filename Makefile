.PHONY: setup generate load run report serve check demo

setup:
	@echo "setup: not yet implemented"

generate:
	@echo "generate: not yet implemented"

load:
	@echo "load: not yet implemented"

run:
	@echo "run: not yet implemented"

report:
	@echo "report: not yet implemented"

serve:
	@echo "serve: not yet implemented"

check:
	@echo "check: not yet implemented"

# demo = generate -> load -> run -> report (SPEC §1). Must exit 0 on main
# at every commit (CLAUDE.md rule 4), even before those steps are wired up.
demo:
	@echo "demo: not yet implemented (will run generate -> load -> run -> report)"
