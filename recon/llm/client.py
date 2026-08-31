"""client.py — LLMClient strategy interface, MockLLM, and provider backends (SPEC §8, §9).

Interface first, real model last. `LLMClient` is a `Protocol` with three
duties: `adjudicate`/`explain` (tier 4, SPEC §8 — return raw JSON text,
parsed and validated only by adjudicator.py) and `converse` (the Q&A tool-
calling loop, SPEC §9.2 — returns an already-structured dict, since a
tool-calling turn has no single JSON payload to validate against a fixed
schema the way the other two duties do). Every concrete backend — `MockLLM`
(canned, used by every test and gate), `AnthropicLLM`, `GeminiLLM` —
implements the same three methods. CLAUDE.md rule 5: the LLM is never
load-bearing — every concrete client (including a missing/misconfigured
RealLLM) fails by raising, never by fabricating a plausible-looking answer,
and `--llm off` never constructs a client at all.
"""

from __future__ import annotations

import json
import os
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

# Every exception code this system raises (hop1/2/3 + verifier) — the closed
# set `Adjudication.reason_code` may name.
ExceptionCode = Literal[
    "ORPHAN_ORDER",
    "ORPHAN_PAYMENT",
    "DUPLICATE_PAYMENT",
    "PARTIAL_CAPTURE_MISMATCH",
    "FEE_VARIANCE",
    "AMBIGUOUS_SETTLEMENT",
    "UNEXPLAINED_BANK_CREDIT",
    "MISSING_IN_BANK",
    "UNSETTLED_IN_TRANSIT",
    "GL_MISSING",
    "GL_DUPLICATE",
    "GL_AMBIGUOUS_MATCH",
    "GL_DECOMPOSITION_FAIL",
    "UNLINKED_REFUND",
    "CHARGEBACK_UNRESOLVED",
    "DATA_QUALITY",
    "DUPLICATE_CLAIM",
]


