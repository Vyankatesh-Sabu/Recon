import { useState, type FormEvent } from "react"
import { LedgerPanel } from "../components/LedgerPanel"
import { askQuestion, type AskResponse } from "../lib/api"

const SUGGESTIONS = [
  "Which two settlements can't you tell apart, and why?",
  "Trace ORD-1017 end to end.",
  "What is the largest exception by rupees at risk?",
]

/**
 * UI_SPEC.md §2.8 — the Q&A console, where **the tool calls are visible**.
 *
 * That is the whole point of the screen: showing `⟶ list_exceptions()` and
 * the line that came back demonstrates the model retrieved rather than
 * recalled. The summary on each `⟵` line is computed server-side
 * (recon/llm/qa.py::_summarize) from the tool's own output — the console
 * displays retrieval, it does not interpret it.
 *
 * Prose in Inter, IDs and figures in Plex Mono, so the grounding is
 * visible typographically: mono means the system produced this, sans means
 * a human (or a model) is explaining.
 */
export function AskConsole() {
  const [question, setQuestion] = useState("")
  const [asked, setAsked] = useState<string | null>(null)
  const [result, setResult] = useState<AskResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function ask(q: string) {
    const trimmed = q.trim()
    if (!trimmed) return
    setLoading(true)
    setError(null)
    setResult(null)
    setAsked(trimmed)
    try {
      setResult(await askQuestion(trimmed))
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    void ask(question)
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-lg font-semibold tracking-tight">Ask the reconciliation agent</h1>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Which two settlements can't you tell apart, and why?"
          className="bg-paper border border-rule rounded-sm px-3 py-1.5 text-sm flex-1 outline-none focus:border-trace"
        />
        <button
          disabled={loading}
          className="text-sm px-3 py-1.5 rounded-sm border border-rule hover:border-trace hover:text-trace transition-colors disabled:opacity-50"
        >
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>

      <div className="flex flex-wrap gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => {
              setQuestion(s)
              void ask(s)
            }}
            disabled={loading}
            className="text-xs px-2 py-1 rounded-sm border border-rule text-muted hover:text-figure hover:border-trace transition-colors disabled:opacity-50"
          >
            {s}
          </button>
        ))}
      </div>

      {error && <p className="text-sm text-flag">{error}</p>}

      {(asked || result) && (
        <LedgerPanel>
          <div className="flex flex-col gap-4 text-sm">
            <Line label="you">
              <span>{asked}</span>
            </Line>

            {/* The retrieval, in the order it happened. A call whose result
                has not come back yet shows no ⟵ line rather than a
                placeholder that would imply an answer exists. */}
            {result?.tool_calls.map((call, i) => (
              <div key={i} className="flex flex-col gap-0.5 pl-14">
                <p className="figures text-xs text-trace">
                  ⟶ {call.name}({formatArgs(call.input)})
                </p>
                {result.tool_results[i] && (
                  <p className="figures text-xs text-muted">⟵ {result.tool_results[i].summary}</p>
                )}
              </div>
            ))}

            {loading && <p className="pl-14 text-xs text-muted">retrieving…</p>}

            {result && (
              <Line label="claude">
                <div className="flex flex-col gap-3">
                  <p className="whitespace-pre-wrap leading-relaxed">{result.answer}</p>
                  {result.record_ids.length > 0 && (
                    <p className="figures text-xs text-muted break-all">
                      records: {result.record_ids.join("  ")}
                    </p>
                  )}
                </div>
              </Line>
            )}
          </div>
        </LedgerPanel>
      )}
    </div>
  )
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4">
      <span className="text-xs text-muted w-10 shrink-0 pt-0.5">{label}</span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

/** `{"ref": "setl_0812"}` reads as `ref="setl_0812"` — the call as it would
 * be written, not as JSON transport. */
function formatArgs(input: Record<string, unknown>): string {
  return Object.entries(input)
    .map(([k, v]) => `${k}=${typeof v === "string" ? `"${v}"` : String(v)}`)
    .join(", ")
}
