import { LedgerPanel } from "./LedgerPanel"
import { Money } from "./Money"
import type { LLMCall } from "../lib/api"

/**
 * AdjudicationPanel — one row per tier-4 call: what the model was shown,
 * what it decided, and what the verifier then did with it.
 *
 * The last column is the point of the whole screen. "The verifier catches
 * the model" is only a claim until a decision and an independent verdict
 * sit on the same line. An abstention shows "—" rather than a verdict,
 * because nothing was proposed and there was nothing to check — reading
 * that as approval would be exactly the overstatement this project is
 * trying not to make.
 *
 * Figures in mono, prose in sans (UI_SPEC §1): what the model wrote is a
 * human explaining, what it was shown is data the system produced.
 */

function outcomeTone(outcome: string | undefined): string {
  if (outcome === "rejected") return "text-flag"
  if (outcome === "accepted") return "text-verified"
  return "text-muted"
}

export function AdjudicationPanel({ calls }: { calls: LLMCall[] }) {
  return (
    <LedgerPanel title={`Adjudication · ${calls.length} tier-4 call${calls.length === 1 ? "" : "s"}`}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-rule text-xs text-muted">
              <th className="text-left font-medium py-2 pr-6">BANK LINE</th>
              <th className="text-left font-medium py-2 pr-6">CANDIDATES OFFERED</th>
              <th className="text-left font-medium py-2 pr-6">MODEL DECISION</th>
              <th className="text-left font-medium py-2">VERIFIER OUTCOME</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((call) => (
              <tr key={call.line_id} className="border-b border-rule/60 last:border-0 align-top">
                <td className="py-2 pr-6 figures">
                  <div>{call.line_id}</div>
                  <div className="text-xs text-muted">
                    <Money amountP={call.payload.item.credit_p} /> · {call.payload.item.value_date}
                  </div>
                </td>
                <td className="py-2 pr-6">
                  {call.payload.candidates.length === 0 ? (
                    <span className="text-muted">none</span>
                  ) : (
                    <ul className="flex flex-col gap-0.5">
                      {call.payload.candidates.map((c) => (
                        <li key={c.batch} className="figures text-xs">
                          {c.batch} · {c.rows} rows · <Money amountP={c.net_p} /> · delta{" "}
                          <Money amountP={c.delta_p} tone={c.delta_p === 0 ? "verified" : "caution"} />
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="py-2 pr-6 max-w-xs">{call.decision}</td>
                <td className={`py-2 ${outcomeTone(call.verifier_outcome)}`}>
                  {call.verifier_outcome ? (
                    <>
                      <div>{call.verifier_outcome}</div>
                      {call.verifier_reason && (
                        <div className="text-xs text-muted">{call.verifier_reason}</div>
                      )}
                    </>
                  ) : (
                    // Abstained: no proposal was made, so there is no verdict.
                    <span title="the model abstained — nothing was proposed, so nothing was verified">
                      —
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-4 text-xs text-muted max-w-3xl leading-relaxed">
        The model may propose on residue the deterministic tiers could not resolve; every proposal
        goes through the same verifier as everything else. On a genuine tie it is not allowed to
        propose at all, because arithmetic cannot tell a lucky pick from a wrong one.
      </p>
    </LedgerPanel>
  )
}
