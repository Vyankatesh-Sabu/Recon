import { useEffect, useMemo, useState } from "react"
import { Money } from "./Money"
import { getMatch, type MatchLink } from "../lib/api"

const ROW_INTERVAL_MS = 180

/** UI_SPEC.md §1: "prefers-reduced-motion respected; the run then steps
 * rather than animates." Here that means every row is present from the
 * first paint instead of streaming in. */
function usePrefersReducedMotion(): boolean {
  const query = useMemo(
    () => (typeof window !== "undefined" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null),
    [],
  )
  const [reduced, setReduced] = useState(query?.matches ?? false)
  useEffect(() => {
    if (!query) return
    const onChange = () => setReduced(query.matches)
    query.addEventListener("change", onChange)
    return () => query.removeEventListener("change", onChange)
  }, [query])
  return reduced
}

/**
 * ReconstructionViewer — UI_SPEC.md §2.3, "the best thirty seconds in the
 * video". The subset-sum arithmetic behind one hop-2 match, rendered as a
 * worked ledger column: a settlement with no shared key, every rupee
 * accounted for, and the delta landing on zero.
 *
 * Every figure is a value GET /api/match/{link_id} returned — net_p per
 * row, the running subtotal_p, reconstructed_p, delta_p. Nothing on this
 * screen is added up in the browser (UI_SPEC §0), which matters most here
 * of all: this panel's entire claim is that the arithmetic is the
 * pipeline's, shown rather than re-performed.
 *
 * Rows stream in one per ~180ms via setTimeout + a CSS transition.
 * Deliberately not framer-motion — CLAUDE.md rule 8 approves that
 * dependency for the run console only.
 */
export function ReconstructionViewer({ linkId, onClose }: { linkId: string; onClose?: () => void }) {
  const [match, setMatch] = useState<MatchLink | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revealed, setRevealed] = useState(0)
  const reducedMotion = usePrefersReducedMotion()

  useEffect(() => {
    setMatch(null)
    setError(null)
    setRevealed(0)
    getMatch(linkId)
      .then(setMatch)
      .catch((e) => setError(String(e)))
  }, [linkId])

  const rows = match?.rows
  useEffect(() => {
    if (!rows) return
    if (reducedMotion) {
      setRevealed(rows.length)
      return
    }
    const timers = rows.map((_, i) => setTimeout(() => setRevealed(i + 1), (i + 1) * ROW_INTERVAL_MS))
    return () => timers.forEach(clearTimeout)
  }, [rows, reducedMotion])

  if (error) return <p className="text-sm text-flag">{error}</p>
  if (!match) return <p className="text-sm text-muted">Loading reconstruction…</p>

  const bank = match.bank_line
  if (!rows || !bank) {
    return (
      <p className="text-sm text-muted">
        {match.link_id} is a hop-{match.hop} link — there is no settlement reconstruction behind it.
      </p>
    )
  }

  const complete = revealed >= rows.length
  // "no UTR recovered · no settlement id" is a statement about the gateway
  // rows, asserted only when the rows actually say so.
  const noReferences = rows.every((r) => r.utr === null && r.settlement_id === null)
  const subtotalP = revealed > 0 ? rows[revealed - 1].subtotal_p : 0

  return (
    <div className="bg-paper border border-rule rounded-sm">
      <div className="border-b border-rule px-4 py-2.5 flex items-baseline justify-between gap-4">
        <span className="text-xs tracking-wide text-muted">
          RECONSTRUCTION · tier {match.tier} · {match.status}
        </span>
        {onClose && (
          <button onClick={onClose} className="text-xs text-muted hover:text-figure transition-colors">
            close
          </button>
        )}
      </div>

      <div className="p-4 overflow-x-auto">
        <div className="min-w-[42rem]">
          {/* Header: the bank line as it arrived */}
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 pb-3 border-b border-rule">
            <span className="text-xs text-muted">BANK LINE</span>
            <span className="figures text-sm">{bank.line_id}</span>
            <span className="figures text-sm">{bank.value_date}</span>
            <Money amountP={bank.credit_p} />
            <span className="text-xs text-muted">narration: {bank.narration}</span>
          </div>
          {noReferences && (
            <p className="text-xs text-muted pt-1.5">no UTR recovered · no settlement id</p>
          )}

          {/* The worked column */}
          <table className="w-full text-sm mt-4 figures tabular-nums">
            <thead>
              <tr className="text-xs text-muted font-sans">
                <th className="text-left font-medium py-1 pr-4">payment</th>
                <th className="text-left font-medium py-1 pr-4">method</th>
                <th className="text-right font-medium py-1 px-4">amount</th>
                <th className="text-right font-medium py-1 px-4">fee</th>
                <th className="text-right font-medium py-1 px-4">gst</th>
                <th className="text-right font-medium py-1 pl-4">net</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={row.payment_id}
                  className="transition-opacity duration-200"
                  style={{ opacity: i < revealed ? 1 : 0 }}
                  aria-hidden={i >= revealed}
                >
                  <td className="py-1 pr-4">{row.payment_id}</td>
                  <td className="py-1 pr-4 text-muted">{row.kind === "capture" ? row.method : row.kind}</td>
                  <td className="py-1 px-4 text-right">
                    <Money amountP={row.amount_p} />
                  </td>
                  <td className="py-1 px-4 text-right">
                    {row.fee_p ? <Money amountP={-row.fee_p} tone="muted" /> : <span className="text-muted">—</span>}
                  </td>
                  <td className="py-1 px-4 text-right">
                    {row.gst_p ? <Money amountP={-row.gst_p} tone="muted" /> : <span className="text-muted">—</span>}
                  </td>
                  <td className="py-1 pl-4 text-right">
                    <Money amountP={row.net_p} tone={row.net_p < 0 ? "caution" : "figure"} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* The totals block — reconstructed, the bank line, the delta */}
          <div className="mt-3 pt-3 border-t border-rule flex justify-end">
            <dl className="text-sm w-80">
              <div className="flex justify-between py-0.5">
                <dt className="text-muted">{complete ? "reconstructed" : "running subtotal"}</dt>
                <dd>
                  <Money amountP={complete ? match.reconstructed_p! : subtotalP} />
                </dd>
              </div>
              <div className="flex justify-between py-0.5">
                <dt className="text-muted">bank line</dt>
                <dd>
                  <Money amountP={bank.credit_p} />
                </dd>
              </div>
              <div
                className="flex justify-between py-1 mt-1 border-t border-rule transition-opacity duration-300"
                style={{ opacity: complete ? 1 : 0.25 }}
              >
                <dt className="font-medium">delta</dt>
                <dd className="flex items-center gap-2">
                  <Money
                    amountP={match.delta_p!}
                    tone={complete && match.delta_p === 0 ? "verified" : "figure"}
                  />
                  {complete && match.delta_p === 0 && <span className="text-verified text-sm">✓</span>}
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </div>
  )
}
