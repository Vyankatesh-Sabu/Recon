"""world.py — clean-world builder (SPEC §5.2).

Builds the reconciling-by-construction synthetic world: orders, gateway
payments, bank lines, GL entries, plus the GroundTruth links/in_transit
entries that are true by construction. `defects.py` mutates the World this
returns; nothing here knows about defects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import config
from recon import busdays, moneymath
from recon.generator.truth import GroundTruth


@dataclass
class Order:
    order_id: str
    customer: str
    amount_p: int
    method: str  # card|upi|nb|cod
    status: str  # confirmed|cancelled
    created_on: date


@dataclass
class GwPayment:
    payment_id: str
    order_id: str | None
    kind: str  # capture|refund|chargeback|adjustment
    amount_p: int  # signed: refunds/chargebacks negative
    fee_p: int  # zero for non-captures
    gst_p: int  # zero for non-captures
    method: str
    captured_on: date
    settlement_id: str | None = None
    utr: str | None = None
    # Generator-only, never a real domain value: overrides the CSV text for
    # this row's amount column (D-14b). Never read by anything but io.py.
    raw_amount_override: str | None = None

    @property
    def settlement_contribution_p(self) -> int:
        """This row's contribution to a batch's settlement_net_p (SPEC §5.1)."""
        if self.kind == "capture":
            return moneymath.net_p(self.amount_p, self.fee_p, self.gst_p)
        return self.amount_p  # refund/chargeback/adjustment: already signed


@dataclass
class BankLine:
    line_id: str
    value_date: date
    narration: str
    credit_p: int
    debit_p: int = 0


@dataclass
class GlEntry:
    voucher_no: str
    line_no: int
    entry_date: date
    account: str
    debit_p: int
    credit_p: int
    memo: str = ""


@dataclass
class World:
    orders: list[Order] = field(default_factory=list)
    gw_payments: list[GwPayment] = field(default_factory=list)
    bank_lines: list[BankLine] = field(default_factory=list)
    gl_entries: list[GlEntry] = field(default_factory=list)

    def payments_in_batch(self, settlement_id: str) -> list[GwPayment]:
        return [p for p in self.gw_payments if p.settlement_id == settlement_id]

    def voucher_lines(self, voucher_no: str) -> list[GlEntry]:
        return [g for g in self.gl_entries if g.voucher_no == voucher_no]

    def remove_voucher(self, voucher_no: str) -> None:
        self.gl_entries = [g for g in self.gl_entries if g.voucher_no != voucher_no]

    def remove_bank_line(self, line_id: str) -> None:
        self.bank_lines = [b for b in self.bank_lines if b.line_id != line_id]


def _day_order_counts(rng, n_days: int, total: int, low: int, high: int) -> list[int]:
    """n_days counts, each in [low, high], summing to exactly total (SPEC §5.2.1).

    Rejection-sampled off rng: deterministic for a fixed seed, and total=60
    over 10 days with a 4-9 range (40..90 reachable) converges immediately.
    """
    for _ in range(10_000):
        counts = [rng.randint(low, high) for _ in range(n_days)]
        if sum(counts) == total:
            return counts
    raise RuntimeError("could not sample day-order counts summing to total; check config ranges")


def _next_voucher_seq(world: World, prefix: str) -> int:
    return sum(1 for g in world.gl_entries if g.voucher_no.startswith(prefix) and g.line_no == 1) + 1


def add_voucher(world: World, voucher_no: str, entry_date: date, lines: list[tuple[str, int, int, str]]) -> None:
    """lines: list of (account, debit_p, credit_p, memo). Asserts the voucher balances (V3)."""
    total_debit = sum(line[1] for line in lines)
    total_credit = sum(line[2] for line in lines)
    if total_debit != total_credit:
        raise AssertionError(f"voucher {voucher_no} does not balance: debit={total_debit} credit={total_credit}")
    for i, (account, debit_p, credit_p, memo) in enumerate(lines, start=1):
        world.gl_entries.append(
            GlEntry(
                voucher_no=voucher_no,
                line_no=i,
                entry_date=entry_date,
                account=account,
                debit_p=debit_p,
                credit_p=credit_p,
                memo=memo,
            )
        )


def _draw_method(rng) -> str:
    methods, weights = zip(*config.METHOD_MIX.items())
    return rng.choices(methods, weights=weights, k=1)[0]


def draw_amount_p(rng) -> int:
    raw_p = rng.randint(*config.AMOUNT_RANGE_P)
    return moneymath.round_half_up(raw_p, 100) * 100  # round to whole rupees


def _draw_utr(rng) -> str:
    digits = "".join(rng.choice("0123456789") for _ in range(10))
    return f"UTIB0{digits}"


def next_payment_id(world: World) -> str:
    """Next sequential PAY-#### id — used by defects.py when injecting new gw rows."""
    existing = [int(p.payment_id.split("-")[1]) for p in world.gw_payments if p.payment_id.startswith("PAY-")]
    return f"PAY-{(max(existing) + 1 if existing else 1):04d}"


