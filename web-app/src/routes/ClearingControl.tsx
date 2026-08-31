import { useEffect, useState } from "react"
import { LedgerPanel } from "../components/LedgerPanel"
import { Money } from "../components/Money"
import { getClearingControl, type ClearingControl as ClearingControlData } from "../lib/api"

/** P7 shell stub for UI_SPEC.md §2.6 — real GET /api/control/clearing.
 * The T-account visualization is P9's job; this renders the two-number
 * control line itself, which is the part that actually carries the claim. */
export function ClearingControl() {
  const [data, setData] = useState<ClearingControlData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getClearingControl()
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-lg font-semibold tracking-tight">Clearing account control</h1>
      {error && <p className="text-sm text-flag">{error}</p>}
      {data && (
        <LedgerPanel title={data.run_id}>
          <div className="flex flex-col gap-2 text-sm max-w-lg">
            <div className="flex justify-between">
              <span className="text-muted">GL residual, computed from journal entries alone</span>
              <Money amountP={data.residual_p} />
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Exception exposure, computed from the queue alone</span>
              <Money amountP={data.exposure_p} />
            </div>
            <div className="border-t border-rule mt-1 pt-2 flex justify-between font-medium">
              <span>Difference</span>
              <Money amountP={data.residual_p - data.exposure_p} tone={data.balanced ? "verified" : "flag"} />
            </div>
          </div>

          <div className="mt-6">
            <p className="text-xs text-muted mb-2">Per-code breakdown</p>
            <div className="flex flex-col gap-1 text-sm figures">
              {Object.entries(data.breakdown).map(([code, amount]) => (
                <div key={code} className="flex justify-between">
                  <span className="font-sans text-muted">{code}</span>
                  <Money amountP={amount} tone={amount < 0 ? "flag" : "figure"} />
                </div>
              ))}
            </div>
          </div>
        </LedgerPanel>
      )}
    </div>
  )
}
