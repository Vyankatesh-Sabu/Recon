import { useEffect, useMemo, useState } from "react"
import { EvidenceTable } from "../components/EvidenceTable"
import { LedgerPanel } from "../components/LedgerPanel"
import { Money } from "../components/Money"
import { RefusalCard, pairRefusals } from "../components/RefusalCard"
import { SeverityRule } from "../components/SeverityRule"
import { getLatestRun, getRunExceptions, type ExceptionRecord } from "../lib/api"

/**
 * UI_SPEC.md §2.4 (the queue) + §2.5 (the refusal card).
 *
 * The list a controller actually works from: typed code, rupees at risk,
 * age, a suggested action for each, and an expansion showing the evidence
 * as a table rather than a JSON blob. Filtering is done by the API (hop,
 * severity, code are real query parameters on
 * /api/run/{id}/exceptions) — the browser never re-derives the list.
 *
 * The refusals are lifted out of the table on purpose: AMBIGUOUS_SETTLEMENT
 * rendered as one more row among seventeen is the same mistake as burying
 * an abstention in a log. They get §2.5's mirrored card above the queue,
 * and the panel header states the split so nothing looks hidden.
 */

type SeverityFilter = "" | "critical" | "warn" | "info"

const SEVERITIES: { value: SeverityFilter; label: string }[] = [
  { value: "", label: "all severities" },
  { value: "critical", label: "critical" },
  { value: "warn", label: "warn" },
  { value: "info", label: "info" },
]

const HOPS: { value: string; label: string }[] = [
  { value: "", label: "all hops" },
  { value: "1", label: "hop 1 · order → capture" },
  { value: "2", label: "hop 2 · capture → bank" },
  { value: "3", label: "hop 3 · bank → ledger" },
]

const selectClass =
  "bg-paper border border-rule rounded-sm text-sm px-2 py-1 text-figure focus:outline-none focus:border-trace"

export function Exceptions() {
  const [runId, setRunId] = useState<string | null>(null)
  const [exceptions, setExceptions] = useState<ExceptionRecord[] | null>(null)
  const [allCodes, setAllCodes] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const [hop, setHop] = useState("")
  const [severity, setSeverity] = useState<SeverityFilter>("")
  const [code, setCode] = useState("")

  useEffect(() => {
    getLatestRun()
      .then((run) => {
        setRunId(run.run_id)
        // The unfiltered list once, purely to populate the code dropdown
        // with the codes this run actually produced.
        return getRunExceptions(run.run_id).then((res) =>
          setAllCodes([...new Set(res.exceptions.map((e) => e.code))].sort()),
        )
      })
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    if (!runId) return
    setExpanded(null)
    getRunExceptions(runId, {
      hop: hop ? Number(hop) : undefined,
      severity: severity || undefined,
      code: code || undefined,
    })
      .then((res) => setExceptions(res.exceptions))
      .catch((e) => setError(String(e)))
  }, [runId, hop, severity, code])

  const refusalPairs = useMemo(() => (exceptions ? pairRefusals(exceptions) : []), [exceptions])
  const queued = useMemo(
    () => exceptions?.filter((e) => e.code !== "AMBIGUOUS_SETTLEMENT") ?? [],
    [exceptions],
  )
  const refusedCount = (exceptions?.length ?? 0) - queued.length
  const criticalOnly = severity === "critical"

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-baseline justify-between gap-4 flex-wrap">
        <h1 className="text-lg font-semibold tracking-tight">Exceptions</h1>
        {runId && <span className="text-xs text-muted figures">{runId}</span>}
      </div>
      {error && <p className="text-sm text-flag">{error}</p>}

      <div className="flex flex-wrap items-center gap-3 border-y border-rule py-3">
        <select className={selectClass} value={hop} onChange={(e) => setHop(e.target.value)}>
          {HOPS.map((h) => (
            <option key={h.value} value={h.value}>
              {h.label}
            </option>
          ))}
        </select>
        <select
          className={selectClass}
          value={severity}
          onChange={(e) => setSeverity(e.target.value as SeverityFilter)}
        >
          {SEVERITIES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <select className={selectClass} value={code} onChange={(e) => setCode(e.target.value)}>
          <option value="">all codes</option>
          {allCodes.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {/* One control for the video, sharing the severity filter's state so
            there is only ever one source of truth for what is on screen. */}
        <button
          onClick={() => setSeverity(criticalOnly ? "" : "critical")}
          className={`text-sm px-3 py-1 rounded-sm border transition-colors ${
            criticalOnly ? "border-flag text-flag" : "border-rule text-muted hover:text-figure"
          }`}
        >
          critical only
        </button>
        {(hop || severity || code) && (
          <button
            onClick={() => {
              setHop("")
              setSeverity("")
              setCode("")
            }}
            className="text-xs text-muted hover:text-figure transition-colors"
          >
            clear filters
          </button>
        )}
      </div>

      {refusalPairs.map((pair) => (
        <RefusalCard key={pair[0].exc.exc_id} pair={pair} />
      ))}

      {exceptions && (
        <LedgerPanel
          title={`${exceptions.length} matching · ${queued.length} in the queue, ${refusedCount} refused`}
        >
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-rule text-xs text-muted">
                <th className="text-left font-medium py-2 px-3">CODE</th>
                <th className="text-left font-medium py-2 px-3">RECORDS</th>
                <th className="text-right font-medium py-2 px-3">₹ AT RISK</th>
                <th className="text-right font-medium py-2 px-3">AGE (d)</th>
                <th className="text-left font-medium py-2 px-3">SUGGESTED ACTION</th>
              </tr>
            </thead>
            <tbody>
              {queued.map((e) => {
                const open = expanded === e.exc_id
                return [
                  <tr
                    key={e.exc_id}
                    className="border-b border-rule/60 cursor-pointer hover:bg-ink/40"
                    onClick={() => setExpanded(open ? null : e.exc_id)}
                  >
                    <td className="py-2 px-3 align-top">
                      <SeverityRule severity={e.severity}>
                        <span className="flex items-center gap-2">
                          <span className="text-muted text-xs w-3">{open ? "▾" : "▸"}</span>
                          <span>{e.code}</span>
                          <span className="text-muted text-xs">hop {e.hop ?? "—"}</span>
                        </span>
                      </SeverityRule>
                    </td>
                    <td className="py-2 px-3 align-top figures text-xs">
                      {e.records.map((r) => r.id).join(", ")}
                    </td>
                    <td className="py-2 px-3 align-top text-right">
                      <Money amountP={e.amount_at_risk_p} tone="caution" />
                    </td>
                    <td className="py-2 px-3 align-top text-right figures tabular-nums">{e.age_days}</td>
                    <td className="py-2 px-3 align-top text-muted">{e.suggested_action}</td>
                  </tr>,
                  open && (
                    <tr key={`${e.exc_id}-evidence`} className="border-b border-rule/60 bg-ink/40">
                      <td colSpan={5} className="px-3 pb-4">
                        <p className="text-sm py-3">{e.explanation}</p>
                        <EvidenceTable exc={e} />
                      </td>
                    </tr>
                  ),
                ]
              })}
              {queued.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-6 px-3 text-center text-muted text-sm">
                    No exceptions match these filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </LedgerPanel>
      )}
    </div>
  )
}
