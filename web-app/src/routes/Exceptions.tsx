import { useEffect, useState } from "react"
import { LedgerPanel } from "../components/LedgerPanel"
import { LedgerTable, type LedgerColumn } from "../components/LedgerTable"
import { Money } from "../components/Money"
import { RefusalCard, pairRefusals } from "../components/RefusalCard"
import { SeverityRule } from "../components/SeverityRule"
import { getLatestRun, getRunExceptions, type ExceptionRecord } from "../lib/api"

/** UI_SPEC.md §2.4 (queue) + §2.5 (the refusal card).
 *
 * The refusals are pulled OUT of the table on purpose: AMBIGUOUS_SETTLEMENT
 * rendered as one more row among seventeen is the same mistake as burying
 * the abstention in a log. It gets its own mirrored presentation above the
 * queue, and the queue below it lists everything the engine could type and
 * price. Filters and evidence expansion arrive in step 5. */
export function Exceptions() {
  const [exceptions, setExceptions] = useState<ExceptionRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getLatestRun()
      .then((run) => getRunExceptions(run.run_id))
      .then((res) => setExceptions(res.exceptions))
      .catch((e) => setError(String(e)))
  }, [])

  const refusalPairs = exceptions ? pairRefusals(exceptions) : []
  const queued = exceptions?.filter((e) => e.code !== "AMBIGUOUS_SETTLEMENT") ?? []

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

      {refusalPairs.map((pair) => (
        <RefusalCard key={pair[0].exc.exc_id} pair={pair} />
      ))}

      {exceptions && (
        <LedgerPanel title={`${exceptions.length} open · ${queued.length} typed, ${exceptions.length - queued.length} refused`}>
          <LedgerTable columns={columns} rows={queued} rowKey={(e) => e.exc_id} />
        </LedgerPanel>
      )}
    </div>
  )
}
