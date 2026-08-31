import { useEffect, useState } from "react"
import { LedgerPanel } from "../components/LedgerPanel"
import { LedgerTable, type LedgerColumn } from "../components/LedgerTable"
import { Money } from "../components/Money"
import { SeverityRule } from "../components/SeverityRule"
import { getReport, getRunExceptions, type ExceptionRecord } from "../lib/api"

/** P7 shell stub for UI_SPEC.md §2.4 — real GET /api/run/{id}/exceptions
 * for the latest run (found via /report, which is the only endpoint that
 * knows "latest" without a run_id in hand), sorted by ₹ at risk, severity
 * as a coloured left edge per spec. Filters and the evidence-expansion row
 * are P9's job. (/report itself doesn't carry `hop` per exception — the
 * P6 endpoint does, which is why this fetches through it instead.) */
export function Exceptions() {
  const [exceptions, setExceptions] = useState<ExceptionRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getReport()
      .then((r) => {
        if (r.error || !r.run_id) {
          setError(r.error ?? "no completed run found")
          return
        }
        return getRunExceptions(r.run_id).then((res) => setExceptions(res.exceptions))
      })
      .catch((e) => setError(String(e)))
  }, [])

  const columns: LedgerColumn<ExceptionRecord>[] = [
    {
      header: "CODE",
      render: (e) => (
        <SeverityRule severity={e.severity}>
          <span className="text-sm">{e.code}</span>
        </SeverityRule>
      ),
    },
    { header: "HOP", figures: true, render: (e) => e.hop ?? "—" },
    { header: "₹ AT RISK", align: "right", render: (e) => <Money amountP={e.amount_at_risk_p} tone="caution" /> },
    { header: "AGE (d)", align: "right", figures: true, render: (e) => e.age_days },
    { header: "EXPLANATION", render: (e) => <span className="text-sm text-muted">{e.explanation}</span> },
  ]

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-lg font-semibold tracking-tight">Exceptions</h1>
      {error && <p className="text-sm text-flag">{error}</p>}
      {exceptions && (
        <LedgerPanel title={`${exceptions.length} open`}>
          <LedgerTable columns={columns} rows={exceptions} rowKey={(e) => e.exc_id} />
        </LedgerPanel>
      )}
    </div>
  )
}
