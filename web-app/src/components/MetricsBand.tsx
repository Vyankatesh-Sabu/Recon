import { motion } from "framer-motion"
import type { ReactNode } from "react"
import { Money } from "./Money"
import type { PipelineMetrics } from "../lib/api"

function pct(f: number): string {
  return (f * 100).toFixed(1) + "%"
}

/** UI_SPEC.md §2.2 — NOT a card grid. One horizontal ledger strip,
 * hairline-ruled between figures. FALSE MATCH gets the largest type on
 * the whole screen: it's the number the brief is actually asking for. */
export function MetricsBand({ metrics }: { metrics: PipelineMetrics }) {
  const { fully_chained, chainable_orders } = metrics.full_chain_fraction
  const falseMatchOk = metrics.false_match_rate === 0

  const cells: { label: string; top: ReactNode; bottom?: ReactNode; big?: boolean }[] = [
    {
      label: "FULL CHAIN",
      top: (
        <span className="figures tabular-nums">
          {fully_chained} / {chainable_orders}
        </span>
      ),
      bottom: <span className="figures tabular-nums text-muted">{pct(metrics.full_chain_rate)}</span>,
    },
    { label: "LINK PRECISION", top: <span className="figures tabular-nums">{pct(metrics.link_precision)}</span> },
    {
      label: "FALSE MATCH",
      top: (
        <span className={`figures tabular-nums ${falseMatchOk ? "text-verified" : "text-flag"}`}>
          {pct(metrics.false_match_rate)}
        </span>
      ),
      big: true,
    },
    { label: "RECONCILED", top: <Money amountP={metrics.value_reconciled_p} /> },
    {
      label: "AT RISK",
      top: <Money amountP={metrics.amount_at_risk_p} tone="caution" />,
      bottom: (
        <span className="figures tabular-nums text-muted">{metrics.exceptions.open} open</span>
      ),
    },
    {
      label: "LLM",
      top: <span className="figures tabular-nums">{metrics.llm_calls.total} calls</span>,
      bottom: <span className="figures tabular-nums text-muted">{metrics.llm_calls.accepted} kept</span>,
    },
  ]

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="bg-paper border border-rule rounded-sm overflow-x-auto"
    >
      <div className="flex divide-x divide-rule min-w-max">
        {cells.map((cell) => (
          <div key={cell.label} className="px-6 py-4 flex flex-col gap-1 min-w-[9rem]">
            <div className="text-xs text-muted tracking-wide">{cell.label}</div>
            <div className={cell.big ? "text-4xl font-medium leading-none" : "text-lg leading-none"}>{cell.top}</div>
            {cell.bottom && <div className="text-xs mt-0.5">{cell.bottom}</div>}
          </div>
        ))}
      </div>
    </motion.div>
  )
}
