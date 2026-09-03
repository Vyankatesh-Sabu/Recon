import { useEffect, useState } from "react"
import { LedgerPanel } from "../components/LedgerPanel"
import { LedgerTable, type LedgerColumn } from "../components/LedgerTable"
import { Money } from "../components/Money"
import { getClearingControl, type ClearingControl as ClearingControlData, type ClearingEntry } from "../lib/api"

/**
 * UI_SPEC.md §2.6 — the clearing account control (video beat 6).
 *
 * A running T-account for PG_RECEIVABLE with the balance tracking down the
 * page, then the three-line control block. The T-account's closing balance
 * and the GL residual are the same number by construction — same rows,
 * summed two ways — and the API asserts that before returning, so a
 * disagreement is a server-side error rather than a screen that quietly
 * contradicts itself.
 *
 * `difference_p` comes from the API. The browser must not subtract the two
 * control numbers itself (UI_SPEC §0): the entire claim is that these are
 * computed by independent paths, and a subtraction done here would be a
 * third path nobody verified.
 */
export function ClearingControl() {
  const [data, setData] = useState<ClearingControlData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getClearingControl()
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  const columns: LedgerColumn<ClearingEntry>[] = [
    { header: "DATE", figures: true, render: (e) => e.entry_date },
    { header: "VOUCHER", figures: true, render: (e) => e.voucher_no },
    { header: "MEMO", render: (e) => <span className="text-xs text-muted">{e.memo ?? "—"}</span> },
    {
      header: "DEBIT",
      align: "right",
      render: (e) => (e.debit_p ? <Money amountP={e.debit_p} /> : <span className="text-muted">—</span>),
    },
    {
      header: "CREDIT",
      align: "right",
      render: (e) => (e.credit_p ? <Money amountP={e.credit_p} /> : <span className="text-muted">—</span>),
    },
    { header: "BALANCE", align: "right", render: (e) => <Money amountP={e.balance_p} /> },
  ]

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-lg font-semibold tracking-tight">Clearing account control</h1>
      {error && <p className="text-sm text-flag">{error}</p>}

      {data && (
        <>
          <LedgerPanel title={`PG_RECEIVABLE · ${data.entries.length} journal lines · ${data.run_id}`}>
            <div className="overflow-x-auto">
              <LedgerTable
                columns={columns}
                rows={data.entries}
                rowKey={(e) => `${e.voucher_no}-${e.entry_date}-${e.balance_p}`}
              />
            </div>
          </LedgerPanel>

          <LedgerPanel title="The control">
            <div className="flex flex-col gap-2 text-sm max-w-2xl">
              <div className="flex justify-between gap-8">
                <span className="text-muted">GL residual, computed from journal entries alone</span>
                <Money amountP={data.residual_p} />
              </div>
              <div className="flex justify-between gap-8">
                <span className="text-muted">Exception exposure, computed from the queue alone</span>
                <Money amountP={data.exposure_p} />
              </div>
              <div className="border-t border-rule mt-1 pt-2 flex justify-between gap-8 font-medium">
                <span>Difference</span>
                <span className="flex items-center gap-2">
                  <Money amountP={data.difference_p} tone={data.balanced ? "verified" : "flag"} />
                  {data.balanced && <span className="text-verified">✓</span>}
                </span>
              </div>
            </div>
            <p className="mt-4 text-xs text-muted max-w-2xl leading-relaxed">
              Two numbers derived by completely independent paths, equal to the paisa. If they ever
              disagree the run aborts — the control exists to catch this pipeline's own bugs, and it
              has caught two.
            </p>
          </LedgerPanel>

          <LedgerPanel title="Exposure by exception code">
            <div className="flex flex-col gap-1 text-sm max-w-2xl">
              {Object.entries(data.breakdown).map(([code, amount]) => (
                <div key={code} className="flex justify-between gap-8">
                  <span className="text-muted">{code}</span>
                  <Money amountP={amount} tone={amount < 0 ? "flag" : "figure"} />
                </div>
              ))}
            </div>
          </LedgerPanel>
        </>
      )}
    </div>
  )
}
