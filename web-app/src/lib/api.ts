/**
 * api.ts — typed client for recon/api.py's endpoints. Every function here
 * hits the real FastAPI app (proxied by Vite, see vite.config.ts) and
 * returns exactly what the pipeline produced — UI_SPEC.md §0's hard
 * boundary: "the frontend reads from endpoints and never computes a
 * number." No fixture, no mock, ever lives in this file.
 */

export interface RunRecord {
  src: string
  id: string
}

export interface ExceptionRecord {
  exc_id: string
  code: string
  severity: "critical" | "warn" | "info"
  hop: number | null
  records: RunRecord[]
  amount_at_risk_p: number
  age_days: number
  explanation: string
  suggested_action: string
  status: string
  evidence: unknown | null
  /** The match_link whose reconstruction explains this exception, when one
   * exists. Null for refusals (AMBIGUOUS_SETTLEMENT,
   * UNEXPLAINED_BANK_CREDIT), which propose no link by design. */
  evidence_link_id: string | null
}

/** The pipeline's own scored metrics dict (recon/scoring/scorer.py's
 * return value) — everything UI_SPEC.md §2.2's metrics band renders. */
export interface PipelineMetrics {
  link_precision: number
  link_recall: number
  false_match_rate: number
  full_chain_rate: number
  full_chain_fraction: { fully_chained: number; chainable_orders: number }
  exc_detection: number
  exc_code_accuracy: number
  tier_histogram: Record<string, number>
  hop_match: Record<string, string> // "h1": "57/57"
  llm_calls: { total: number; accepted: number; rejected: number; abstained: number }
  records_processed: number
  exceptions: { open: number; critical: number; warn: number; info: number }
  amount_at_risk_p: number
  value_reconciled_p: number
  runtime_s: number
  residual_p?: number
  narrated_exceptions?: number
  /** One entry per tier-4 adjudication call (recon/llm/adjudicator.py's
   * Tier4Stats.call_log) — the payload the model was shown, what it
   * decided, and what the verifier then did with it. Absent on an
   * --llm off run, which makes no calls. */
  llm_call_log?: LLMCall[]
}

/** One tier-4 adjudication call, as the adjudication panel renders it.
 * `verifier_outcome` is absent when the model abstained: nothing was
 * proposed, so there was no verdict to reach. */
export interface LLMCall {
  line_id: string
  payload: {
    task: string
    item: { line_id: string; value_date: string; credit_p: number; narration: string }
    candidates: {
      batch: string
      rows: number
      net_p: number
      delta_p: number
      date_gap_bdays: number
      narration_tokens_matched: string[]
    }[]
    failed_checks: string[]
    instruction: string
  }
  decision: string
  verifier_outcome?: string
  verifier_reason?: string | null
}

export interface RunMetrics {
  run_id: string
  seed: number
  llm_mode: string
  started_at: string
  finished_at: string | null
  status: "finished" | "running"
  metrics: PipelineMetrics | null
}

export interface MatchLink {
  link_id: string
  run_id: string
  hop: number
  src_a: string
  id_a: string
  src_b: string
  id_b: string
  tier: number | null
  confidence: number | null
  status: string
  reason: string
  evidence: unknown | null
  // hop-2 links only: the full settlement reconstruction, computed
  // server-side (recon/api.py::_hop2_reconstruction). Every figure the
  // reconstruction viewer shows comes from here — net_p per row,
  // reconstructed_p, delta_p — because UI_SPEC §0 forbids the browser
  // from adding anything up itself.
  bank_line?: ReconstructionBankLine | null
  rows?: ReconstructionRow[]
  reconstructed_p?: number
  delta_p?: number
}

export interface ReconstructionBankLine {
  line_id: string
  value_date: string
  credit_p: number
  narration: string
  utr_extracted: string | null
}

export interface ReconstructionRow {
  payment_id: string
  kind: string
  method: string | null
  amount_p: number
  fee_p: number
  gst_p: number
  net_p: number
}

export interface ClearingControl {
  run_id: string
  residual_p: number
  exposure_p: number
  /** residual_p - exposure_p, subtracted server-side. UI_SPEC §0: the
   * browser must not compute this itself. */
  difference_p: number
  balanced: boolean
  breakdown: Record<string, number>
  /** Every PG_RECEIVABLE line in ledger order, with a server-computed
   * running balance whose closing value is residual_p. */
  entries: ClearingEntry[]
}

