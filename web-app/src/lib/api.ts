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
}

export interface RunMetrics {
  run_id: string
  seed: number
  llm_mode: string
  started_at: string
  finished_at: string | null
  status: "finished" | "running"
  metrics: Record<string, unknown> | null
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
}

export interface ClearingControl {
  run_id: string
  residual_p: number
  exposure_p: number
  balanced: boolean
  breakdown: Record<string, number>
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

export function startRun(opts: { seed?: number; llmMode?: "on" | "off"; paceMs?: number } = {}) {
  return postJSON<{ run_id: string }>("/api/run", {
    seed: opts.seed,
    llm_mode: opts.llmMode ?? "off",
    pace_ms: opts.paceMs ?? 0,
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
