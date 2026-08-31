import { useState, type FormEvent } from "react"
import { LedgerPanel } from "../components/LedgerPanel"
import { getOrderChain } from "../lib/api"

/** P7 shell stub for UI_SPEC.md §2.7 — real GET /api/order/{id}/chain on
 * submit. The horizontal four-hop visualization is P10's job; this
 * renders the raw chain (still real, still ledger-typeset) to prove the
 * lookup wires end to end. */
export function ChainExplorer() {
  const [orderId, setOrderId] = useState("")
  const [chain, setChain] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setChain(null)
    try {
      setChain(await getOrderChain(orderId.trim()))
    } catch (err) {
      setError(String(err))
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-lg font-semibold tracking-tight">Chain explorer</h1>
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          value={orderId}
          onChange={(e) => setOrderId(e.target.value)}
          placeholder="ORD-1001"
          className="figures bg-paper border border-rule rounded-sm px-3 py-1.5 text-sm flex-1 max-w-xs outline-none focus:border-trace"
        />
        <button className="text-sm px-3 py-1.5 rounded-sm border border-rule hover:border-trace hover:text-trace transition-colors">
          Trace
        </button>
      </form>
      {error && <p className="text-sm text-flag">{error}</p>}
      {chain && (
        <LedgerPanel title={orderId}>
          <pre className="figures text-xs text-muted whitespace-pre-wrap">{JSON.stringify(chain, null, 2)}</pre>
        </LedgerPanel>
      )}
    </div>
  )
}
