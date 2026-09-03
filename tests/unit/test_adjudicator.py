"""test_adjudicator.py — tier-4 adjudication end to end (SPEC §8, T-6/G4)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import config
from recon.db import migrate
from recon.engine.pipeline import run_pipeline
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.llm.client import MockLLM
from recon.loader import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]


def _seeded_pipeline_inputs(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    world, truth = generate_world(config.SEED, defects=True)
    write_csvs(world, data_dir)
    gt_path = data_dir / "ground_truth.json"
    write_ground_truth(truth, gt_path)
    db_path = tmp_path / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    report = load_all(conn, data_dir)
    assert report.quarantined == []
    conn.close()
    return db_path, gt_path


def _d02_line_ids(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    ids = [r[0] for r in conn.execute("SELECT line_id FROM bank_lines WHERE line_id LIKE 'bl_d02_%'")]
    conn.close()
    return ids


def test_g4_wrong_match_on_d02_twins_is_rejected_ambiguous_survives(tmp_path: Path):
    db_path, gt_path = _seeded_pipeline_inputs(tmp_path)
    d02_lines = _d02_line_ids(db_path)
    assert len(d02_lines) == 2

    # MockLLM deliberately scripted to return a WRONG "match" for both twins
    # — SPEC's own point: any engine or LLM that picks one is wrong.
    script = {
        line_id: {
            "decision": "match",
            "candidate": "candidate_a",
            "reason_code": None,
            "explanation": "Picking the larger subset (deliberately wrong test fixture).",
            "confidence": 0.87,
        }
        for line_id in d02_lines
    }
    mock = MockLLM(adjudicate_script=script)

    ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="on", llm_client=mock)

    conn = sqlite3.connect(db_path)
    try:
        # the LLM's wrong "match" must never even become a proposal for a
        # genuinely ambiguous line — SPEC's refusal is absolute, enforced
        # unconditionally in adjudicator.py (V1+V2 alone can't catch this:
        # a wrong pick on a real "Multiple" still passes arithmetic re-checks)
        for line_id in d02_lines:
            n_links = conn.execute(
                "SELECT COUNT(*) FROM match_link WHERE id_b = ? AND tier = 4", (line_id,)
            ).fetchone()[0]
            assert n_links == 0, f"{line_id}: a wrong tier-4 match must never even be proposed"
            n_accepted = conn.execute(
                "SELECT COUNT(*) FROM match_link WHERE id_b = ? AND status = 'accepted'", (line_id,)
            ).fetchone()[0]
            assert n_accepted == 0, f"{line_id}: must never be accepted by any tier"

        # AMBIGUOUS_SETTLEMENT must survive, still open, for both lines
        exc_rows = conn.execute(
            "SELECT records, status FROM exceptions WHERE code = 'AMBIGUOUS_SETTLEMENT'"
        ).fetchall()
        assert len(exc_rows) == 2
        for records_json, status in exc_rows:
            assert status == "open"
            line_id = json.loads(records_json)[0]["id"]
            assert line_id in d02_lines
    finally:
        conn.close()

    # the report's LLM tally reflects both wrong "match" attempts as
    # overridden/abstained, never proposed, never accepted (a 3rd residue —
    # D-07's UNEXPLAINED_BANK_CREDIT, unscripted — also abstains by default,
    # since it has zero candidates)
    llm_calls = ctx["metrics"]["llm_calls"]
    assert llm_calls["abstained"] == 3
    assert llm_calls["accepted"] == 0
    assert llm_calls["rejected"] == 0
    overridden = [c for c in ctx["metrics"]["llm_call_log"] if c["line_id"] in d02_lines]
    assert len(overridden) == 2
    assert all("overridden" in c["decision"] for c in overridden)


def test_g4_d01_still_resolves_with_llm_on(tmp_path: Path):
    db_path, gt_path = _seeded_pipeline_inputs(tmp_path)
    mock = MockLLM()  # default (well-behaved) MockLLM — D-01 never even reaches tier4
    ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="on", llm_client=mock)

    conn = sqlite3.connect(db_path)
    try:
        # D-01's batch is tier2_unique at hop2 — resolved before tier4 ever runs
        row = conn.execute(
            "SELECT COUNT(*) FROM match_link WHERE tier = 2 AND reason = 'tier2_subset_sum_unique' AND status = 'accepted'"
        ).fetchone()[0]
        assert row > 0
    finally:
        conn.close()
    assert ctx["metrics"]["false_match_rate"] == 0.0


def test_g4_llm_off_runs_whole_pipeline_and_reports_honestly(tmp_path: Path):
    db_path, gt_path = _seeded_pipeline_inputs(tmp_path)
    ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="off")
    assert ctx["metrics"]["llm_mode"] == "off"
    assert ctx["metrics"]["llm_calls"] == {"total": 0, "accepted": 0, "rejected": 0, "abstained": 0}
    # AMBIGUOUS_SETTLEMENT / UNEXPLAINED_BANK_CREDIT stand exactly as hop2 left them
    codes = {row[0] for row in sqlite3.connect(db_path).execute("SELECT code FROM exceptions WHERE status='open'")}
    assert "AMBIGUOUS_SETTLEMENT" in codes
    assert "UNEXPLAINED_BANK_CREDIT" in codes
    assert ctx["metrics"]["false_match_rate"] == 0.0


def test_default_mockllm_abstains_on_genuine_ambiguity(tmp_path: Path):
    """A well-behaved model (not deliberately scripted wrong) correctly
    abstains on the D-02 twins — both candidates are equally decisive
    (delta_p == 0 for each), so len(decisive) != 1 and the default logic
    reports insufficient_evidence, exactly as SPEC's "abstaining is a
    correct and rewarded outcome" intends."""
    db_path, gt_path = _seeded_pipeline_inputs(tmp_path)
    d02_lines = _d02_line_ids(db_path)
    mock = MockLLM()
    run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="on", llm_client=mock)

    adjudicate_calls = [c for c in mock.calls if c["kind"] == "adjudicate"]
    d02_calls = [c for c in adjudicate_calls if c["payload"]["item"]["line_id"] in d02_lines]
    assert len(d02_calls) == 2
    for call in d02_calls:
        assert len(call["payload"]["candidates"]) == 2

    conn = sqlite3.connect(db_path)
    exc_rows = conn.execute("SELECT status FROM exceptions WHERE code = 'AMBIGUOUS_SETTLEMENT'").fetchall()
    conn.close()
    assert all(status == "open" for (status,) in exc_rows)


