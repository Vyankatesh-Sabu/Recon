"""events.py — the shared on_event type for pipeline instrumentation (P6 supplement).

hop1/hop2/hop3/verifier each accept an optional `on_event: OnEvent | None`
parameter and call it at insertion time — an "exception" event from each
hop's own add_exception closure, and "match"/"rejected" events exclusively
from verifier.py (accept_or_reject is the sole place status='accepted' is
ever set, per CLAUDE.md rule 7; reject_v1 is the V1/V4 refusal path, and a
proposal thrown out by the verifier is the one outcome that used to emit
nothing at all). pipeline.run_pipeline wraps whatever the caller
passes in its own emit() closure that assigns sequence numbers and paces
delivery (--pace); a bare None here means "don't bother," so nothing pays
for event construction when nobody is listening.

Event shapes:
    {"kind": "exception", "hop": int, "exc_id": str, "code": str,
     "severity": str, "amount_at_risk_p": int, "records": [...]}
    {"kind": "match", "hop": int, "link_id": str, "tier": int | None,
     "id_a": str, "id_b": str}
    {"kind": "rejected", "hop": int, "link_id": str, "tier": int | None,
     "reason": str}
"""

from __future__ import annotations

from collections.abc import Callable

OnEvent = Callable[[dict], None]
