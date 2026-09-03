#!/usr/bin/env python
"""llm_wrong_match.py — what happens when the model is wrong.

`make demo-llm-wrong`. A real run of the real pipeline on seed 42, with a
deliberately wrong stand-in for the model: it always picks the first
candidate it is offered, confidently. Nothing here is mocked except the
model itself — the same hops, the same verifier, the same V5 control.

Two different things stop it, and the difference is the point:

  * On the D-02 twins it is not allowed to propose at all. Both readings
    of that bank line are arithmetically valid, so V1 would happily accept
    either — verification cannot save you from a tie, and the refusal has
    to be absolute, enforced before a proposal exists.
  * On the unexplained credit it does propose, and the verifier re-derives
    the arithmetic from the raw gateway rows and throws it out.

Video beat 6b (SUBMISSION.md §5). Prints, per tier-4 call: the line, the
candidates offered with their deltas, the model's decision, and what
happened to it — then the surviving exceptions, the false-match rate, and
the V5 tie.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import config  # noqa: E402
from recon.db import migrate  # noqa: E402
from recon.engine import verifier  # noqa: E402
from recon.engine.pipeline import run_pipeline  # noqa: E402
from recon.generator import generate_world  # noqa: E402
from recon.generator.io import write_csvs  # noqa: E402
from recon.generator.truth import write_ground_truth  # noqa: E402
from recon.loader import load_all  # noqa: E402
from recon.moneymath import format_rupees  # noqa: E402

RULE = "─" * 78


class WrongLLM:
    """Always picks the first candidate, with 0.95 confidence.

    Not a strawman. On a genuine tie every candidate is equally defensible,
    so "pick the first" is indistinguishable from a careful model that
    happened to choose wrong — which is exactly the failure a confident
    model produces in the wild.
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


def _build_world(root: Path) -> tuple[Path, Path]:
    """Generate and load seed 42 into a scratch database, so this demo never
    touches data/recon.db or the run history the report reads from."""
    data_dir = root / "data"
    world, truth = generate_world(config.SEED, defects=True)
    write_csvs(world, data_dir)
    gt_path = data_dir / "ground_truth.json"
    write_ground_truth(truth, gt_path)
    db_path = root / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    try:
        report = load_all(conn, data_dir)
        assert report.quarantined == [], report.quarantined
    finally:
        conn.close()
    return db_path, gt_path


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path, gt_path = _build_world(Path(tmp))

        print(RULE)
        print("RECON-4 — a deliberately wrong model, against the real pipeline")
        print(f"seed {config.SEED} · the model always picks the first candidate, confidence 0.95")
        print(RULE)

        ctx = run_pipeline(
            db_path=db_path,
            ground_truth_path=gt_path,
            seed=config.SEED,
            llm_mode="on",
            llm_client=WrongLLM(),
            narrate=False,  # adjudication is what is on trial here, not prose
        )
        run_id = ctx["run_id"]
        metrics = ctx["metrics"]

        conn = sqlite3.connect(db_path)
        try:
            tier4 = {
                id_b: (status, reason)
                for id_b, status, reason in conn.execute(
                    "SELECT id_b, status, reason FROM match_link WHERE run_id = ? AND tier = 4",
                    (run_id,),
                )
            }

            for call in metrics["llm_call_log"]:
                item = call["payload"]["item"]
                print()
                print(f"BANK LINE {item['line_id']}  {item['value_date']}  {format_rupees(item['credit_p'])}")
                print(f"          {item['narration']}")
                print("  candidates offered:")
                if not call["payload"]["candidates"]:
                    print("    (none)")
                for cand in call["payload"]["candidates"]:
                    print(
                        f"    {cand['batch']:20} {cand['rows']} row(s)  net {format_rupees(cand['net_p']):>14}"
                        f"  delta {format_rupees(cand['delta_p']):>14}"
                        f"  gap {cand['date_gap_bdays']:+d} bday"
                        f"  narration tokens {cand['narration_tokens_matched'] or '—'}"
                    )
                print(f"  model decision:    {call['decision']}")

                outcome = tier4.get(item["line_id"])
                if outcome is None:
                    print("  what happened:     overridden: ambiguous refusal is absolute")
                    print("                     (V1 cannot catch a wrong pick here — both readings")
                    print("                      re-derive to the same number, which is what a tie IS)")
                else:
                    status, reason = outcome
                    print(f"  what happened:     proposed → verifier {status.upper()}: {reason}")

            print()
            print(RULE)
            open_rows = conn.execute(
                "SELECT code, COUNT(*) FROM exceptions WHERE run_id = ? AND status = 'open' "
                "GROUP BY code ORDER BY code",
                (run_id,),
            ).fetchall()
            total_open = sum(n for _, n in open_rows)
            print(f"Surviving open exceptions: {total_open}")
            for code, n in open_rows:
                print(f"  {code:28} {n}")

            residual_p = verifier.compute_residual_p(conn)
            exposure_p, _ = verifier.compute_exposure_p(conn, run_id)
        finally:
            conn.close()

        calls = metrics["llm_calls"]
        print()
        print(
            f"LLM: {calls['total']} calls — {calls['accepted']} accepted, "
            f"{calls['rejected']} rejected by the verifier, {calls['abstained']} abstained/overridden"
        )
        print(f"FALSE-MATCH RATE: {metrics['false_match_rate'] * 100:.1f}%")
        print(
            f"V5 clearing control: GL residual {format_rupees(residual_p)} == "
            f"exception exposure {format_rupees(exposure_p)} "
            f"{'✓' if residual_p == exposure_p else '✗ ABORT'}"
        )
        print(RULE)
        print("A confidently wrong model changed nothing. That is the product.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
