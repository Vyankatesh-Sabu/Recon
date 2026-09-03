"""adjudicator.py — tier-4 adjudication behind the verifier (SPEC §8).

Builds the payload — every delta is computed by us; the model never sees a
raw table and never does arithmetic. Parses the response into `Adjudication`
(extra="forbid"); one retry on schema violation, then treated as abstention.
A "match" decision on `tier2_unexplained_credit` residue creates a PROPOSED
match_link (hop=2, tier=4) — it is re-verified by verifier.py exactly like
any hop2 proposal (V1 batch check + V2 uniqueness), and the overall run's V5
clearing control still applies unchanged. A tier-4 proposal the verifier
rejects leaves the original exception (UNEXPLAINED_BANK_CREDIT) open —
"demoted" back to standing, never silently dropped.

A "match" decision on `tier2_ambiguous` residue is different in kind, not
just degree: a wrong pick there still passes V1's arithmetic re-check (every
candidate in a genuine "Multiple" is, by definition, equally valid
arithmetically), so V1+V2 cannot be relied on to catch it — proven by
testing, not assumed (a scripted-wrong MockLLM match on the D-02 twins was
accepted by the verifier before this override existed). SPEC §6.3's refusal
("Do NOT PICK ONE. Not even by earliest date.") is therefore enforced
unconditionally, here, before such a decision ever becomes a proposal —
never overridable by tier 4, however confident the model claims to be.

Tier 4 runs only on hop2's residue: bank lines that hop2 couldn't resolve
at tier 1 or tier 2 (both "Multiple" -> AMBIGUOUS_SETTLEMENT and
"NoSolution" -> UNEXPLAINED_BANK_CREDIT — "abstaining is a correct and
rewarded outcome" is exactly the expected answer for the former).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from pydantic import ValidationError

from recon.llm.client import Adjudication, ExceptionNarrative, LLMClient

MAX_CANDIDATES = 5
_RESOLVABLE_OUTCOMES = ("tier2_ambiguous", "tier2_unexplained_credit")
_CANDIDATE_KEY_BY_LABEL = {"candidate_a": "subset_a", "candidate_b": "subset_b"}


@dataclass
class Tier4Stats:
    calls: int = 0  # total adjudicate() calls made
    proposed: int = 0  # calls that resulted in a match_link proposal ("match" + resolvable candidate)
    abstained: int = 0  # no_match / insufficient_evidence / unparseable / unresolvable candidate
    accepted: int = 0  # filled in by finalize_tier4_stats, after the verifier's 2nd pass
    rejected: int = 0
    call_log: list[dict] = field(default_factory=list)


def _build_payload(entry: dict, bank_line_row: tuple) -> dict:
    line_id, value_date, narration, credit_p = bank_line_row
    evidence = entry["evidence"]
    candidates: list[dict] = []

    if entry["outcome"] == "tier2_ambiguous":
        for label, key in (("candidate_a", "subset_a"), ("candidate_b", "subset_b")):
            subset = evidence[key]
            net_p = sum(row["net_p"] for row in subset)
            candidates.append(
                {
                    "batch": label,
                    "rows": len(subset),
                    "net_p": net_p,
                    "delta_p": net_p - credit_p,
                    "date_gap_bdays": 0,
                    "narration_tokens_matched": [],
                }
            )
        failed_checks = ["multiple disjoint subsets sum to the target within tolerance"]
    else:  # tier2_unexplained_credit
        failed_checks = ["no subset within tolerance"]
        if evidence.get("reason") == "over_cap":
            failed_checks.append("candidate pool exceeded max_items")

    return {
        "task": "hop2_unresolved_bank_credit",
        "item": {"line_id": line_id, "value_date": value_date, "credit_p": credit_p, "narration": narration},
        "candidates": candidates[:MAX_CANDIDATES],
        "failed_checks": failed_checks,
        "instruction": "Choose a candidate only if evidence is decisive. Abstaining is a correct and rewarded outcome.",
    }


def _resolve_candidate_rows(entry: dict, candidate_label: str | None) -> list[dict] | None:
    if candidate_label is None:
        return None
    key = _CANDIDATE_KEY_BY_LABEL.get(candidate_label)
    if key is None:
        return None
    return entry["evidence"].get(key)


def _strip_code_fence(raw: str) -> str:
    """Despite every provider's system prompt saying "ONLY a JSON object,
    no other text," a model will still sometimes wrap it in a markdown
    code fence (found live 2026-09-03: Gemini did this on its very first
    real adjudication call, producing `Invalid JSON: expected value at
    line 1 column 1`). Strips a leading/trailing ``` or ```json fence if
    present; a no-op on already-bare JSON. Applies to every provider —
    this isn't Gemini-specific behavior, just more likely to surface once
    something actually calls a real model instead of MockLLM."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    return text


