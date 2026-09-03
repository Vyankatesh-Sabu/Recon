import { AnimatePresence, motion } from "framer-motion"
import { Money } from "./Money"
import { SeverityRule } from "./SeverityRule"
import { TierBadge } from "./TierBadge"
import { isFullyChained, type ChainRow } from "../lib/runChains"
import type { RunEvent } from "../lib/api"

type ExceptionEvent = Extract<RunEvent, { kind: "exception" }>

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

function RowView({ row }: { row: ChainRow }) {
  const complete = isFullyChained(row)
  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0, backgroundColor: complete ? "rgba(78,168,138,0.08)" : "rgba(0,0,0,0)" }}
      transition={{ duration: 0.25 }}
      className="grid grid-cols-4"
    >
      <Cell id={row.orderId} />
      <Cell id={row.gwId} tier={row.tier1} />
      <Cell id={row.bankId} tier={row.tier2} />
      <Cell id={row.glId} done={row.hop3Done} />
    </motion.div>
  )
}

function GutterChip({ exc }: { exc: ExceptionEvent }) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="shrink-0"
    >
      <SeverityRule severity={exc.severity}>
        <div className="bg-ink px-2 py-1 rounded-sm text-xs whitespace-nowrap flex items-center gap-1.5">
          <span>{exc.code}</span>
          <Money amountP={exc.amount_at_risk_p} tone="caution" />
        </div>
      </SeverityRule>
    </motion.div>
  )
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
export function RunCanvas({ rows, gutter }: { rows: ChainRow[]; gutter: ExceptionEvent[] }) {
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
            <RowView key={row.key} row={row} />
          ))}
        </AnimatePresence>
        {rows.length === 0 && <div className="px-3 py-8 text-center text-muted text-sm">No records yet.</div>}
      </div>
      <div className="border-t border-rule px-3 py-2">
        <div className="text-xs text-muted mb-1.5">Exceptions</div>
        <div className="flex gap-2 overflow-x-auto pb-1 min-h-[1.75rem]">
          <AnimatePresence initial={false}>
            {gutter.map((exc) => (
              <GutterChip key={exc.exc_id} exc={exc} />
            ))}
          </AnimatePresence>
          {gutter.length === 0 && <span className="text-muted text-xs">None yet.</span>}
        </div>
      </div>
    </div>
  )
}
