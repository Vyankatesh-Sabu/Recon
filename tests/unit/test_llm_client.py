"""test_llm_client.py — Adjudication/ExceptionNarrative schemas + MockLLM (SPEC §8)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from recon.llm.client import Adjudication, ExceptionNarrative, MockLLM, create_llm_client


def test_adjudication_extra_forbid():
    Adjudication.model_validate(
        {"decision": "match", "candidate": "a", "reason_code": None, "explanation": "x", "confidence": 0.9}
    )
    with pytest.raises(ValidationError):
        Adjudication.model_validate(
            {
                "decision": "match",
                "candidate": "a",
                "reason_code": None,
                "explanation": "x",
                "confidence": 0.9,
                "extra_field": "not allowed",
            }
        )


def test_adjudication_rejects_unknown_decision():
    with pytest.raises(ValidationError):
        Adjudication.model_validate(
            {"decision": "maybe", "candidate": None, "reason_code": None, "explanation": "x", "confidence": 0.5}
        )


def test_exception_narrative_extra_forbid():
    ExceptionNarrative.model_validate({"explanation": "x", "suggested_action": "y"})
    with pytest.raises(ValidationError):
        ExceptionNarrative.model_validate({"explanation": "x", "suggested_action": "y", "extra": 1})


def test_mock_llm_default_abstains_when_no_decisive_candidate():
    llm = MockLLM()
    payload = {
        "task": "hop2_unresolved_bank_credit",
        "item": {"line_id": "bl_x", "value_date": "2026-08-10", "credit_p": 1000, "narration": "..."},
        "candidates": [{"batch": "a", "rows": 1, "net_p": 900, "delta_p": -100}],
        "failed_checks": [],
        "instruction": "...",
    }
    result = Adjudication.model_validate_json(llm.adjudicate(payload))
    assert result.decision == "insufficient_evidence"


def test_mock_llm_default_matches_single_decisive_candidate():
    llm = MockLLM()
    payload = {
        "task": "hop2_unresolved_bank_credit",
        "item": {"line_id": "bl_x", "value_date": "2026-08-10", "credit_p": 1000, "narration": "..."},
        "candidates": [{"batch": "only", "rows": 1, "net_p": 1000, "delta_p": 0}],
        "failed_checks": [],
        "instruction": "...",
    }
    result = Adjudication.model_validate_json(llm.adjudicate(payload))
    assert result.decision == "match"
    assert result.candidate == "only"


def test_mock_llm_scripted_response_overrides_default():
    llm = MockLLM(adjudicate_script={"bl_wrong": {"decision": "match", "candidate": "candidate_a", "reason_code": None, "explanation": "scripted wrong", "confidence": 0.99}})
    payload = {"task": "t", "item": {"line_id": "bl_wrong"}, "candidates": [], "failed_checks": [], "instruction": ""}
    result = Adjudication.model_validate_json(llm.adjudicate(payload))
    assert result.decision == "match"
    assert result.explanation == "scripted wrong"


def test_mock_llm_records_calls_for_inspection():
    llm = MockLLM()
    llm.adjudicate({"task": "t", "item": {"line_id": "x"}, "candidates": [], "failed_checks": [], "instruction": ""})
    llm.explain({"code": "GL_MISSING", "amount_at_risk_p": 500})
    assert len(llm.calls) == 2
    assert llm.calls[0]["kind"] == "adjudicate"
    assert llm.calls[1]["kind"] == "explain"


def test_mock_llm_explain_default_and_scripted():
    llm = MockLLM(explain_script={"GL_MISSING": {"explanation": "scripted", "suggested_action": "act"}})
    default = ExceptionNarrative.model_validate_json(llm.explain({"code": "ORPHAN_ORDER", "amount_at_risk_p": 100}))
    assert "ORPHAN_ORDER" in default.explanation
    scripted = ExceptionNarrative.model_validate_json(llm.explain({"code": "GL_MISSING", "amount_at_risk_p": 100}))
    assert scripted.explanation == "scripted"


def test_create_llm_client_rejects_unknown_provider():
    with pytest.raises(ValueError):
        create_llm_client("not-a-real-provider")