def _adjudicate_with_retry(client: LLMClient, payload: dict) -> tuple[Adjudication | None, str | None]:
    """Returns (adjudication, failure_reason) — failure_reason is None on
    success, else a short label distinguishing WHY it abstained. Found
    live (2026-09-03, first real Gemini run): a stale/retired model name
    404'd on every single call, and the broad except below swallowed it
    into an indistinguishable "schema failure" — cost real time to
    diagnose. The retry-then-abstain BEHAVIOR here is unchanged (CLAUDE.md
    rule 5: the LLM is never load-bearing — no API key, a network error, a
    rate limit, none of those may crash the pipeline), only the label is
    now honest about which of "the model's JSON didn't validate" vs "the
    client itself raised" actually happened.
    """
    last_reason = "no_response"
    for _ in range(2):  # one retry on schema violation
        try:
            raw = client.adjudicate(payload)
        except Exception as exc:
            last_reason = f"provider_error: {exc}"
            continue
        try:
            return Adjudication.model_validate_json(_strip_code_fence(raw)), None
        except (ValidationError, ValueError) as exc:
            last_reason = f"schema_failure: {exc}"
            continue
    return None, last_reason  # treated as abstention


def run_tier4(
    conn: sqlite3.Connection, run_id: str, evidence_log: list[dict], client: LLMClient
) -> Tier4Stats:
    stats = Tier4Stats()
    link_seq = 0

    def next_link_id() -> str:
        nonlocal link_seq
        link_seq += 1
        return f"{run_id}-ML4-{link_seq:04d}"

    residue = [e for e in evidence_log if e["outcome"] in _RESOLVABLE_OUTCOMES]
    for entry in sorted(residue, key=lambda e: e["bank_line"]):
        line_id = entry["bank_line"]
        bank_line_row = conn.execute(
            "SELECT line_id, value_date, narration, credit_p FROM bank_lines WHERE line_id = ?", (line_id,)
        ).fetchone()
        if bank_line_row is None:
            continue

        payload = _build_payload(entry, bank_line_row)
        stats.calls += 1
        adjudication, failure_reason = _adjudicate_with_retry(client, payload)
        stats.call_log.append(
            {
                "line_id": line_id,
                "payload": payload,
                "decision": adjudication.decision if adjudication else f"abstained ({failure_reason})",
            }
        )

        if adjudication is None or adjudication.decision != "match":
            stats.abstained += 1
            continue

        if entry["outcome"] == "tier2_ambiguous":
            # SPEC §6.3: "Do NOT PICK ONE. Not even by earliest date." A
            # wrong pick here still passes V1's arithmetic re-check (that's
            # what makes it ambiguous — every candidate is equally valid
            # arithmetically), so V1+V2 alone cannot catch it; the refusal
            # has to be enforced here, unconditionally, before it ever
            # becomes a proposal. A "match" decision on genuine ambiguity
            # is always wrong by construction, however confident the model
            # is — treated as an (overridden) abstention, not a proposal.
            stats.call_log[-1]["decision"] = f"{adjudication.decision} (overridden: ambiguous refusal is absolute)"
            stats.abstained += 1
            continue

        subset = _resolve_candidate_rows(entry, adjudication.candidate)
        if not subset:
            stats.abstained += 1
            continue

        stats.proposed += 1
        evidence_with_llm = {**entry["evidence"], "llm_adjudication": adjudication.model_dump()}
        for row in subset:
            conn.execute(
                "INSERT INTO match_link "
                "(link_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, reason, evidence, run_id) "
                "VALUES (?, 2, 'gw', ?, 'bank', ?, 4, ?, 'proposed', 'tier4_llm_match', ?, ?)",
                (
                    next_link_id(),
                    row["id"],
                    line_id,
                    adjudication.confidence,
                    json.dumps(evidence_with_llm),
                    run_id,
                ),
            )

    conn.commit()
    return stats


