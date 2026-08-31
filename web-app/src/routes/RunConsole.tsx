import { useState } from "react"
import { LedgerPanel } from "../components/LedgerPanel"
import { LedgerTable, type LedgerColumn } from "../components/LedgerTable"
import { Money } from "../components/Money"
import { SeverityRule } from "../components/SeverityRule"
import { TierBadge } from "../components/TierBadge"
import { getRunMetrics, startRun, streamRun, type RunEvent, type RunMetrics } from "../lib/api"

/**
 * P7 shell stub for UI_SPEC.md §2.1 (the hero screen) — real POST /api/run
 * + real SSE stream, rendered as a plain ledger-ruled event log rather than
 * the full record-flow choreography. That visual build is P8's job; this
 * proves the wiring (trigger a run, watch it stream, land on real metrics)
 * end to end with the LLM off.
 */
export function RunConsole() {
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle")
  const [metrics, setMetrics] = useState<RunMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleStart() {
    setStatus("running")
    setEvents([])
    setMetrics(null)
    setError(null)
    try {
      const { run_id } = await startRun({ llmMode: "off" })
      setRunId(run_id)
      streamRun(
        run_id,
        (event) => setEvents((prev) => [...prev, event]),
        () => {
          setStatus("done")
          getRunMetrics(run_id).then(setMetrics).catch((e) => setError(String(e)))
        },
      )
    } catch (e) {
      setStatus("error")
      setError(String(e))
    }
  }

  const columns: LedgerColumn<RunEvent>[] = [
    { header: "SEQ", figures: true, render: (e) => e.seq },
    {
      header: "KIND",
      render: (e) =>
        e.kind === "match" ? <TierBadge tier={e.tier} /> : <span className="text-caution text-xs">EXC</span>,
    },
    { header: "HOP", figures: true, render: (e) => e.hop },
    {
      header: "DETAIL",
      render: (e) =>
        e.kind === "match" ? (
          <span className="figures text-xs text-muted">
            {e.id_a} <span className="text-verified">↔</span> {e.id_b}
          </span>
        ) : (
          <SeverityRule severity={e.severity}>
            <span className="text-xs">{e.code}</span>
          </SeverityRule>
        ),
    },
    {
      header: "₹ AT RISK",
      align: "right",
      render: (e) => (e.kind === "exception" ? <Money amountP={e.amount_at_risk_p} tone="caution" /> : null),
    },
  ]

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Run console</h1>
        <button
          onClick={handleStart}
          disabled={status === "running"}
          className="text-sm px-3 py-1.5 rounded-sm border border-rule text-figure hover:border-trace hover:text-trace transition-colors disabled:opacity-50 disabled:cursor-default"
        >
          {status === "running" ? "Running…" : "Start run"}
        </button>
      </div>

      {runId && <p className="text-xs text-muted figures">{runId}</p>}
      {error && <p className="text-sm text-flag">{error}</p>}

      <LedgerPanel title={`Event stream (${events.length})`}>
        <LedgerTable columns={columns} rows={events} rowKey={(e) => String(e.seq)} />
      </LedgerPanel>

      {metrics?.metrics && (
        <LedgerPanel title="Metrics (§2.2 lands in P8)">
          <pre className="figures text-xs text-muted whitespace-pre-wrap">
            {JSON.stringify(metrics.metrics, null, 2)}
          </pre>
        </LedgerPanel>
      )}
    </div>
  )
}
