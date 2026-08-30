"""defects.py — defect injectors, one function per code D-01..D-14 (SPEC §5.3).

Each `apply_dNN(world, truth, rng, ctx)` selects a suitable existing record
from the already-built clean `world` (via filtering + `rng.choice` among
candidates), mutates it, and records the expected outcome onto `truth` — a
correct link stays or is added, or an exception is raised, per the SPEC §5.3
table. `_Ctx` tracks which days/orders earlier injectors already touched so
two defects never collide on the same record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import config
from recon import busdays, moneymath
from recon.generator.truth import GroundTruth
from recon.generator.world import (
    BankLine,
    GlEntry,
    GwPayment,
    World,
    add_voucher,
    draw_amount_p,
    is_settled,
    next_payment_id,
    resync_day_capture_voucher,
    resync_settlement,
)


@dataclass
class _Ctx:
    used_days: set[date] = field(default_factory=set)
    used_orders: set[str] = field(default_factory=set)


def _business_days() -> list[date]:
    return busdays.business_days_in_range(config.DATE_FROM, config.DATE_TO)


def _group_by_day(world: World) -> dict[date, list[GwPayment]]:
    by_day: dict[date, list[GwPayment]] = {}
    for p in world.gw_payments:
        by_day.setdefault(p.captured_on, []).append(p)
    return by_day


def _settled_days(world: World) -> list[date]:
    days = {p.captured_on for p in world.gw_payments}
    return sorted(d for d in days if is_settled(world, d))


def _find_refund_voucher(world: World, refund: GwPayment) -> str | None:
    amt = abs(refund.amount_p)
    for g in world.gl_entries:
        if g.entry_date == refund.captured_on and g.account == "SALES_RETURNS" and g.debit_p == amt:
            return g.voucher_no
    return None


def apply_d01(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    by_day = _group_by_day(world)
    lo, hi = config.D01_BATCH_ROWS_RANGE
    candidates = [
        day
        for day, rows in by_day.items()
        if day not in ctx.used_days
        and is_settled(world, day)
        and lo <= len(rows) <= hi
        and sum(1 for p in rows if p.kind == "refund") == 1
    ]
    if not candidates:
        raise RuntimeError("D-01: no candidate batch found (settled, 5-7 rows, exactly 1 refund)")
    day = rng.choice(sorted(candidates))
    ctx.used_days.add(day)
    for p in by_day[day]:
        p.settlement_id = None
        p.utr = None
    line_id = f"setl_{day:%m%d}"
    bank_line = next(b for b in world.bank_lines if b.line_id == line_id)
    bank_line.narration = "NEFT CR AXIS BANK SETTLEMENT"
    # Ground truth: links from clean build stay correct as-is — D-01 only
    # strips the tier-1 shortcut (settlement_id/utr); the right answer is
    # unchanged, hop-2 must recover it via tier-2 subset-sum instead.


def apply_d02(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    by_day = _group_by_day(world)
    candidates = []
    for day, rows in by_day.items():
        if day in ctx.used_days or not is_settled(world, day):
            continue
        if any(p.kind != "capture" for p in rows):
            continue  # keep the split simple: pure-capture day only
        if len(rows) < 4:
            continue
        if not any(p.method == "upi" for p in rows):
            continue  # need a zero-fee row so forcing equality is exact
        candidates.append(day)
    if not candidates:
        raise RuntimeError("D-02: no candidate day found (settled, >=4 pure captures, includes a upi row)")
    day = rng.choice(sorted(candidates))
    ctx.used_days.add(day)

    # Isolate one upi row (zero fee/gst -> net == amount_p) into subset_b on
    # its own; everything else in the day's batch goes into subset_a. This
    # guarantees net_a > 0 (a real multi-capture settlement sum), so the
    # single adjusted amount that forces net_b == net_a is always positive.
    rows = sorted(by_day[day], key=lambda p: p.payment_id)
    upi_row = next(p for p in rows if p.method == "upi")
    subset_b = [upi_row]
    subset_a = [p for p in rows if p is not upi_row]

    net_a = moneymath.settlement_net_p([p.settlement_contribution_p for p in subset_a], [])
    new_amount = net_a  # upi: net == amount_p, no fee/gst to solve for
    assert new_amount > 0, "D-02: subset_a of a settled day must have positive net"
    upi_row.amount_p = new_amount
    upi_row.fee_p = 0
    upi_row.gst_p = 0
    order = next(o for o in world.orders if o.order_id == upi_row.order_id)
    order.amount_p = new_amount  # keep hop-1 pairing exact, not a partial-capture mismatch

    net_b = moneymath.settlement_net_p([p.settlement_contribution_p for p in subset_b], [])
    assert net_a == net_b, f"D-02: subsets not equal after adjustment ({net_a} != {net_b})"

    # The original single settlement for this day never existed in this
    # form — remove it (bank line, voucher, and the links clean-build made
    # for it) before laying down the two genuinely ambiguous ones.
    orig_line_id = f"setl_{day:%m%d}"
    world.remove_bank_line(orig_line_id)
    world.remove_voucher(f"V-{day:%Y%m%d}-SETL")
    truth.remove_links_for("bank", orig_line_id)

    settle_date = busdays.add_bdays(day, config.SETTLEMENT_LAG_BDAYS)
    scrub_narrations = ("NEFT CR HDFC BANK SETTLEMENT", "NEFT CR ICICI BANK SETTLEMENT")
    line_ids = (f"bl_d02_{day:%m%d}a", f"bl_d02_{day:%m%d}b")
    for subset, net, line_id, narration, suffix in zip(
        (subset_a, subset_b), (net_a, net_b), line_ids, scrub_narrations, ("A", "B")
    ):
        world.bank_lines.append(
            BankLine(line_id=line_id, value_date=settle_date, narration=narration, credit_p=net, debit_p=0)
        )
        for p in subset:
            p.settlement_id = None
            p.utr = None
        fee_total = sum(p.fee_p for p in subset)
        gst_total = sum(p.gst_p for p in subset)
        receivable_total = sum(p.amount_p for p in subset)
        add_voucher(
            world,
            f"V-{day:%Y%m%d}-SETL-{suffix}",
            settle_date,
            [
                ("BANK", net, 0, "settlement (ambiguous)"),
                ("FEE_EXPENSE", fee_total, 0, "settlement (ambiguous)"),
                ("INPUT_GST", gst_total, 0, "settlement (ambiguous)"),
                ("PG_RECEIVABLE", 0, receivable_total, "settlement (ambiguous)"),
            ],
        )
    resync_day_capture_voucher(world, day)  # one order's amount changed

    truth.add_exception(
        "AMBIGUOUS_SETTLEMENT",
        records=[("bank", line_ids[0]), ("bank", line_ids[1])],
        amount_at_risk_p=net_a + net_b,
        note="two settlements with identical net amounts on the same value_date; "
        "engine must refuse — selecting either is a false match",
    )


def apply_d03(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    refunds = sorted((p for p in world.gw_payments if p.kind == "refund"), key=lambda p: p.payment_id)
    chosen = rng.sample(refunds, config.DEFECT_COUNTS["D-03"])
    for refund in chosen:
        voucher_no = _find_refund_voucher(world, refund)
        if voucher_no is None:
            raise RuntimeError(f"D-03: no GL voucher found for refund {refund.payment_id}")
        world.remove_voucher(voucher_no)
        truth.add_exception(
            "UNLINKED_REFUND",
            records=[("gw", refund.payment_id)],
            amount_at_risk_p=abs(refund.amount_p),
        )


def apply_d04(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    candidates = [
        day
        for day in _settled_days(world)
        if day not in ctx.used_days
        and sum(p.fee_p for p in world.gw_payments if p.captured_on == day and p.kind == "capture") > 0
    ]
    if not candidates:
        raise RuntimeError("D-04: no settled day with nonzero fee found")
    day = rng.choice(candidates)
    ctx.used_days.add(day)
    voucher_no = f"V-{day:%Y%m%d}-SETL"
    lines = world.voucher_lines(voucher_no)
    bank_gl = next(g for g in lines if g.account == "BANK")
    fee_gl = next(g for g in lines if g.account == "FEE_EXPENSE")
    gst_gl = next(g for g in lines if g.account == "INPUT_GST")
    receivable_gl = next(g for g in lines if g.account == "PG_RECEIVABLE")
    lumped = fee_gl.debit_p + gst_gl.debit_p
    settle_date = lines[0].entry_date
    world.remove_voucher(voucher_no)
    add_voucher(
        world,
        voucher_no,
        settle_date,
        [
            ("BANK", bank_gl.debit_p, 0, "settlement"),
            ("BANK_CHARGES", lumped, 0, "fee+gst lumped (defect)"),
            ("PG_RECEIVABLE", 0, receivable_gl.credit_p, "settlement"),
        ],
    )
    # hop-3 link from clean build still points at this voucher_no — stays valid.
    truth.add_exception(
        "GL_DECOMPOSITION_FAIL",
        records=[("gl", voucher_no)],
        amount_at_risk_p=lumped,
        note="fee and GST lumped into BANK_CHARGES instead of FEE_EXPENSE/INPUT_GST; "
        "the input-tax credit on the GST component is lost",
    )


def apply_d05(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    candidates = [d for d in _settled_days(world) if d not in ctx.used_days]
    if not candidates:
        raise RuntimeError("D-05: no settled day available")
    day = rng.choice(candidates)
    ctx.used_days.add(day)
    voucher_no = f"V-{day:%Y%m%d}-SETL"
    line_id = f"setl_{day:%m%d}"
    world.remove_voucher(voucher_no)
    truth.remove_links_for("gl", voucher_no)
    bank_line = next(b for b in world.bank_lines if b.line_id == line_id)
    truth.add_exception(
        "GL_MISSING",
        records=[("bank", line_id)],
        amount_at_risk_p=bank_line.credit_p,
    )


def apply_d06(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    # Exclude days already restructured by a day-level defect (D-01/D-02/
    # D-04/D-05/D-10/D-11/D-13): those defects' bank lines aren't named
    # "setl_<day>", so is_settled()/resync_settlement() wouldn't notice a
    # row added here and their carefully-built invariants would silently
    # go stale.
    days = [d for d in _business_days() if d not in ctx.used_days]
    for _ in range(config.DEFECT_COUNTS["D-06"]):
        day = rng.choice(days)
        method = rng.choice(["card", "upi", "nb"])
        amount_p = draw_amount_p(rng)
        fee = moneymath.fee_p(amount_p, config.FEE_BPS[method])
        gst = moneymath.gst_p(fee, config.GST_BPS_ON_FEE)
        payment = GwPayment(
            payment_id=next_payment_id(world),
            order_id=None,
            kind="capture",
            amount_p=amount_p,
            fee_p=fee,
            gst_p=gst,
            method=method,
            captured_on=day,
        )
        world.gw_payments.append(payment)
        settled = is_settled(world, day)
        if settled:
            sibling = next(p for p in world.gw_payments if p.captured_on == day and p.utr)
            payment.settlement_id = sibling.settlement_id
            payment.utr = sibling.utr
        else:
            payment.settlement_id = f"setl_{day:%m%d}"
        resync_day_capture_voucher(world, day)
        if settled:
            resync_settlement(world, day)
            truth.add_link(2, ("gw", payment.payment_id), ("bank", f"setl_{day:%m%d}"))
        truth.add_exception(
            "ORPHAN_PAYMENT",
            records=[("gw", payment.payment_id)],
            amount_at_risk_p=amount_p,
        )


def apply_d07(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    bank_line = next(b for b in world.bank_lines if b.line_id == "bl_direct_neft")
    world.remove_voucher("V-DIRECT-NEFT")
    truth.remove_links_for("gl", "V-DIRECT-NEFT")
    truth.remove_links_for("bank", "bl_direct_neft")
    bank_line.narration = "NEFT CR HDFC KALYANI ENTERPRISES"
    bank_line.credit_p = config.D07_AMOUNT_P
    truth.add_exception(
        "UNEXPLAINED_BANK_CREDIT",
        records=[("bank", "bl_direct_neft")],
        amount_at_risk_p=config.D07_AMOUNT_P,
        note="no gateway record, no GL entry; route to invoice queue",
    )


def apply_d08(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    # See apply_d06's comment: avoid both the original's day and the
    # duplicate's day landing on a day already restructured elsewhere.
    captures = sorted(
        (
            p
            for p in world.gw_payments
            if p.kind == "capture"
            and p.order_id
            and p.order_id not in ctx.used_orders
            and p.captured_on not in ctx.used_days
            and busdays.add_bdays(p.captured_on, 1) not in ctx.used_days
        ),
        key=lambda p: p.payment_id,
    )
    original = rng.choice(captures)
    ctx.used_orders.add(original.order_id)
    dup_day = busdays.add_bdays(original.captured_on, 1)
    if dup_day > config.DATE_TO:
        dup_day = original.captured_on
    duplicate = GwPayment(
        payment_id=next_payment_id(world),
        order_id=original.order_id,
        kind="capture",
        amount_p=original.amount_p,
        fee_p=original.fee_p,
        gst_p=original.gst_p,
        method=original.method,
        captured_on=dup_day,
    )
    world.gw_payments.append(duplicate)
    settled = is_settled(world, dup_day)
    if settled:
        sibling = next(p for p in world.gw_payments if p.captured_on == dup_day and p.utr)
        duplicate.settlement_id = sibling.settlement_id
        duplicate.utr = sibling.utr
    else:
        duplicate.settlement_id = f"setl_{dup_day:%m%d}"
    resync_day_capture_voucher(world, dup_day)
    if settled:
        resync_settlement(world, dup_day)
        truth.add_link(2, ("gw", duplicate.payment_id), ("bank", f"setl_{dup_day:%m%d}"))
    # hop-1 keeps linking the ORIGINAL (first by time) capture; the
    # duplicate gets no hop-1 link, only the exception below.
    truth.add_exception(
        "DUPLICATE_PAYMENT",
        records=[("gw", duplicate.payment_id)],
        amount_at_risk_p=duplicate.amount_p,
        note="second capture on the same order (customer retry); refund suggested",
    )


def apply_d09(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    captures = sorted(
        (
            p
            for p in world.gw_payments
            if p.kind == "capture"
            and p.order_id
            and p.order_id not in ctx.used_orders
            and p.captured_on not in ctx.used_days
        ),
        key=lambda p: p.payment_id,
    )
    victim = rng.choice(captures)
    ctx.used_orders.add(victim.order_id)
    day = victim.captured_on
    was_settled = is_settled(world, day)
    world.gw_payments.remove(victim)
    truth.remove_links_for("gw", victim.payment_id)  # drops the hop-1 (and hop-2, if any) link
    resync_day_capture_voucher(world, day)
    if was_settled:
        resync_settlement(world, day)
    order = next(o for o in world.orders if o.order_id == victim.order_id)
    truth.add_exception(
        "ORPHAN_ORDER",
        records=[("orders", order.order_id)],
        amount_at_risk_p=order.amount_p,
        note="order confirmed but its capture failed at the gateway (optimistic status)",
    )


def apply_d10(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    candidates = sorted(
        (
            p
            for p in world.gw_payments
            if p.kind == "capture"
            and p.method == "card"
            and p.order_id not in ctx.used_orders
            and p.captured_on not in ctx.used_days
            and is_settled(world, p.captured_on)
        ),
        key=lambda p: p.payment_id,
    )
    if not candidates:
        raise RuntimeError("D-10: no settled card capture available")
    victim = rng.choice(candidates)
    ctx.used_orders.add(victim.order_id)
    ctx.used_days.add(victim.captured_on)
    correct_fee = victim.fee_p
    wrong_fee = moneymath.fee_p(victim.amount_p, config.D10_WRONG_FEE_BPS)
    wrong_gst = moneymath.gst_p(wrong_fee, config.GST_BPS_ON_FEE)
    victim.fee_p = wrong_fee
    victim.gst_p = wrong_gst
    resync_settlement(world, victim.captured_on)
    truth.add_exception(
        "FEE_VARIANCE",
        records=[("gw", victim.payment_id)],
        amount_at_risk_p=wrong_fee - correct_fee,
        note=f"fee charged at {config.D10_WRONG_FEE_BPS}bps instead of "
        f"{config.FEE_BPS['card']}bps for a card capture; detected by recomputation, not auto-resolved",
    )


def apply_d11(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    week1_day = _business_days()[0]
    week1_orders = sorted(
        (
            o
            for o in world.orders
            if o.created_on == week1_day and o.method != "cod" and o.order_id not in ctx.used_orders
        ),
        key=lambda o: o.order_id,
    )
    if not week1_orders:
        raise RuntimeError("D-11: no eligible week-1 order available")
    order = rng.choice(week1_orders)
    ctx.used_orders.add(order.order_id)

    settled_days = [d for d in _settled_days(world) if d not in ctx.used_days]
    if not settled_days:
        raise RuntimeError("D-11: no settled day available for the late batch")
    late_day = max(settled_days)
    ctx.used_days.add(late_day)

    sibling = next(p for p in world.gw_payments if p.captured_on == late_day and p.utr)
    chargeback = GwPayment(
        payment_id=next_payment_id(world),
        order_id=order.order_id,
        kind="chargeback",
        amount_p=-order.amount_p,
        fee_p=0,
        gst_p=0,
        method=order.method,
        captured_on=late_day,
        settlement_id=sibling.settlement_id,
        utr=sibling.utr,
    )
    world.gw_payments.append(chargeback)
    resync_settlement(world, late_day)
    truth.add_link(2, ("gw", chargeback.payment_id), ("bank", f"setl_{late_day:%m%d}"))
    truth.add_exception(
        "CHARGEBACK_UNRESOLVED",
        records=[("gw", chargeback.payment_id), ("orders", order.order_id)],
        amount_at_risk_p=order.amount_p,
        note="chargeback against a week-1 order netted into a later settlement batch",
    )


def apply_d12(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    captures = sorted(
        (
            p
            for p in world.gw_payments
            if p.kind == "capture"
            and p.order_id
            and p.order_id not in ctx.used_orders
            and p.captured_on not in ctx.used_days
        ),
        key=lambda p: p.payment_id,
    )
    victim = rng.choice(captures)
    ctx.used_orders.add(victim.order_id)
    order = next(o for o in world.orders if o.order_id == victim.order_id)
    order.amount_p = config.D12_ORDER_AMOUNT_P
    victim.amount_p = config.D12_CAPTURE_AMOUNT_P
    victim.fee_p = moneymath.fee_p(victim.amount_p, config.FEE_BPS[victim.method])
    victim.gst_p = moneymath.gst_p(victim.fee_p, config.GST_BPS_ON_FEE)
    day = victim.captured_on
    resync_day_capture_voucher(world, day)
    if is_settled(world, day):
        resync_settlement(world, day)
    # hop-1 keeps its link (accept the association, flag the amount delta).
    truth.add_exception(
        "PARTIAL_CAPTURE_MISMATCH",
        records=[("orders", order.order_id), ("gw", victim.payment_id)],
        amount_at_risk_p=order.amount_p - victim.amount_p,
    )


def apply_d13(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    candidates = [d for d in _settled_days(world) if d not in ctx.used_days]
    if not candidates:
        raise RuntimeError("D-13: no settled day available")
    day = rng.choice(candidates)
    ctx.used_days.add(day)
    voucher_no = f"V-{day:%Y%m%d}-SETL"
    lines = world.voucher_lines(voucher_no)
    dup_voucher_no = f"{voucher_no}-DUP"
    for g in lines:
        world.gl_entries.append(
            GlEntry(
                voucher_no=dup_voucher_no,
                line_no=g.line_no,
                entry_date=g.entry_date,
                account=g.account,
                debit_p=g.debit_p,
                credit_p=g.credit_p,
                memo=(g.memo + " (duplicate)").strip(),
            )
        )
    receivable_gl = next(g for g in lines if g.account == "PG_RECEIVABLE")
    truth.add_exception(
        "GL_DUPLICATE",
        records=[("gl", dup_voucher_no)],
        amount_at_risk_p=receivable_gl.credit_p,
        note="duplicate settlement voucher; clearing account goes over-credited",
    )


def apply_d14(world: World, truth: GroundTruth, rng, ctx: _Ctx) -> None:
    # (a) mixed-case UTR + surrounding whitespace on one well-formed narration
    wellformed = sorted(
        (
            b
            for b in world.bank_lines
            if b.narration.startswith("RAZORPAY SETTLEMENT") and b.narration.split(" ")[-1].startswith("UTIB0")
        ),
        key=lambda b: b.line_id,
    )
    if not wellformed:
        raise RuntimeError("D-14a: no well-formed settlement narration left to mangle")
    target_a = rng.choice(wellformed)
    parts = target_a.narration.split(" ")
    utr_token = parts[-1]
    parts[-1] = utr_token[0] + utr_token[1:].lower()
    target_a.narration = "  " + " ".join(parts) + "  "

    # (b) one gateway CSV amount written as a comma rupee string ("12,000.00")
    remaining = sorted(
        (p for p in world.gw_payments if p.kind == "capture" and p.raw_amount_override is None),
        key=lambda p: p.payment_id,
    )
    # Prefer an amount >= Rs 1,000 so the written string actually exercises
    # the thousands-comma stripping, not just the decimal-point path.
    over_1000 = [p for p in remaining if p.amount_p >= 1_000_00]
    target_b = rng.choice(over_1000 or remaining)
    target_b.raw_amount_override = moneymath.format_rupees(target_b.amount_p).lstrip("₹")
    # Neither mutation adds a ground-truth exception (SPEC §5.3 D-14):
    # "loader normalises silently" — this is what T-8/G2 check.


def apply_all_defects(world: World, truth: GroundTruth, rng) -> None:
    ctx = _Ctx()
    apply_d01(world, truth, rng, ctx)
    apply_d02(world, truth, rng, ctx)
    apply_d03(world, truth, rng, ctx)
    apply_d04(world, truth, rng, ctx)
    apply_d05(world, truth, rng, ctx)
    apply_d06(world, truth, rng, ctx)
    apply_d07(world, truth, rng, ctx)
    apply_d08(world, truth, rng, ctx)
    apply_d09(world, truth, rng, ctx)
    apply_d10(world, truth, rng, ctx)
    apply_d11(world, truth, rng, ctx)
    apply_d12(world, truth, rng, ctx)
    apply_d13(world, truth, rng, ctx)
    apply_d14(world, truth, rng, ctx)