# --- Step 7: a tier-4 proposal that actually reaches the verifier -------


class WrongLLM:
    """A model that always picks the first candidate it is offered, with
    high confidence — the shape of failure this system is built to survive.

    It is not a strawman: on the D-02 twins, picking either candidate is
    arithmetically valid, so it is picking a defensible-looking answer.
    That is precisely why the ambiguity override, not V1, has to stop it
    there — and why the unexplained credit, where V1 *can* check the
    arithmetic, is the case that proves the verifier does its job.
    """

    def adjudicate(self, payload: dict) -> str:
        candidates = payload["candidates"]
        if not candidates:
            return json.dumps(
                {
                    "decision": "insufficient_evidence",
                    "candidate": None,
                    "explanation": "No candidates were offered.",
                    "confidence": 0.1,
                }
            )
        return json.dumps(
            {
                "decision": "match",
                "candidate": candidates[0]["batch"],
                "explanation": "The first candidate is the best fit.",
                "confidence": 0.95,
            }
        )

    def explain(self, evidence: dict) -> str:
        return json.dumps(
            {
                "explanation": evidence["template_explanation"],
                "suggested_action": evidence["template_suggested_action"],
            }
        )

    def converse(self, messages: list[dict], tools: list[dict], system: str) -> dict:
        raise NotImplementedError("WrongLLM only adjudicates")


