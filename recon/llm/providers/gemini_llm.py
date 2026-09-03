"""gemini_llm.py — RealLLM backend: Google Gemini API (SPEC §8, strategy pattern).

Reads GEMINI_API_KEY from the environment. Temperature 0. Same contract as
AnthropicLLM (recon/llm/providers/anthropic_llm.py): return raw text,
never validate it — adjudicator.py owns parsing/retry/abstention.

Verified live 2026-09-03 (first real run with a real GEMINI_API_KEY):
`adjudicate()`'s call shape works — the original `gemini-2.5-flash` model
name had since been retired ("no longer available to new users", 404),
silently swallowed by adjudicator.py's never-load-bearing catch-all and
misreported as a schema failure it never was (fixed alongside this: see
adjudicator.py's `_adjudicate_with_retry`). `converse()` (the Q&A tool-
calling path) has since been exercised live too: turn 1 worked, turn 2
failed with "Function call is missing a thought_signature in functionCall
parts" — Gemini requires its own function-call parts returned verbatim,
which `Part.from_function_call()` cannot reproduce. Fixed by carrying the
raw parts through the conversation history; see `converse` below.
"""

from __future__ import annotations

import json
import os

MODEL = "gemini-3.6-flash"

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
        """Gemini's tool-calling turn. Two things here were found by running
        it, not by reading about it: FunctionResponse.response must be a
        dict, and the model's own function-call parts must come back
        verbatim on the next turn because they carry a thought_signature.
        Both are commented at the point they matter."""
        from google.genai import types

        contents = []
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=m["content"])]))
            elif m["role"] == "assistant":
                # Gemini requires its own function-call parts back verbatim.
                # Each one carries a `thought_signature` — an opaque, encrypted
                # handle on the model's reasoning for that call — and rebuilding
                # the turn with Part.from_function_call() drops it, which the
                # API rejects on turn 2 with:
                #   400 INVALID_ARGUMENT ... Function call is missing a
                #   thought_signature in functionCall parts ...
                # So the raw parts are carried through qa.py's history (see
                # `raw_parts` below) and replayed as they arrived. The
                # from_function_call() path remains for any history that
                # predates them — a MockLLM turn, or another provider's.
                raw_parts = m.get("raw_parts")
                if raw_parts:
                    parts = [types.Part.model_validate(part) for part in raw_parts]
                else:
                    parts = [
                        types.Part.from_function_call(name=block["name"], args=block["input"])
                        for block in m["content"]
                        if block["type"] == "tool_use"
                    ]
                contents.append(types.Content(role="model", parts=parts))
            elif m["role"] == "tool_result":
                c = m["content"]
                # Found live 2026-09-03: Gemini's FunctionResponse.response
                # requires a dict — recon/llm/tools.py's list_exceptions()
                # returns a bare list, and the SDK rejected it outright
                # (pydantic ValidationError) rather than degrading. Every
                # tool result gets wrapped the same way regardless of its
                # actual shape, so this holds for any future tool too.
                result = c["result"] if isinstance(c["result"], dict) else {"result": c["result"]}
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(name=c["name"], response=result)],
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
        return {
            "stop_reason": "tool_use" if tool_calls else "end_turn",
            "tool_calls": tool_calls,
            "text": text,
            # The turn exactly as Gemini produced it, thought_signature and
            # all. Dumped in python mode, never JSON: thought_signature is
            # `bytes`, and a JSON round-trip would corrupt it.
            "raw_parts": [p.model_dump(exclude_none=True) for p in parts],
        }