def is_settled(world: World, day: date) -> bool:
    """True if `day`'s batch already has a bank line (i.e. settled within DATE_TO)."""
    line_id = f"setl_{day:%m%d}"
    return any(b.line_id == line_id for b in world.bank_lines)


def resync_day_capture_voucher(world: World, day: date) -> None:
    """Recompute "V-{day}-CAP" from the current gw_payments for that day.

    Called by defects.py after mutating/adding/removing a capture on `day`,
    so the daily capture journal stays derived (never a stale literal).
    """
    captures_today = [p for p in world.gw_payments if p.captured_on == day and p.kind == "capture"]
    voucher_no = f"V-{day:%Y%m%d}-CAP"
    world.remove_voucher(voucher_no)
    if not captures_today:
        return
    cap_total = sum(p.amount_p for p in captures_today)
    add_voucher(
        world,
        voucher_no,
        day,
        [("PG_RECEIVABLE", cap_total, 0, "daily captures"), ("SALES", 0, cap_total, "daily captures")],
    )


def resync_settlement(world: World, day: date) -> None:
    """Recompute `day`'s bank line credit_p + "V-{day}-SETL" voucher from current gw_payments.

    No-op if `day` was never settled (no matching bank line) — callers must
    only invoke this for a day they know is settled (see `is_settled`).
    """
    line_id = f"setl_{day:%m%d}"
    bank_line = next((b for b in world.bank_lines if b.line_id == line_id), None)
    if bank_line is None:
        return
    day_rows = [p for p in world.gw_payments if p.captured_on == day]
    captures_today = [p for p in day_rows if p.kind == "capture"]
    chargebacks_today = [p for p in day_rows if p.kind == "chargeback"]
    net = moneymath.settlement_net_p([p.settlement_contribution_p for p in day_rows], [])
    assert net > 0, f"batch {line_id} resynced to a non-positive net {net}"
    bank_line.credit_p = net
    fee_total = sum(p.fee_p for p in captures_today)
    gst_total = sum(p.gst_p for p in captures_today)
    # PG_RECEIVABLE only ever tracks captures/refunds (money the customer
    # owed and either paid or was refunded). A chargeback is a direct hit
    # to cash on an ALREADY-cleared receivable from some earlier, unrelated
    # settlement — it doesn't touch PG_RECEIVABLE at all; it needs its own
    # CHARGEBACK_LOSS debit line, or the voucher balances by silently
    # (and wrongly) treating the chargeback as if it reduced this batch's
    # receivable, which V5 would then have no way to explain.
    receivable_total = sum(p.amount_p for p in day_rows if p.kind != "chargeback")
    chargeback_total = sum(-p.amount_p for p in chargebacks_today)  # amount_p is negative
    settle_date = busdays.add_bdays(day, config.SETTLEMENT_LAG_BDAYS)
    voucher_no = f"V-{day:%Y%m%d}-SETL"
    world.remove_voucher(voucher_no)
    lines = [
        ("BANK", net, 0, "settlement"),
        ("FEE_EXPENSE", fee_total, 0, "settlement"),
        ("INPUT_GST", gst_total, 0, "settlement"),
    ]
    if chargeback_total:
        lines.append(("CHARGEBACK_LOSS", chargeback_total, 0, "settlement"))
    lines.append(("PG_RECEIVABLE", 0, receivable_total, "settlement"))
    add_voucher(world, voucher_no, settle_date, lines)


