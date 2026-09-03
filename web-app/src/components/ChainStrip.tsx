import { Money } from "./Money"
import { SeverityRule } from "./SeverityRule"
import type { OrderChain } from "../lib/api"

/**
 * ChainStrip — UI_SPEC.md §2.7: one order's four hops laid out
 * horizontally, per-hop status, and any exception on a broken hop shown
 * inline rather than linked away to.
 *
 * A hop with no accepted link renders as a broken connector, not as an
 * empty box: an order that stops at the gateway and an order that never
 * reached it should not look the same, and the reason it stopped is right
 * there under the break.
 */

type Stage = {
  label: string
  id: string | null
  detail?: string | null
  /** Status of the hop that LEADS INTO this stage; null on the first. */
  hop: string | null
}

function stages(chain: OrderChain): Stage[] {
  return [
    { label: "ORDER", id: chain.order.order_id, detail: chain.order.created_on, hop: null },
    {
      label: "GATEWAY",
      id: chain.capture?.payment_id ?? null,
      detail: chain.capture?.captured_on ?? null,
      hop: chain.hops.h1,
    },
    {
      label: "BANK",
      id: chain.settlement.bank_line,
      detail: chain.settlement.batch,
      hop: chain.hops.h2,
    },
    {
      label: "LEDGER",
      id: chain.gl.vouchers[0] ?? null,
      detail: chain.gl.vouchers.length > 1 ? `+${chain.gl.vouchers.length - 1} more` : null,
      hop: chain.hops.h3,
    },
  ]
}

function Connector({ hop }: { hop: string | null }) {
  const accepted = hop === "accepted"
  return (
    <div className="flex flex-col items-center justify-center px-2 shrink-0 self-stretch">
      <div
        className="h-px w-8"
        style={{
          background: accepted ? "var(--verified)" : "var(--flag)",
          // A broken hop is a dashed rule, so it reads as broken even
          // where colour alone might not.
          ...(accepted ? {} : { backgroundImage: "none", borderTop: "1px dashed var(--flag)", height: 0 }),
        }}
      />
      <span className={`text-[10px] mt-1 ${accepted ? "text-verified" : "text-flag"}`}>
        {hop ?? "broken"}
      </span>
    </div>
  )
}

function StageBox({ stage }: { stage: Stage }) {
  return (
    <div className="flex-1 min-w-0 border border-rule rounded-sm px-3 py-2 bg-ink/40">
      <p className="text-[10px] text-muted tracking-wide">{stage.label}</p>
      {stage.id ? (
        <>
          <p className="figures text-sm truncate">{stage.id}</p>
          {stage.detail && <p className="figures text-xs text-muted truncate">{stage.detail}</p>}
        </>
      ) : (
        <p className="text-sm text-muted">—</p>
      )}
    </div>
  )
}

export function ChainStrip({ chain }: { chain: OrderChain }) {
  const list = stages(chain)
  const intact = list.every((s) => s.hop === null || s.hop === "accepted") && list.every((s) => s.id)

  return (
    <div className="bg-paper border border-rule rounded-sm">
      <div className="border-b border-rule px-4 py-2.5 flex items-baseline justify-between gap-4">
        <span className="figures text-sm">{chain.order.order_id}</span>
        <span className="flex items-baseline gap-3 text-xs">
          <Money amountP={chain.order.amount_p} />
          <span className={intact ? "text-verified" : "text-flag"}>
            {intact ? "full chain ✓" : "broken"}
          </span>
        </span>
      </div>

      <div className="p-4">
        <div className="flex items-stretch overflow-x-auto">
          {list.map((stage, i) => (
            <div key={stage.label} className="flex items-stretch flex-1 min-w-[7rem]">
              {i > 0 && <Connector hop={stage.hop} />}
              <StageBox stage={stage} />
            </div>
          ))}
        </div>

        {chain.exceptions.length > 0 && (
          <div className="mt-4 flex flex-col gap-2">
            {chain.exceptions.map((exc) => (
              <SeverityRule key={exc.exc_id} severity={exc.severity}>
                <div className="text-sm">
                  <span className="figures text-xs">{exc.code}</span>{" "}
                  <Money amountP={exc.amount_at_risk_p} tone="caution" />
                  <p className="text-muted text-xs mt-0.5">{exc.explanation}</p>
                  <p className="text-muted text-xs">→ {exc.suggested_action}</p>
                </div>
              </SeverityRule>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