export interface ClearingEntry {
  voucher_no: string
  entry_date: string
  account: string
  debit_p: number
  credit_p: number
  memo: string | null
  balance_p: number
}

export interface AskResponse {
  answer: string
  tool_calls: { name: string; input: Record<string, unknown> }[]
  record_ids: string[]
}

// Every hop/exception/match event pipeline.py emits, in emission order
// (recon/engine/events.py). Present shape — hop2/hop3/verifier.py's
// on_event calls, not yet UI_SPEC.md §3's literal SSE shape (that
// reconciliation is P8's, when the run console is the actual consumer).
export type RunEvent =
  | {
      kind: "exception"
      hop: number
      exc_id: string
      code: string
      severity: string
      amount_at_risk_p: number
      records: RunRecord[]
      seq: number
      run_id: string
    }
  | {
      kind: "match"
      hop: number
      link_id: string
      tier: number | null
      id_a: string
      id_b: string
      seq: number
      run_id: string
    }
  | {
      // A proposal the verifier threw out (V1/V4). The gutter renders these
      // beside exceptions — "verification is the product" is only visible
      // if a rejection is visible.
      kind: "rejected"
      hop: number
      link_id: string
      tier: number | null
      reason: string
      seq: number
      run_id: string
    }

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${path} -> ${res.status}: ${body}`)
  }
  return res.json() as Promise<T>
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${path} -> ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export function startRun(
  opts: { seed?: number; llmMode?: "on" | "off"; paceMs?: number; narrate?: boolean } = {},
) {
  return postJSON<{ run_id: string }>("/api/run", {
    seed: opts.seed,
    llm_mode: opts.llmMode ?? "off",
    pace_ms: opts.paceMs ?? 0,
    narrate: opts.narrate ?? true,
  })
}

/** Streams one RunEvent per SSE `data:` chunk until the server closes the
 * connection (the pipeline's sentinel). Returns an EventSource-like handle
 * the caller can close early. */
export function streamRun(runId: string, onEvent: (e: RunEvent) => void, onDone: () => void): () => void {
  const controller = new AbortController()
  fetch(`/api/run/${runId}/stream`, { signal: controller.signal })
    .then(async (res) => {
      if (!res.body) return onDone()
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n\n")
        buffer = lines.pop() ?? ""
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            onEvent(JSON.parse(line.slice("data: ".length)) as RunEvent)
          }
        }
      }
      onDone()
    })
    .catch(() => onDone())
  return () => controller.abort()
}

export const getRunMetrics = (runId: string) => getJSON<RunMetrics>(`/api/run/${runId}/metrics`)

/** The most recently finished run — how every screen other than the run
 * console names a run without having started one. 404s when the DB has
 * never completed a run. */
export const getLatestRun = () => getJSON<RunMetrics>("/api/run/latest")

export const getRunExceptions = (
  runId: string,
  filters: { hop?: number; code?: string; severity?: string } = {},
) => {
  const params = new URLSearchParams()
  if (filters.hop !== undefined) params.set("hop", String(filters.hop))
  if (filters.code) params.set("code", filters.code)
  if (filters.severity) params.set("severity", filters.severity)
  const qs = params.toString()
  return getJSON<{ run_id: string; exceptions: ExceptionRecord[] }>(
    `/api/run/${runId}/exceptions${qs ? `?${qs}` : ""}`,
  )
}

export const getMatch = (linkId: string) => getJSON<MatchLink>(`/api/match/${linkId}`)

export const getOrderChain = (orderId: string, runId?: string) =>
  getJSON<Record<string, unknown>>(`/api/order/${orderId}/chain${runId ? `?run_id=${runId}` : ""}`)

export const getClearingControl = (runId?: string) =>
  getJSON<ClearingControl>(`/api/control/clearing${runId ? `?run_id=${runId}` : ""}`)

export const askQuestion = (question: string, runId?: string) =>
  postJSON<AskResponse>("/api/ask", { question, run_id: runId })

// The latest-run report (pre-P6, still live) — used where a screen just
// needs "whatever the last run was" without triggering a new one.
export const getReport = () =>
  getJSON<{
    error?: string
    run_id?: string
    seed?: number
    llm_mode?: string
    finished_at?: string
    metrics?: Record<string, unknown>
    exceptions?: ExceptionRecord[]
  }>("/report")