def build_clean_world(rng) -> tuple[World, GroundTruth]:
    world = World()
    truth = GroundTruth(seed=config.SEED)

    business_days = busdays.business_days_in_range(config.DATE_FROM, config.DATE_TO)
    assert len(business_days) == 10, f"expected 10 business days, got {len(business_days)}"

    day_counts = _day_order_counts(rng, len(business_days), config.N_ORDERS, *config.ORDERS_PER_DAY_RANGE)

    # --- 1. Orders ---
    orders_by_day: dict[date, list[Order]] = {d: [] for d in business_days}
    cod_indices = set(rng.sample(range(config.N_ORDERS), config.N_COD_ORDERS))
    order_seq = 0
    for day, count in zip(business_days, day_counts):
        for _ in range(count):
            order_seq += 1
            is_cod = (order_seq - 1) in cod_indices
            method = "cod" if is_cod else _draw_method(rng)
            order = Order(
                order_id=f"ORD-{1000 + order_seq}",
                customer=f"CUST-{order_seq:03d}",
                amount_p=draw_amount_p(rng),
                method=method,
                status="confirmed",
                created_on=day,
            )
            world.orders.append(order)
            orders_by_day[day].append(order)

    # --- 2. One capture per non-cod order, same day ---
    payment_seq = 0
    capture_by_order: dict[str, GwPayment] = {}
    for order in world.orders:
        if order.method == "cod":
            continue
        payment_seq += 1
        fee = moneymath.fee_p(order.amount_p, config.FEE_BPS[order.method])
        gst = moneymath.gst_p(fee, config.GST_BPS_ON_FEE)
        capture = GwPayment(
            payment_id=f"PAY-{payment_seq:04d}",
            order_id=order.order_id,
            kind="capture",
            amount_p=order.amount_p,
            fee_p=fee,
            gst_p=gst,
            method=order.method,
            captured_on=order.created_on,
        )
        world.gw_payments.append(capture)
        capture_by_order[order.order_id] = capture
        truth.add_link(1, ("orders", order.order_id), ("gw", capture.payment_id))

    # --- 3. Refunds: 4 full-amount refunds against earlier captures ---
    eligible = [c for c in world.gw_payments if c.kind == "capture"]
    rng.shuffle(eligible)
    refunds_made = 0
    for capture in eligible:
        if refunds_made >= config.N_REFUNDS:
            break
        lo, hi = config.REFUND_OFFSET_BDAYS_RANGE
        max_offset = 0
        for k in range(lo, hi + 1):
            if busdays.add_bdays(capture.captured_on, k) <= config.DATE_TO:
                max_offset = k
        if max_offset < lo:
            continue  # too close to DATE_TO to fit even the minimum offset
        offset = rng.randint(lo, max_offset)
        refund_date = busdays.add_bdays(capture.captured_on, offset)
        payment_seq += 1
        refund = GwPayment(
            payment_id=f"PAY-{payment_seq:04d}",
            order_id=capture.order_id,
            kind="refund",
            amount_p=-capture.amount_p,
            fee_p=0,
            gst_p=0,
            method=capture.method,
            captured_on=refund_date,
        )
        world.gw_payments.append(refund)
        refunds_made += 1
    assert refunds_made == config.N_REFUNDS, f"only placed {refunds_made}/{config.N_REFUNDS} refunds"

    # --- 4/5. Settlement batching + GL, per day ---
    gw_by_day: dict[date, list[GwPayment]] = {}
    for p in world.gw_payments:
        gw_by_day.setdefault(p.captured_on, []).append(p)

    for day in business_days:
        day_rows = gw_by_day.get(day, [])

        captures_today = [p for p in day_rows if p.kind == "capture"]
        if captures_today:
            cap_total = sum(p.amount_p for p in captures_today)
            add_voucher(
                world,
                f"V-{day:%Y%m%d}-CAP",
                day,
                [("PG_RECEIVABLE", cap_total, 0, "daily captures"), ("SALES", 0, cap_total, "daily captures")],
            )

        for p in day_rows:
            if p.kind != "refund":
                continue
            amt = abs(p.amount_p)
            seq = _next_voucher_seq(world, f"V-{day:%Y%m%d}-REF")
            add_voucher(
                world,
                f"V-{day:%Y%m%d}-REF-{seq}",
                day,
                [("SALES_RETURNS", amt, 0, "refund"), ("PG_RECEIVABLE", 0, amt, "refund")],
            )

        if not day_rows:
            continue

        batch_id = f"setl_{day:%m%d}"
        settle_date = busdays.add_bdays(day, config.SETTLEMENT_LAG_BDAYS)
        for p in day_rows:
            p.settlement_id = batch_id

        if settle_date > config.DATE_TO:
            truth.add_in_transit(batch_id, settle_date)
            continue

        utr = _draw_utr(rng)
        for p in day_rows:
            p.utr = utr
        net = moneymath.settlement_net_p([p.settlement_contribution_p for p in day_rows], [])
        assert net > 0, f"batch {batch_id} settled non-positive net {net}"
        line_id = batch_id
        world.bank_lines.append(
            BankLine(
                line_id=line_id,
                value_date=settle_date,
                narration=f"RAZORPAY SETTLEMENT {batch_id} {utr}",
                credit_p=net,
                debit_p=0,
            )
        )
        for p in day_rows:
            truth.add_link(2, ("gw", p.payment_id), ("bank", line_id))

        fee_total = sum(p.fee_p for p in captures_today)
        gst_total = sum(p.gst_p for p in captures_today)
        # PG_RECEIVABLE tracks captures/refunds only (see resync_settlement's
        # comment) — no chargebacks exist yet at this point in construction,
        # but the filter is here for consistency with resync_settlement.
        receivable_total = sum(p.amount_p for p in day_rows if p.kind != "chargeback")
        voucher_no = f"V-{day:%Y%m%d}-SETL"
        add_voucher(
            world,
            voucher_no,
            settle_date,
            [
                ("BANK", net, 0, "settlement"),
                ("FEE_EXPENSE", fee_total, 0, "settlement"),
                ("INPUT_GST", gst_total, 0, "settlement"),
                ("PG_RECEIVABLE", 0, receivable_total, "settlement"),
            ],
        )
        truth.add_link(3, ("bank", line_id), ("gl", voucher_no))

    # --- 6. Two non-gateway bank lines ---
    maint_date = business_days[len(business_days) // 2]
    world.bank_lines.append(
        BankLine(
            line_id="bl_maint_charge",
            value_date=maint_date,
            narration="ACCOUNT MAINTENANCE CHARGE",
            credit_p=0,
            debit_p=config.MAINTENANCE_CHARGE_P,
        )
    )
    add_voucher(
        world,
        "V-MAINT-CHARGE",
        maint_date,
        [
            ("BANK_CHARGES", config.MAINTENANCE_CHARGE_P, 0, "account maintenance"),
            ("BANK", 0, config.MAINTENANCE_CHARGE_P, "account maintenance"),
        ],
    )
    truth.add_link(3, ("bank", "bl_maint_charge"), ("gl", "V-MAINT-CHARGE"))

    neft_date = business_days[1]
    world.bank_lines.append(
        BankLine(
            line_id="bl_direct_neft",
            value_date=neft_date,
            narration="NEFT CR DIRECT CUSTOMER PAYMENT",
            credit_p=config.DIRECT_NEFT_CREDIT_CLEAN_P,
            debit_p=0,
        )
    )
    add_voucher(
        world,
        "V-DIRECT-NEFT",
        neft_date,
        [
            ("BANK", config.DIRECT_NEFT_CREDIT_CLEAN_P, 0, "direct customer payment"),
            ("SALES", 0, config.DIRECT_NEFT_CREDIT_CLEAN_P, "direct customer payment"),
        ],
    )
    truth.add_link(3, ("bank", "bl_direct_neft"), ("gl", "V-DIRECT-NEFT"))

    return world, truth
