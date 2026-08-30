"""gemini_llm.py — RealLLM backend: Google Gemini API (SPEC §8, strategy pattern).

Reads GEMINI_API_KEY from the environment. Temperature 0. Same contract as
AnthropicLLM (recon/llm/providers/anthropic_llm.py): return raw text,
never validate it — adjudicator.py owns parsing/retry/abstention.

NOTE: unlike AnthropicLLM, this file wasn't checked against a live SDK
reference in this session — double-check `google-genai`'s exact call shape
(package name, `Client`/`generate_content` signature) against current docs
before relying on this in production; it isn't exercised by any test or
gate here (only MockLLM is).
"""

from __future__ import annotations

import json
import os

MODEL = "gemini-2.5-flash"

_ADJUDICATE_SYSTEM = (
    "You are a reconciliation adjudicator. You are given pre-computed deltas "
    "and candidates; you never see raw data tables and must never do your "
    "own arithmetic. Choose a candidate only if the evidence is decisive. "
    "Abstaining is a correct and rewarded outcome. Respond with ONLY a JSON "
    'object matching this schema, no other text: {"decision": '
    '"match"|"no_match"|"insufficient_evidence", "candidate": string|null, '
    '"reason_code": string|null, "explanation": string (<=2 sentences), '
    '"confidence": number 0..1}.'
)

_EXPLAIN_SYSTEM = (
    "You write plain-language explanations of reconciliation exceptions for "
    "a finance team, from structured evidence only. Respond with ONLY a "
    'JSON object matching this schema, no other text: {"explanation": '
    'string, "suggested_action": string}.'
)


class GeminiLLM:
    """Gemini API backend for LLMClient (strategy pattern, SPEC §8)."""

    def __init__(self, model: str = MODEL) -> None:
        from google import genai  # deferred: only imported when this backend is actually selected

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def _complete(self, system: str, user_payload: dict) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=json.dumps(user_payload),
            config=types.GenerateContentConfig(system_instruction=system, temperature=0),
        )
        return response.text or ""

    def adjudicate(self, payload: dict) -> str:
        return self._complete(_ADJUDICATE_SYSTEM, payload)

    def explain(self, evidence: dict) -> str:
        return self._complete(_EXPLAIN_SYSTEM, evidence)

    def converse(self, messages: list[dict], tools: list[dict], system: str) -> dict:
        """NOTE: best-effort, same caveat as the module docstring — Gemini's
        function-calling shape (Tool/FunctionDeclaration, Content/Part,
        function_call/function_response) is reproduced from training
        knowledge, not verified against a live SDK reference this session."""
        from google.genai import types

        contents = []
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=m["content"])]))
            elif m["role"] == "assistant":
                parts = [
                    types.Part.from_function_call(name=block["name"], args=block["input"])
                    for block in m["content"]
                    if block["type"] == "tool_use"
                ]
                contents.append(types.Content(role="model", parts=parts))
            elif m["role"] == "tool_result":
                c = m["content"]
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(name=c["name"], response=c["result"])],
                    )
                )

        gemini_tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["input_schema"])
                    for t in tools
                ]
            )
        ]
        response = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0, tools=gemini_tools),
        )
        parts = response.candidates[0].content.parts if response.candidates else []
        tool_calls = [
            {"id": f"call_{i}", "name": p.function_call.name, "input": dict(p.function_call.args)}
            for i, p in enumerate(parts)
            if getattr(p, "function_call", None)
        ]
        text = "".join(p.text for p in parts if getattr(p, "text", None)) or None
        return {"stop_reason": "tool_use" if tool_calls else "end_turn", "tool_calls": tool_calls, "text": text}
