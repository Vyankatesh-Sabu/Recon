import { useEffect, useState, type FormEvent } from "react"
import { ChainStrip } from "../components/ChainStrip"
import { getOrderChain, type OrderChain } from "../lib/api"

/** UI_SPEC.md §2.7. Loads the two seed-42 orders the spec asks for side by
 * side — an intact chain next to a broken one — because the comparison is
 * the point: "good for answering a live question and for showing an intact
 * chain next to a broken one." Searching replaces the pair with one order. */
const DEFAULT_PAIR = ["ORD-1017", "ORD-1058"]

export function ChainExplorer() {
  const [orderId, setOrderId] = useState("")
  const [chains, setChains] = useState<OrderChain[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all(DEFAULT_PAIR.map((id) => getOrderChain(id)))
      .then(setChains)
      .catch((e) => setError(String(e)))
  }, [])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const id = orderId.trim()
    if (!id) return
    setError(null)
    try {
      setChains([await getOrderChain(id)])
    } catch (err) {
      setChains([])
      setError(String(err))
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-lg font-semibold tracking-tight">Chain explorer</h1>

      <form onSubmit={handleSubmit} className="flex gap-2 items-center">
        <input
          value={orderId}
          onChange={(e) => setOrderId(e.target.value)}
          placeholder="ORD-1017"
          className="figures bg-paper border border-rule rounded-sm px-3 py-1.5 text-sm flex-1 max-w-xs outline-none focus:border-trace"
        />
        <button className="text-sm px-3 py-1.5 rounded-sm border border-rule hover:border-trace hover:text-trace transition-colors">
          Trace
        </button>
        {chains.length === 1 && (
          <button
            type="button"
            onClick={() => {
              setOrderId("")
              setError(null)
              Promise.all(DEFAULT_PAIR.map((id) => getOrderChain(id)))
                .then(setChains)
                .catch((e) => setError(String(e)))
            }}
            className="text-xs text-muted hover:text-figure transition-colors"
          >
            show the pair
          </button>
        )}
      </form>

      {error && <p className="text-sm text-flag">{error}</p>}

      {chains.map((chain) => (
        <ChainStrip key={chain.order.order_id} chain={chain} />
      ))}
    </div>
  )
}
