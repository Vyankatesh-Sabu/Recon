import { AnimatePresence, motion } from "framer-motion"
import { Money } from "./Money"
import { SeverityRule } from "./SeverityRule"
import { TierBadge } from "./TierBadge"
import { isFullyChained, type ChainRow } from "../lib/runChains"
import type { RunEvent } from "../lib/api"

type ExceptionEvent = Extract<RunEvent, { kind: "exception" }>
type RejectedEvent = Extract<RunEvent, { kind: "rejected" }>
/** The gutter carries both kinds of failure: an exception the engine
 * raised, and a proposal the verifier threw out. The second is the one
 * that demonstrates the thesis, so it is shown, not counted. */
export type GutterItem = ExceptionEvent | RejectedEvent

const COLUMNS = ["Orders", "Gateway", "Bank", "Ledger"] as const

function Cell({ id, tier, done }: { id?: string; tier?: number | null; done?: boolean }) {
  return (
    <div className="px-3 py-1.5 border-r border-rule/60 last:border-0 flex items-center gap-1.5 min-h-[2.25rem] overflow-hidden">
      {id ? (
        <>
          <span className="figures text-xs truncate">{id}</span>
          {tier !== undefined && <TierBadge tier={tier ?? null} />}
          {done && <span className="text-verified text-xs shrink-0">✓</span>}
        </>
      ) : (
        <span className="text-muted text-xs">—</span>
      )}
    </div>
  )
}

function RowView({ row, onOpenReconstruction }: { row: ChainRow; onOpenReconstruction?: (linkId: string) => void }) {
  const complete = isFullyChained(row)
  // Only a tier-2 row has a reconstruction worth opening: tier 1 matched on
  // a shared UTR, and there is no arithmetic to show for that.
  const openable = row.tier2 === 2 && row.link2Id ? row.link2Id : null
  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0, backgroundColor: complete ? "rgba(78,168,138,0.08)" : "rgba(0,0,0,0)" }}
      transition={{ duration: 0.25 }}
      className={`grid grid-cols-4 ${openable ? "cursor-pointer hover:bg-ink/40" : ""}`}
      onClick={openable ? () => onOpenReconstruction?.(openable) : undefined}
      title={openable ? "Show how this settlement was reconstructed" : undefined}
    >
      <Cell id={row.orderId} />
      <Cell id={row.gwId} tier={row.tier1} />
      <Cell id={row.bankId} tier={row.tier2} />
      <Cell id={row.glId} done={row.hop3Done} />
    </motion.div>
  )
}

function GutterChip({ item }: { item: GutterItem }) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="shrink-0"
    >
      {item.kind === "exception" ? (
        <SeverityRule severity={item.severity}>
          <div className="bg-ink px-2 py-1 rounded-sm text-xs whitespace-nowrap flex items-center gap-1.5">
            <span>{item.code}</span>
            <Money amountP={item.amount_at_risk_p} tone="caution" />
          </div>
        </SeverityRule>
      ) : (
        // A rejection is always --flag regardless of tier: the verifier
        // refusing a proposal is the most consequential thing on screen.
        <div className="border-l-2 pl-3" style={{ borderColor: "var(--flag)" }}>
          <div className="bg-ink px-2 py-1 rounded-sm text-xs whitespace-nowrap flex items-center gap-1.5">
            <span className="text-flag">REJECTED</span>
            <span className="figures">T{item.tier ?? "?"}</span>
            <span className="text-muted" title={item.reason}>
              {item.reason}
            </span>
          </div>
        </div>
      )}
    </motion.div>
  )
}

function gutterKey(item: GutterItem): string {
  return item.kind === "exception" ? item.exc_id : `rejected-${item.link_id}`
}

/**
 * UI_SPEC.md §2.1 — the hero screen. A ledger-ruled canvas with four
 * labelled columns (Orders · Gateway · Bank · Ledger); records enter as
 * rows and snap together left to right as real match events arrive
 * (recon/engine/events.py, via RunConsole's SSE stream), tier badges per
 * match, failed items dropping into a severity-tinted gutter along the
 * bottom. The row/gutter derivation itself lives in lib/runChains.ts —
 * this component only renders what that reducer already computed.
 */
export function RunCanvas({
  rows,
  gutter,
  onOpenReconstruction,
}: {
  rows: ChainRow[]
  gutter: GutterItem[]
  onOpenReconstruction?: (linkId: string) => void
}) {
  return (
    <div className="bg-paper border border-rule rounded-sm overflow-hidden">
      <div className="grid grid-cols-4 border-b border-rule">
        {COLUMNS.map((col) => (
          <div key={col} className="px-3 py-2 text-xs text-muted border-r border-rule last:border-0">
            {col}
          </div>
        ))}
      </div>
      <div className="max-h-[26rem] overflow-y-auto divide-y divide-rule/60">
        <AnimatePresence initial={false}>
          {rows.map((row) => (
            <RowView key={row.key} row={row} onOpenReconstruction={onOpenReconstruction} />
          ))}
        </AnimatePresence>
        {rows.length === 0 && <div className="px-3 py-8 text-center text-muted text-sm">No records yet.</div>}
      </div>
      <div className="border-t border-rule px-3 py-2">
        <div className="text-xs text-muted mb-1.5">Exceptions and rejected proposals</div>
        <div className="flex gap-2 overflow-x-auto pb-1 min-h-[1.75rem]">
          <AnimatePresence initial={false}>
            {gutter.map((item) => (
              <GutterChip key={gutterKey(item)} item={item} />
            ))}
          </AnimatePresence>
          {gutter.length === 0 && <span className="text-muted text-xs">None yet.</span>}
        </div>
      </div>
    </div>
  )
}
