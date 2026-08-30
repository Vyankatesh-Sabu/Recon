"""api.py — FastAPI app exposing the Q&A endpoint (SPEC §9).

POST /ask {"question": str} -> {"answer": str, "tool_calls": [...], "record_ids": [...]}.
The LLM client and DB path are FastAPI dependencies specifically so tests
(tests/unit/test_api.py) and gate_p5.py can override them with a MockLLM
and a seeded temp database via `app.dependency_overrides`, without ever
touching a real provider or `data/recon.db`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from recon import db as recon_db
from recon.llm import qa
from recon.llm.client import LLMClient, create_llm_client

app = FastAPI(title="RECON-4 Q&A")


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    tool_calls: list[dict]
    record_ids: list[str]


def get_llm_client() -> LLMClient:
    return create_llm_client()


def get_db_path() -> Path:
    return recon_db.DB_PATH


@app.post("/ask", response_model=AskResponse)
def ask(
    request: AskRequest,
    client: LLMClient = Depends(get_llm_client),
    db_path: Path = Depends(get_db_path),
) -> AskResponse:
    conn = recon_db.connect(db_path)
    try:
        result = qa.answer_question(conn, request.question, client)
        return AskResponse(**result)
    finally:
        conn.close()