def finalize_tier4_stats(conn: sqlite3.Connection, run_id: str, stats: Tier4Stats) -> None:
    """Call after the verifier's second pass has processed tier-4 proposals —
    fills in accepted/rejected so the report can print
    "LLM proposed N, verifier accepted A, rejected R, abstained X", and
    stamps each call in the log with what the verifier did with it.

    The `verifier_outcome`/`verifier_reason` keys are what makes the
    adjudication panel (UI_SPEC §2.1) honest: "the model said X, the
    verifier said Y" on the same row, per call. A call the model abstained
    on produced no proposal, so it has no outcome to report and the keys
    stay absent — that's the difference between "the verifier let it
    through" and "there was nothing to check", and the panel renders the
    second as "—" rather than inventing a verdict."""
    by_line: dict[str, tuple[str, str | None]] = {}
    for id_b, status, reason in conn.execute(
        "SELECT id_b, status, reason FROM match_link WHERE run_id = ? AND tier = 4 GROUP BY id_b", (run_id,)
    ):
        by_line[id_b] = (status, reason)
        if status == "accepted":
            stats.accepted += 1
        elif status == "rejected":
            stats.rejected += 1

    for call in stats.call_log:
        outcome = by_line.get(call["line_id"])
        if outcome is None:
            continue
        call["verifier_outcome"], call["verifier_reason"] = outcome


def resolve_exceptions_for_accepted_tier4(conn: sqlite3.Connection, run_id: str) -> int:
    """A tier-4 match the verifier accepted resolves the original
    AMBIGUOUS_SETTLEMENT / UNEXPLAINED_BANK_CREDIT exception (status ->
    'resolved') rather than deleting or rewriting it. A rejected tier-4
    match leaves the exception exactly as it was — open, standing —
    which is how "rejection demotes to the original exception" falls out
    naturally: nothing here ever touches an exception the verifier rejected.
    """
    accepted_lines = [
        id_b
        for id_b, status in conn.execute(
            "SELECT id_b, status FROM match_link WHERE run_id = ? AND tier = 4 GROUP BY id_b", (run_id,)
        )
        if status == "accepted"
    ]
    resolved = 0
    for line_id in accepted_lines:
        cur = conn.execute(
            "UPDATE exceptions SET status = 'resolved' WHERE run_id = ? AND status = 'open' "
            "AND code IN ('AMBIGUOUS_SETTLEMENT', 'UNEXPLAINED_BANK_CREDIT') AND records LIKE ?",
            (run_id, f'%"{line_id}"%'),
        )
        resolved += cur.rowcount
    conn.commit()
    return resolved


def _narrate_with_retry(client: LLMClient, evidence: dict) -> ExceptionNarrative | None:
    for _ in range(2):  # one retry on schema violation
        try:
            raw = client.explain(evidence)
            return ExceptionNarrative.model_validate_json(_strip_code_fence(raw))
        except (ValidationError, ValueError):
            continue
        except Exception:
            continue  # never load-bearing (CLAUDE.md rule 5) — see _adjudicate_with_retry
    return None  # keep the template


def narrate_exceptions(conn: sqlite3.Connection, run_id: str, client: LLMClient) -> int:
    """Second LLM duty (SPEC §8): regenerate explanation/suggested_action
    for every open exception from its own row as structured evidence.
    `--llm off` never calls this — the templated strings hop1/hop2/hop3
    already wrote are the honest, always-available fallback, and a schema
    failure here just keeps that template rather than blanking it out.
    """
    narrated = 0
    rows = conn.execute(
        "SELECT exc_id, code, severity, records, amount_at_risk_p, age_days, explanation, suggested_action "
        "FROM exceptions WHERE run_id = ? AND status = 'open'",
        (run_id,),
    ).fetchall()
    for exc_id, code, severity, records_json, amount_at_risk_p, age_days, old_explanation, old_action in rows:
        evidence = {
            "code": code,
            "severity": severity,
            "records": json.loads(records_json),
            "amount_at_risk_p": amount_at_risk_p,
            "age_days": age_days,
            "template_explanation": old_explanation,
            "template_suggested_action": old_action,
        }
        narrative = _narrate_with_retry(client, evidence)
        if narrative is None:
            continue
        conn.execute(
            "UPDATE exceptions SET explanation = ?, suggested_action = ? WHERE exc_id = ?",
            (narrative.explanation, narrative.suggested_action, exc_id),
        )
        narrated += 1
    conn.commit()
    return narrated
