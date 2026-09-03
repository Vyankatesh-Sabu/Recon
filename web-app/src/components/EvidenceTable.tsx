import { Money } from "./Money"
import { ReconstructionViewer } from "./ReconstructionViewer"
import type { ExceptionRecord } from "../lib/api"

/**
 * EvidenceTable — UI_SPEC.md §2.4: "Row expands to the full evidence blob
 * rendered as a table, not raw JSON."
 *
 * Evidence in this system is not one shape, so this dispatches on what the
 * blob actually contains rather than on the exception code:
 *
 *   expected / found         GL decomposition — expected-vs-found columns
 *   subset_a + subset_b      a refusal's two indistinguishable readings
 *   subset / candidate_pool  the rows a reconstruction considered
 *   rows                     a settlement batch's per-row arithmetic
 *   anything else            its scalar fields, as a labelled table
 *
 * Each block says where it came from. That caption is load-bearing: the
 * API finds evidence by following a match_link that touches one of the
 * exception's records, which is related to the exception but not always an
 * explanation OF it, and a table implying otherwise would be the UI
 * asserting something the pipeline never claimed.
 */

const MONEY_KEY = /(_p|_p_)$/

function Caption({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-muted mb-2">{children}</p>
}

function Grid({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="text-sm border-collapse min-w-full">
        <thead>
          <tr className="border-b border-rule">
            {head.map((h) => (
              <th key={h} className="text-left text-xs font-medium text-muted py-1.5 pr-6 last:pr-0">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, i) => (
            <tr key={i} className="border-b border-rule/50 last:border-0">
              {cells.map((c, j) => (
                <td key={j} className="py-1.5 pr-6 last:pr-0 align-top figures tabular-nums">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** A paise-suffixed key renders as rupees; everything else as its literal
 * value. Nothing is recomputed — this only chooses a format. */
function scalarCell(key: string, value: unknown): React.ReactNode {
  if (typeof value === "number" && MONEY_KEY.test(key)) return <Money amountP={value} />
  if (value === null) return <span className="text-muted">—</span>
  if (typeof value === "object") return <span className="text-muted">{Array.isArray(value) ? `${value.length} items` : "—"}</span>
  return String(value)
}

type Subset = { id: string; net_p: number }[]

export function EvidenceTable({ exc }: { exc: ExceptionRecord }) {
  const ev = (exc.evidence ?? {}) as Record<string, unknown>
  // The reconstruction viewer re-derives the same batch from the database.
  // Showing the stored copy underneath it says the same thing twice, so
  // the live one wins and the stored rows are suppressed.
  const reconstructionLinkId = exc.evidence_link_hop === 2 ? exc.evidence_link_id : null

  return (
    <div className="flex flex-col gap-5 py-2">
      {/* Always: the records this exception is actually about. */}
      <div>
        <Caption>Records</Caption>
        <Grid
          head={["source", "id"]}
          rows={exc.records.map((r) => [r.src, r.id])}
        />
      </div>

      <div>
        <Caption>Suggested action</Caption>
        <p className="text-sm">{exc.suggested_action}</p>
      </div>

      {/* A settlement reconstruction, but only when the linked match is a
          hop-2 one — that is the only hop with arithmetic to show. */}
      {reconstructionLinkId && (
        <div>
          <Caption>Settlement reconstruction behind linked match {reconstructionLinkId}</Caption>
          <ReconstructionViewer linkId={reconstructionLinkId} />
        </div>
      )}

      {/* GL decomposition: what the voucher should have posted vs what it did. */}
      {isRecord(ev.expected) && isRecord(ev.found) && (
        <div>
          <Caption>
            Voucher {String(ev.voucher_no)} — expected against found
          </Caption>
          <Grid
            head={["account", "expected", "found"]}
            rows={Array.from(new Set([...Object.keys(ev.expected), ...Object.keys(ev.found)])).map((account) => [
              <span className="font-sans">{account}</span>,
              scalarCell("_p", (ev.expected as Record<string, unknown>)[account] ?? null),
              scalarCell("_p", (ev.found as Record<string, unknown>)[account] ?? null),
            ])}
          />
        </div>
      )}

      {/* A refusal: the two readings that could not be told apart. */}
      {isSubset(ev.subset_a) && isSubset(ev.subset_b) && (
        <div>
          <Caption>
            Two disjoint readings of bank line {String(ev.bank_line)}, both summing to the credit —
            which is why neither was proposed
          </Caption>
          <Grid
            head={["reading", "rows", "net"]}
            rows={[
              ["A", ev.subset_a.map((r) => r.id).join(", "), <Money amountP={Number(ev.subtotal_a_p)} />],
              ["B", ev.subset_b.map((r) => r.id).join(", "), <Money amountP={Number(ev.subtotal_b_p)} />],
            ]}
          />
        </div>
      )}

      {/* The pool a reconstruction searched — present on refusals and on
          unexplained credits, where it is the proof that nothing fit. */}
      {isPool(ev.candidate_pool) && (
        <div>
          <Caption>
            Candidate pool searched ({ev.candidate_pool.length} rows
            {typeof ev.target_p === "number" ? " against " : ""}
            {typeof ev.target_p === "number" ? <Money amountP={ev.target_p} /> : null})
            {typeof ev.reason === "string" ? ` — ${ev.reason}` : ""}
          </Caption>
          {ev.candidate_pool.length > 0 ? (
            <Grid
              head={["payment", "captured", "net"]}
              rows={ev.candidate_pool.map((r) => [r.id, r.captured_on, <Money amountP={r.net_p} />])}
            />
          ) : (
            // An empty pool is the finding, not a missing table: no unclaimed
            // gateway row fell in this line's date window at all, so there was
            // nothing to reconstruct from.
            <p className="text-sm">
              No unclaimed gateway row fell within this line's date window — there was nothing to
              reconstruct from.
            </p>
          )}
        </div>
      )}

      {/* A settlement batch's per-row arithmetic, as the matcher saw it. */}
      {!reconstructionLinkId && isBatchRows(ev.rows) && (
        <div>
          <Caption>Linked match evidence — batch {String(ev.settlement_id ?? "—")} as the matcher read it</Caption>
          <Grid
            head={["payment", "kind", "amount", "fee", "gst", "net"]}
            rows={ev.rows.map((r) => [
              r.id,
              <span className="font-sans text-muted">{r.kind}</span>,
              <Money amountP={r.amount_p} />,
              r.fee_p ? <Money amountP={-r.fee_p} tone="muted" /> : "—",
              r.gst_p ? <Money amountP={-r.gst_p} tone="muted" /> : "—",
              <Money amountP={r.net_p} />,
            ])}
          />
        </div>
      )}

      {/* Everything scalar that is left, so nothing in the blob is hidden. */}
      {(() => {
        const scalars = Object.entries(ev).filter(
          ([, v]) => v === null || typeof v !== "object",
        )
        if (scalars.length === 0) return null
        return (
          <div>
            <Caption>
              {exc.evidence_link_id
                ? `Other fields from linked match ${exc.evidence_link_id} (hop ${exc.evidence_link_hop})`
                : "Other fields recorded with this exception"}
            </Caption>
            <Grid
              head={["field", "value"]}
              rows={scalars.map(([k, v]) => [<span className="font-sans text-muted">{k}</span>, scalarCell(k, v)])}
            />
          </div>
        )
      })()}

      {Object.keys(ev).length === 0 && (
        <p className="text-sm text-muted">
          No linked match carried evidence for these records — the explanation above is everything
          the engine recorded.
        </p>
      )}
    </div>
  )
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v)
}

function isSubset(v: unknown): v is Subset {
  return Array.isArray(v) && v.every((r) => isRecord(r) && "id" in r && "net_p" in r)
}

function isPool(v: unknown): v is { id: string; captured_on: string; net_p: number }[] {
  return Array.isArray(v) && v.every((r) => isRecord(r) && "id" in r && "captured_on" in r)
}

function isBatchRows(
  v: unknown,
): v is { id: string; kind: string; amount_p: number; fee_p: number; gst_p: number; net_p: number }[] {
  return Array.isArray(v) && v.every((r) => isRecord(r) && "id" in r && "amount_p" in r && "kind" in r)
}
