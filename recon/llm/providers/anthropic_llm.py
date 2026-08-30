"""anthropic_llm.py — RealLLM backend: Anthropic API (SPEC §8).

Reads ANTHROPIC_API_KEY from the environment via the SDK's default
credential resolution (`anthropic.Anthropic()`, no key passed explicitly).
SPEC asks for temperature 0; the current model generation (Claude Opus 5)
has removed the `temperature`/`top_p`/`top_k` sampling params from the
Messages API entirely (confirmed against the installed SDK — passing
`temperature` raises `TypeError`, not just a no-op), since adaptive
thinking replaces manual sampling control. Determinism is instead left to
the model's own low-variance behavior on a structured, single-answer
classification task — there's nothing to configure toward it. Never
load-bearing (CLAUDE.md rule 5): adjudicator.py treats any failure here
(missing key, network error, malformed JSON) as an abstention after one
retry, never as a pipeline crash.
"""

from __future__ import annotations

import json

MODEL = "claude-opus-5"
MAX_TOKENS = 1024

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


class AnthropicLLM:
    """Anthropic API backend for LLMClient (strategy pattern, SPEC §8)."""

    def __init__(self, model: str = MODEL) -> None:
        import anthropic  # deferred: only imported when this backend is actually selected

        self._client = anthropic.Anthropic()
        self._model = model

    def _complete(self, system: str, user_payload: dict) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": json.dumps(user_payload)}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def adjudicate(self, payload: dict) -> str:
        return self._complete(_ADJUDICATE_SYSTEM, payload)

    def explain(self, evidence: dict) -> str:
        return self._complete(_EXPLAIN_SYSTEM, evidence)