def test_wrong_model_is_overridden_on_ties_and_rejected_by_the_verifier(tmp_path: Path):
    """The whole tier-4 safety story, on one run of real code.

    Before this, no tier-4 proposal could reach the verifier at all: the
    twins were overridden before proposal (correct) and the unexplained
    credit was offered zero candidates, so a "match" resolved to nothing.
    G4 passed because nothing was ever proposed. With real candidates, a
    confidently wrong model proposes exactly once — and V1 throws it out.
    """
    db_path, gt_path = _seeded_pipeline_inputs(tmp_path)
    ctx = run_pipeline(
        db_path=db_path,
        ground_truth_path=gt_path,
        seed=config.SEED,
        llm_mode="on",
        llm_client=WrongLLM(),
        narrate=False,
    )
    run_id = ctx["run_id"]
    metrics = ctx["metrics"]
    conn = sqlite3.connect(db_path)
    try:
        tier4 = conn.execute(
            "SELECT id_a, id_b, status, reason FROM match_link WHERE run_id = ? AND tier = 4",
            (run_id,),
        ).fetchall()
        assert len(tier4) == 1, "exactly one tier-4 proposal reached the verifier"
        (_id_a, id_b, status, reason) = tier4[0]
        assert id_b == "bl_direct_neft", "the unexplained credit is the only proposable residue"
        assert status == "rejected"
        assert reason.startswith("V1_failed (tier4/llm proposal rejected)")

        # The twins were never proposed at all — the ambiguity override
        # stops them before the verifier ever sees them, because V1 could
        # not catch a wrong pick there (both readings are valid arithmetic).
        twin_lines = set(_d02_line_ids(db_path))
        assert twin_lines, "seed 42 has the D-02 twins"
        proposed_lines = {r[0] for r in conn.execute(
            "SELECT id_b FROM match_link WHERE run_id = ? AND tier = 4", (run_id,)
        )}
        assert not (proposed_lines & twin_lines)

        open_codes = [
            r[0]
            for r in conn.execute(
                "SELECT code FROM exceptions WHERE run_id = ? AND status = 'open'", (run_id,)
            )
        ]
        assert open_codes.count("AMBIGUOUS_SETTLEMENT") == 2
        assert open_codes.count("UNEXPLAINED_BANK_CREDIT") == 1, "a rejected proposal resolves nothing"
    finally:
        conn.close()

    assert metrics["false_match_rate"] == 0.0, "a wrong model must not move this number"
    assert metrics["llm_calls"] == {"total": 3, "accepted": 0, "rejected": 1, "abstained": 2}

    # V5 still ties — reaching this line at all proves it, since
    # run_pipeline lets ClearingControlFailure propagate uncaught.
    from recon.engine import verifier

    conn = sqlite3.connect(db_path)
    try:
        residual_p = verifier.compute_residual_p(conn)
        exposure_p, _ = verifier.compute_exposure_p(conn, run_id)
        assert residual_p == exposure_p
    finally:
        conn.close()


def test_tier4_candidates_carry_real_date_gaps_and_narration_tokens(tmp_path: Path):
    """The two per-candidate signals were hardcoded to 0 and [], which told
    the model — falsely — that every candidate sat exactly on the value
    date and that the narration named none of them. They must be derived."""
    from recon.llm import adjudicator

    db_path, gt_path = _seeded_pipeline_inputs(tmp_path)
    ctx = run_pipeline(
        db_path=db_path,
        ground_truth_path=gt_path,
        seed=config.SEED,
        llm_mode="on",
        llm_client=WrongLLM(),
        narrate=False,
    )
    calls = {c["line_id"]: c for c in ctx["metrics"]["llm_call_log"]}

    unexplained = calls["bl_direct_neft"]["payload"]["candidates"]
    assert unexplained, "the unexplained credit must be offered real candidates"
    assert len(unexplained) <= adjudicator.MAX_CANDIDATES
    # nearest by |delta| first, so a genuine explanation would be visible
    deltas = [abs(c["delta_p"]) for c in unexplained]
    assert deltas == sorted(deltas)
    assert all(c["batch"].startswith("row:") for c in unexplained)
    # bl_direct_neft's value date is 4 business days before these captures
    assert all(c["date_gap_bdays"] != 0 for c in unexplained)
    # "NEFT CR HDFC KALYANI ENTERPRISES" names no PAY-/ORD- id, and saying
    # so honestly is the point — an empty list here is a real finding
    assert all(c["narration_tokens_matched"] == [] for c in unexplained)

    twin = calls["bl_d02_0810a"]["payload"]["candidates"]
    assert {c["batch"] for c in twin} == {"candidate_a", "candidate_b"}
    assert all(c["date_gap_bdays"] != 0 for c in twin), "derived, not the old hardcoded 0"
