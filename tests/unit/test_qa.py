"""test_qa.py — the grounded tool-calling loop (SPEC §9.2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import config
from recon.db import migrate
from recon.engine.pipeline import run_pipeline
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.llm import qa
from recon.llm.client import MockLLM
from recon.loader import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]


def _seeded_conn(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    data_dir = tmp_path / "data"
    world, truth = generate_world(config.SEED, defects=True)
    write_csvs(world, data_dir)
    gt_path = data_dir / "ground_truth.json"
    write_ground_truth(truth, gt_path)
    db_path = tmp_path / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    assert load_all(conn, data_dir).quarantined == []
    conn.close()
    ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="off")
    return sqlite3.connect(db_path), ctx["run_id"]


def test_single_tool_call_then_answer(tmp_path: Path):
    conn, run_id = _seeded_conn(tmp_path)
    order_id = conn.execute("SELECT order_id FROM orders WHERE method != 'cod' LIMIT 1").fetchone()[0]
    mock = MockLLM(
        qa_script={
            "trace this order": [
                {"tool_call": {"name": "trace_order", "input": {"order_id": order_id}}},
                {"answer": f"Order {order_id} traced successfully."},
            ]
        }
    )
    result = qa.answer_question(conn, f"trace this order {order_id}", mock, run_id=run_id)
    assert result["answer"] == f"Order {order_id} traced successfully."
    assert result["tool_calls"] == [{"name": "trace_order", "input": {"order_id": order_id}}]
    assert order_id in result["record_ids"]


def test_max_four_tool_calls_enforced(tmp_path: Path):
    conn, run_id = _seeded_conn(tmp_path)
    # script 6 tool calls; the loop must stop executing after 4
    steps = [{"tool_call": {"name": "list_exceptions", "input": {}}} for _ in range(6)] + [
        {"answer": "done"}
    ]
    mock = MockLLM(qa_script={"how many": steps})
    result = qa.answer_question(conn, "how many exceptions total", mock, run_id=run_id)
    assert len(result["tool_calls"]) == qa.MAX_TOOL_CALLS
    # the loop terminates via the tool-call cap, not by exhausting the script
    assert result["answer"] == "The data does not show enough to answer this."


def test_no_scripted_response_says_data_does_not_show(tmp_path: Path):
    conn, run_id = _seeded_conn(tmp_path)
    mock = MockLLM()  # no qa_script at all
    result = qa.answer_question(conn, "what is the meaning of life", mock, run_id=run_id)
    assert "does not show" in result["answer"]
    assert result["tool_calls"] == []
    assert result["record_ids"] == []


def test_unknown_tool_name_reported_as_error_not_crash(tmp_path: Path):
    conn, run_id = _seeded_conn(tmp_path)
    mock = MockLLM(
        qa_script={
            "bogus": [
                {"tool_call": {"name": "delete_everything", "input": {}}},
                {"answer": "handled"},
            ]
        }
    )
    result = qa.answer_question(conn, "bogus question", mock, run_id=run_id)
    assert result["answer"] == "handled"
    assert result["tool_calls"] == [{"name": "delete_everything", "input": {}}]
