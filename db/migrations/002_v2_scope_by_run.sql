-- Fix (P6 supplement): V2's one_claim_a/one_claim_b indexes were scoped
-- globally across every run ever stored in the DB, not per-run. Proven via
-- execution (not from the spec text): calling run_pipeline() twice against
-- the same already-loaded DB — same seed, unchanged data — makes the
-- second run's fresh, entirely legitimate proposals collide with the
-- FIRST run's still-'accepted' rows, producing ~110 spurious
-- DUPLICATE_CLAIM exceptions instead of the honest report.
--
-- V2's actual intent (SPEC.md §6.5, CLAUDE.md rule 7) is "no two proposals
-- WITHIN ONE RUN may claim the same record" — never "ever, across every
-- run this DB has seen." The P6 API (POST /api/run triggered repeatedly
-- against a persistent DB; GET /api/run/{id}/metrics addressing one run
-- among several coexisting) requires multiple runs' match_link rows to
-- coexist, which the un-scoped index made impossible. Confirmed with the
-- user 2026-08-31 before touching this invariant.
DROP INDEX one_claim_a;
DROP INDEX one_claim_b;

CREATE UNIQUE INDEX one_claim_a ON match_link (src_a, id_a, hop, run_id) WHERE status = 'accepted';
CREATE UNIQUE INDEX one_claim_b ON match_link (src_b, id_b, hop, run_id) WHERE status = 'accepted' AND hop <> 2;
