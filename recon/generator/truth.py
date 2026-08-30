"""truth.py — ground_truth.json writer (SPEC §5.4).

`GroundTruth` accumulates links/exceptions/in_transit entries as `world.py`
and `defects.py` build and mutate the world, in construction order — never
derived after the fact by re-inspecting the finished data. Everything here
is plain lists/dicts (no sets) so JSON output is byte-identical across runs
of the same seed (CLAUDE.md rule 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class Link:
    hop: int  # 1|2|3
    a: tuple[str, str]  # (src, id)
    b: tuple[str, str]

    def to_json(self) -> dict:
        return {"hop": self.hop, "a": list(self.a), "b": list(self.b)}


@dataclass
class ExpectedException:
    code: str
    records: list[tuple[str, str]]
    amount_at_risk_p: int
    note: str = ""

    def to_json(self) -> dict:
        out = {
            "code": self.code,
            "records": [{"src": src, "id": rid} for src, rid in self.records],
            "amount_at_risk_p": self.amount_at_risk_p,
        }
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class InTransitEntry:
    batch: str
    expected_settlement: str  # ISO date

    def to_json(self) -> dict:
        return {"batch": self.batch, "expected_settlement": self.expected_settlement}


@dataclass
class GroundTruth:
    seed: int
    links: list[Link] = field(default_factory=list)
    exceptions: list[ExpectedException] = field(default_factory=list)
    in_transit: list[InTransitEntry] = field(default_factory=list)

    def add_link(self, hop: int, a: tuple[str, str], b: tuple[str, str]) -> None:
        self.links.append(Link(hop=hop, a=a, b=b))

    def remove_links_for(self, src: str, rid: str) -> None:
        """Drop any link touching (src, rid) on either side — used when a defect
        makes a previously-correct pairing genuinely unknowable (e.g. D-02)."""
        self.links = [
            link
            for link in self.links
            if not (link.a == (src, rid) or link.b == (src, rid))
        ]

    def add_exception(
        self,
        code: str,
        records: list[tuple[str, str]],
        amount_at_risk_p: int,
        note: str = "",
    ) -> None:
        self.exceptions.append(
            ExpectedException(code=code, records=records, amount_at_risk_p=amount_at_risk_p, note=note)
        )

    def add_in_transit(self, batch: str, expected_settlement: date) -> None:
        self.in_transit.append(
            InTransitEntry(batch=batch, expected_settlement=expected_settlement.isoformat())
        )

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "links": [link.to_json() for link in self.links],
            "exceptions": [exc.to_json() for exc in self.exceptions],
            "in_transit": [entry.to_json() for entry in self.in_transit],
        }


def write_ground_truth(truth: GroundTruth, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(truth.to_dict(), f, indent=2, ensure_ascii=False, sort_keys=False)
        f.write("\n")
