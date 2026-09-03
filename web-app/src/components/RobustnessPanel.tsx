import { useState } from "react"
import { LedgerPanel } from "./LedgerPanel"
import { runEval, type EvalSummary } from "../lib/api"

const COUNTS = [100, 250, 500] as const

/**
 * RobustnessPanel — the accuracy claim, measured live instead of quoted.
 *
 * "One cherry-picked match proves nothing" is the brief's own phrase, so
 * the answer to it should not be a number typed into a frontend. This runs
 * `tests/eval_multi_seed.py` in-process through GET /api/eval and renders
 * whatever comes back — including, if it ever happens, a nonzero
 * false-match rate, which is displayed as the loudest thing on the panel
 * rather than averaged away.
 */
export function RobustnessPanel() {
  const [summary, setSummary] = useState<EvalSummary | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run(count: number) {
    setRunning(true)
    setError(null)
    setSummary(null)
    try {
      setSummary(await runEval(count))
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  const clean = summary && summary.nonzero_false_match_seeds.length === 0 && summary.pipeline_aborted === 0

  return (
    <LedgerPanel title="Robustness — the pipeline across independently generated worlds">
      <div className="flex flex-wrap items-center gap-2">
        {COUNTS.map((n) => (
          <button
            key={n}
            onClick={() => run(n)}
            disabled={running}
            className="text-sm px-3 py-1.5 rounded-sm border border-rule text-figure hover:border-trace hover:text-trace transition-colors disabled:opacity-50 disabled:cursor-default"
          >
            {running ? "Running…" : `Run ${n} worlds`}
          </button>
        ))}
        <span className="text-xs text-muted">
          each world is generated, loaded and reconciled in its own temporary database
        </span>
      </div>

      {error && <p className="text-sm text-flag mt-4">{error}</p>}

      {summary && (
        <div className="mt-5 flex flex-col gap-5">
          <div className="overflow-x-auto">
            <table className="text-sm border-collapse min-w-full">
              <thead>
                <tr className="border-b border-rule text-xs text-muted">
                  <th className="text-left font-medium py-2 pr-8">WORLDS ATTEMPTED</th>
                  <th className="text-left font-medium py-2 pr-8">COMPLETED</th>
                  <th className="text-left font-medium py-2 pr-8">FALSE-MATCH RATE</th>
                  <th className="text-left font-medium py-2">CLEARING-CONTROL ABORTS</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="py-2 pr-8 figures tabular-nums">{summary.count}</td>
                  <td className="py-2 pr-8 figures tabular-nums">{summary.completed}</td>
                  <td
                    className={`py-2 pr-8 figures tabular-nums ${
                      summary.nonzero_false_match_seeds.length === 0 ? "text-verified" : "text-flag"
                    }`}
                  >
                    {summary.false_match_rate
                      ? `${(summary.false_match_rate.max * 100).toFixed(1)}% on all ${summary.completed}`
                      : "—"}
                  </td>
                  <td
                    className={`py-2 figures tabular-nums ${
                      summary.pipeline_aborted === 0 ? "text-verified" : "text-flag"
                    }`}
                  >
                    {summary.pipeline_aborted}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {summary.nonzero_false_match_seeds.length > 0 && (
            <p className="text-sm text-flag">
              Seeds with a nonzero false-match rate:{" "}
              <span className="figures">{summary.nonzero_false_match_seeds.join(", ")}</span>
            </p>
          )}
          {summary.aborted_seeds.length > 0 && (
            <p className="text-sm text-flag">
              Seeds where the clearing control aborted the run:{" "}
              <span className="figures">{summary.aborted_seeds.join(", ")}</span>
            </p>
          )}

          <div className="flex flex-col gap-1 text-sm max-w-2xl">
            <Spread label="link precision" spread={summary.link_precision} />
            <Spread label="link recall" spread={summary.link_recall} />
            <Spread label="full chain rate" spread={summary.full_chain_rate} />
          </div>

          <p className="text-xs text-muted max-w-2xl leading-relaxed">
            {summary.generation_failed} of {summary.count} seeds could not generate a world at all —
            a defect injector found no structurally valid candidate in that particular world (mostly
            D-01, which needs a 5–7 row batch with exactly one refund). That is a generator
            limitation, not a matching failure, and it is counted here rather than dropped.{" "}
            {clean
              ? `Every world that completed did so with a 0.0% false-match rate and no clearing-control abort. ${summary.elapsed_s.toFixed(1)}s.`
              : "See the flagged seeds above."}
          </p>
        </div>
      )}
    </LedgerPanel>
  )
}

function Spread({
  label,
  spread,
}: {
  label: string
  spread: { min: number; max: number; mean: number } | null
}) {
  if (!spread) return null
  return (
    <div className="flex justify-between gap-8">
      <span className="text-muted">{label}</span>
      <span className="figures tabular-nums">
        {spread.min.toFixed(4)} – {spread.max.toFixed(4)} · mean {spread.mean.toFixed(4)}
      </span>
    </div>
  )
}
