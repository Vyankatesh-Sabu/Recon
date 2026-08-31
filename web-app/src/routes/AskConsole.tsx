import { useState, type FormEvent } from "react"
import { LedgerPanel } from "../components/LedgerPanel"
import { askQuestion, type AskResponse } from "../lib/api"

/** P7 shell stub for UI_SPEC.md §2.8 — real POST /api/ask. Tool calls are
 * shown, not hidden, per spec ("it demonstrates the model retrieved
 * rather than recalled"), even in this minimal form; the chat-log framing
 * and mono/sans typographic split for grounding is P10's job. */
export function AskConsole() {
  const [question, setQuestion] = useState("")
  const [result, setResult] = useState<AskResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      setResult(await askQuestion(question.trim()))
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-lg font-semibold tracking-tight">Ask the reconciliation agent</h1>
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Trace ORD-1001 end to end."
          className="bg-paper border border-rule rounded-sm px-3 py-1.5 text-sm flex-1 outline-none focus:border-trace"
        />
        <button
          disabled={loading}
          className="text-sm px-3 py-1.5 rounded-sm border border-rule hover:border-trace hover:text-trace transition-colors disabled:opacity-50"
        >
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>
      {error && <p className="text-sm text-flag">{error}</p>}
      {result && (
        <LedgerPanel>
          <div className="flex flex-col gap-3">
            {result.tool_calls.map((call, i) => (
              <p key={i} className="figures text-xs text-trace">
                ⟶ {call.name}({JSON.stringify(call.input)})
              </p>
            ))}
            <p className="text-sm">{result.answer}</p>
            {result.record_ids.length > 0 && (
              <p className="figures text-xs text-muted">records: {result.record_ids.join(" ")}</p>
            )}
          </div>
        </LedgerPanel>
      )}
    </div>
  )
}