class Adjudication(BaseModel):
    """SPEC §8's tier-4 output schema. extra="forbid" — a model that invents
    extra fields fails validation exactly like one that omits required ones."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["match", "no_match", "insufficient_evidence"]
    candidate: str | None = None
    reason_code: ExceptionCode | None = None
    explanation: str  # ≤ 2 sentences, plain language
    confidence: float  # 0..1


class ExceptionNarrative(BaseModel):
    """Second LLM duty's output schema: explanation + suggested_action for
    one exception, rendered from a structured evidence dict."""

    model_config = ConfigDict(extra="forbid")

    explanation: str
    suggested_action: str


class LLMClient(Protocol):
    """Strategy interface — every backend (mock or real, any provider)
    implements exactly this."""

    def adjudicate(self, payload: dict) -> str:
        """Raw response text for a tier-4 payload; adjudicator.py parses/validates it."""
        ...

    def explain(self, evidence: dict) -> str:
        """Raw response text for an exception-narration request; adjudicator.py parses/validates it."""
        ...

    def converse(self, messages: list[dict], tools: list[dict], system: str) -> dict:
        """One turn of the Q&A tool-calling loop (SPEC §9.2).

        `messages`: provider-agnostic history — each entry is
        `{"role": "user"|"assistant"|"tool_result", "content": ...}`; a
        `tool_result` entry's content is `{"tool_call_id", "name", "result"}`.
        `tools`: SPEC §9.1 tool schemas (name/description/input_schema).

        Returns `{"stop_reason": "tool_use"|"end_turn", "tool_calls":
        [{"id", "name", "input"}, ...], "text": str | None}` — normalized
        the same way regardless of provider, since qa.py's loop is written
        once against this shape, not against any one provider's SDK types.
        """
        ...


class MockLLM:
    """Canned responses for both duties. Any key not scripted falls through
    to a small, well-behaved default — tests script only the cases they
    care about, including a deliberately WRONG match for the D-02 twins to
    exercise the verifier's safety net.

    `adjudicate_script`: keyed by `payload["item"]["line_id"]`.
    `explain_script`: keyed by `evidence["code"]`.
    `qa_script`: keyed by any substring of the ORIGINAL question (the first
    user message); value is an ordered list of steps, each either
    `{"tool_call": {"name": ..., "input": {...}}}` or `{"answer": "..."}`.
    Consumed one step per `converse()` call for that conversation — the
    step index is derived statelessly from how many tool results already
    appear in `messages`, exactly like a real multi-turn loop would resume.
    """

    def __init__(
        self,
        adjudicate_script: dict[str, dict] | None = None,
        explain_script: dict[str, dict] | None = None,
        qa_script: dict[str, list[dict]] | None = None,
    ) -> None:
        self.adjudicate_script = adjudicate_script or {}
        self.explain_script = explain_script or {}
        self.qa_script = qa_script or {}
        self.calls: list[dict] = []  # every payload/evidence/messages seen, for test inspection

    def adjudicate(self, payload: dict) -> str:
        self.calls.append({"kind": "adjudicate", "payload": payload})
        key = payload["item"]["line_id"]
        if key in self.adjudicate_script:
            return json.dumps(self.adjudicate_script[key])
        return json.dumps(self._default_adjudication(payload))

    @staticmethod
    def _default_adjudication(payload: dict) -> dict:
        candidates = payload.get("candidates", [])
        decisive = [c for c in candidates if abs(c.get("delta_p", 1)) == 0]
        if len(decisive) == 1:
            c = decisive[0]
            return {
                "decision": "match",
                "candidate": c["batch"],
                "reason_code": None,
                "explanation": "Exactly one candidate reconstructs the target with zero delta.",
                "confidence": 0.9,
            }
        return {
            "decision": "insufficient_evidence",
            "candidate": None,
            "reason_code": None,
            "explanation": "No single candidate is decisive; abstaining rather than guessing.",
            "confidence": 0.2,
        }

    def explain(self, evidence: dict) -> str:
        self.calls.append({"kind": "explain", "evidence": evidence})
        code = evidence.get("code", "UNKNOWN")
        if code in self.explain_script:
            return json.dumps(self.explain_script[code])
        amount_p = evidence.get("amount_at_risk_p", 0)
        return json.dumps(
            {
                "explanation": f"{code}: ₹{amount_p / 100:,.2f} needs review (mock narrative).",
                "suggested_action": "Review the underlying records and resolve manually.",
            }
        )

    def converse(self, messages: list[dict], tools: list[dict], system: str) -> dict:
        self.calls.append({"kind": "converse", "messages": messages, "tools": tools})
        question = messages[0]["content"] if messages and messages[0]["role"] == "user" else ""
        steps = next((v for k, v in self.qa_script.items() if k in question), None)
        step_index = sum(1 for m in messages if m["role"] == "tool_result")

        if steps is not None and step_index < len(steps):
            step = steps[step_index]
            if "tool_call" in step:
                call = step["tool_call"]
                return {
                    "stop_reason": "tool_use",
                    "tool_calls": [{"id": f"call_{step_index}", "name": call["name"], "input": call["input"]}],
                    "text": None,
                }
            return {"stop_reason": "end_turn", "tool_calls": [], "text": step["answer"]}

        return {
            "stop_reason": "end_turn",
            "tool_calls": [],
            "text": "The data does not show enough to answer this (no scripted response for this question).",
        }


def create_llm_client(provider: str | None = None) -> LLMClient:
    """Factory for the strategy pattern: picks a concrete backend by name.

    `provider` defaults to the RECON_LLM_PROVIDER env var, then "anthropic".
    Never returns MockLLM — that's constructed explicitly by callers (tests,
    `--llm off` never calls this at all).
    """
    provider = (provider or os.environ.get("RECON_LLM_PROVIDER") or "anthropic").lower()
    if provider == "anthropic":
        from recon.llm.providers.anthropic_llm import AnthropicLLM

        return AnthropicLLM()
    if provider == "gemini":
        from recon.llm.providers.gemini_llm import GeminiLLM

        return GeminiLLM()
    raise ValueError(f"unknown LLM provider {provider!r} (expected 'anthropic' or 'gemini')")
