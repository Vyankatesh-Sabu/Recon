"""io.py — writes the generator's World to data/*.csv (SPEC §5.4).

Kept separate from world.py (which only builds the in-memory World) and
truth.py (which only writes ground_truth.json) — same "new small module,
single job" precedent as recon/db.py. Row order is whatever order the lists
are in (deterministic given a fixed seed and construction order), never
re-sorted here, so output is byte-identical across runs of the same seed.
"""

from __future__ import annotations

import csv
from pathlib import Path

from recon.generator.world import World


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def write_csvs(world: World, out_dir: Path | str) -> None:
    out_dir = Path(out_dir)

    _write_csv(
        out_dir / "orders.csv",
        ["order_id", "customer", "amount_p", "method", "status", "created_on"],
        [
            [o.order_id, o.customer, str(o.amount_p), o.method, o.status, o.created_on.isoformat()]
            for o in world.orders
        ],
    )

    _write_csv(
        out_dir / "gateway.csv",
        [
            "payment_id",
            "order_id",
            "kind",
            "amount_p",
            "fee_p",
            "gst_p",
            "method",
            "captured_on",
            "settlement_id",
            "utr",
        ],
        [
            [
                p.payment_id,
                p.order_id or "",
                p.kind,
                p.raw_amount_override if p.raw_amount_override is not None else str(p.amount_p),
                str(p.fee_p),
                str(p.gst_p),
                p.method,
                p.captured_on.isoformat(),
                p.settlement_id or "",
                p.utr or "",
            ]
            for p in world.gw_payments
        ],
    )

    _write_csv(
        out_dir / "bank.csv",
        ["line_id", "value_date", "narration", "credit_p", "debit_p"],
        [
            [b.line_id, b.value_date.isoformat(), b.narration, str(b.credit_p), str(b.debit_p)]
            for b in world.bank_lines
        ],
    )

    _write_csv(
        out_dir / "gl.csv",
        ["voucher_no", "line_no", "entry_date", "account", "debit_p", "credit_p", "memo"],
        [
            [g.voucher_no, str(g.line_no), g.entry_date.isoformat(), g.account, str(g.debit_p), str(g.credit_p), g.memo]
            for g in world.gl_entries
        ],
    )
