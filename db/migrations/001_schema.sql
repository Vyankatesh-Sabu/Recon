-- 001_schema.sql — RECON-4 core schema (SPEC.md §4), SQLite dialect.
--
-- Type mapping from the spec's Postgres-flavoured DDL:
--   BIGINT  -> INTEGER  (SQLite integers are variable-width up to 8 bytes;
--                        plenty of range for paise amounts — CLAUDE.md rule 1)
--   JSONB   -> TEXT      (holds a JSON string; no native JSON type in stdlib
--                        sqlite3 — callers json.dumps/json.loads it)
--   NUMERIC(4,3) -> REAL (match_link.confidence, 0..1)
--   DATE / timestamp -> TEXT, ISO-8601 (YYYY-MM-DD, or full ISO datetime for
--                        runs.started_at/finished_at)
-- All ids are TEXT (per §4 preamble).

CREATE TABLE orders (
    order_id    TEXT PRIMARY KEY,
    customer    TEXT NOT NULL,
    amount_p    INTEGER NOT NULL,
    method      TEXT NOT NULL,       -- card|upi|nb|cod
    status      TEXT NOT NULL,       -- confirmed|cancelled
    created_on  TEXT NOT NULL        -- DATE, ISO-8601
);

CREATE TABLE gw_payments (
    payment_id      TEXT PRIMARY KEY,
    order_id        TEXT,            -- NULL for orphan payments (D-06)
    kind            TEXT NOT NULL,   -- capture|refund|chargeback|adjustment
    amount_p        INTEGER NOT NULL,-- signed: refunds/chargebacks negative
    fee_p           INTEGER NOT NULL,-- zero for non-captures
    gst_p           INTEGER NOT NULL,-- zero for non-captures
    method          TEXT NOT NULL,
    captured_on     TEXT NOT NULL,   -- DATE, ISO-8601
    settlement_id   TEXT,            -- NULL until settled
    utr             TEXT             -- NULL until settled
);

CREATE TABLE bank_lines (
    line_id         TEXT PRIMARY KEY,
    value_date      TEXT NOT NULL,   -- DATE, ISO-8601
    narration       TEXT NOT NULL,
    credit_p        INTEGER NOT NULL,
    debit_p         INTEGER NOT NULL,
    utr_extracted   TEXT             -- filled by parser; may be NULL
);

CREATE TABLE gl_entries (
    voucher_no  TEXT NOT NULL,
    line_no     INTEGER NOT NULL,
    entry_date  TEXT NOT NULL,       -- DATE, ISO-8601
    account     TEXT NOT NULL,
    debit_p     INTEGER NOT NULL,
    credit_p    INTEGER NOT NULL,
    memo        TEXT,
    PRIMARY KEY (voucher_no, line_no)
);

CREATE TABLE match_link (
    link_id     TEXT PRIMARY KEY,
    hop         INTEGER NOT NULL,    -- 1|2|3
    src_a       TEXT NOT NULL,
    id_a        TEXT NOT NULL,
    src_b       TEXT NOT NULL,
    id_b        TEXT NOT NULL,
    tier        INTEGER NOT NULL,    -- 1..4
    confidence  REAL NOT NULL,       -- NUMERIC(4,3) -> REAL
    status      TEXT NOT NULL,       -- proposed|accepted|rejected
    reason      TEXT,
    evidence    TEXT,                -- JSONB -> TEXT holding a JSON string
    run_id      TEXT NOT NULL
);

CREATE TABLE exceptions (
    exc_id              TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    code                TEXT NOT NULL,
    severity            TEXT NOT NULL,   -- info|warn|critical
    hop                 INTEGER,         -- NULL when not hop-specific
    records             TEXT NOT NULL,   -- JSONB -> TEXT: [{src,id},...]
    amount_at_risk_p    INTEGER NOT NULL,
    age_days            INTEGER NOT NULL,
    explanation         TEXT,
    suggested_action    TEXT,
    status              TEXT NOT NULL    -- open|resolved
);

CREATE TABLE runs (
    run_id          TEXT PRIMARY KEY,
    seed            INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    llm_mode        TEXT NOT NULL,
    metrics         TEXT             -- JSONB -> TEXT holding a JSON string
);

-- Invariant V2 (no double claims), enforced in the database itself via
-- SQLite partial unique indexes — these are load-bearing (CLAUDE.md rule 2),
-- not merely advisory. Only verifier.py may set status='accepted'; these
-- indexes are what makes a second such UPDATE/INSERT fail loudly instead of
-- silently double-claiming a record.
--
-- NOTE on the hop-2 exception (deviates from SPEC.md §4's literal comment,
-- confirmed with the user 2026-08-30): hop 2 is implemented as payments on
-- side a, one bank line on side b — a settlement batch produces MANY accepted
-- match_link rows (one per payment) that all share the same (src_b, id_b,
-- hop=2), since every payment in the batch legitimately claims the same bank
-- line. Each of those rows has a DIFFERENT id_a (a different payment), so
-- (src_a, id_a, hop) stays naturally unique even at hop 2 and one_claim_a
-- needs no exception. It is one_claim_b that must exclude hop 2, or the
-- second-through-Nth payment in every settlement batch would fail with a
-- spurious UniqueViolation.
CREATE UNIQUE INDEX one_claim_a ON match_link (src_a, id_a, hop) WHERE status = 'accepted';
CREATE UNIQUE INDEX one_claim_b ON match_link (src_b, id_b, hop) WHERE status = 'accepted' AND hop <> 2;
