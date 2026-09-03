import { useEffect, useRef, useState } from "react"
import { MetricsBand } from "../components/MetricsBand"
import { ReconstructionViewer } from "../components/ReconstructionViewer"
import { RunCanvas } from "../components/RunCanvas"
import { applyMatchEvent, initialChainState, type ChainState } from "../lib/runChains"
import { getRunMetrics, startRun, streamRun, type PipelineMetrics, type RunEvent } from "../lib/api"

type ExceptionEvent = Extract<RunEvent, { kind: "exception" }>
type Status = "idle" | "running" | "done" | "error"

function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const m = Math.floor(totalSeconds / 60)
  const s = totalSeconds % 60
  return `${m}:${String(s).padStart(2, "0")}`
}

/**
 * UI_SPEC.md §2.1 (the hero) + §2.2 (metrics band). Real POST /api/run +
 * real SSE stream (recon/engine/events.py) drive everything on screen —
 * no fixture ever stands in for a record here (§0's hard boundary). On
 * completion the metrics band slides up beneath, fetched from GET
 * /api/run/{id}/metrics — the same numbers `recon.cli report` prints.
 */
export function RunConsole() {
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<Status>("idle")
  const [error, setError] = useState<string | null>(null)
  const [chain, setChain] = useState<ChainState>(initialChainState())
  const [gutter, setGutter] = useState<ExceptionEvent[]>([])
  const [hopCounts, setHopCounts] = useState<Record<1 | 2 | 3, number>>({ 1: 0, 2: 0, 3: 0 })
  const [llmCalls, setLlmCalls] = useState(0)
  const [rejected, setRejected] = useState(0)
  const [recordsSeen, setRecordsSeen] = useState(0)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [metrics, setMetrics] = useState<PipelineMetrics | null>(null)
  // The tier-2 row whose reconstruction is open (UI_SPEC §2.3), if any.
  const [openLinkId, setOpenLinkId] = useState<string | null>(null)

  const seenIds = useRef<Set<string>>(new Set())
  const startedAt = useRef<number>(0)
  const stopStream = useRef<(() => void) | null>(null)

  useEffect(() => {
    if (status !== "running") return
    const interval = setInterval(() => setElapsedMs(Date.now() - startedAt.current), 100)
    return () => clearInterval(interval)
  }, [status])

  function handleEvent(event: RunEvent) {
    if (event.kind === "exception") {
      setGutter((prev) => [...prev, event])
      for (const r of event.records) seenIds.current.add(r.id)
    } else if (event.kind === "rejected") {
      // Rejections get their own gutter chip in ROADMAP step 6; until then
      // they are counted rather than rendered. Seed 42 with the LLM off
      // produces none — every proposal survives V1 — so nothing the demo
      // path emits is being dropped here.
      setRejected((n) => n + 1)
    } else {
      setChain((prev) => applyMatchEvent(prev, event))
      seenIds.current.add(event.id_a)
      seenIds.current.add(event.id_b)
      setHopCounts((prev) => ({ ...prev, [event.hop]: prev[event.hop as 1 | 2 | 3] + 1 }))
      if (event.tier === 4) setLlmCalls((n) => n + 1)
    }
    setRecordsSeen(seenIds.current.size)
  }

  async function handleStart() {
    setStatus("running")
    setError(null)
    setChain(initialChainState())
    setGutter([])
    setHopCounts({ 1: 0, 2: 0, 3: 0 })
    setLlmCalls(0)
    setRejected(0)
    setRecordsSeen(0)
    setMetrics(null)
    setOpenLinkId(null)
    seenIds.current = new Set()
    startedAt.current = Date.now()
    setElapsedMs(0)

    try {
      const { run_id } = await startRun({ llmMode: "off" })
      setRunId(run_id)
      stopStream.current = streamRun(
        run_id,
        handleEvent,
        () => {
          setStatus("done")
          getRunMetrics(run_id)
            .then((r) => setMetrics(r.metrics))
            .catch((e) => setError(String(e)))
        },
      )
    } catch (e) {
      setStatus("error")
      setError(String(e))
    }
  }

  useEffect(() => () => stopStream.current?.(), [])

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Run console</h1>
        <button
          onClick={handleStart}
          disabled={status === "running"}
          className="text-sm px-3 py-1.5 rounded-sm border border-rule text-figure hover:border-trace hover:text-trace transition-colors disabled:opacity-50 disabled:cursor-default"
        >
          {status === "running" ? "Running…" : "Start run"}
        </button>
      </div>

      {runId && <p className="text-xs text-muted figures">{runId}</p>}
      {error && <p className="text-sm text-flag">{error}</p>}

      <div className="flex gap-8 text-sm border-y border-rule py-3">
        <div>
          <span className="text-muted text-xs">records processed </span>
          <span className="figures tabular-nums">{recordsSeen}</span>
        </div>
        <div>
          <span className="text-muted text-xs">hop rates </span>
          <span className="figures tabular-nums">
            H1 {hopCounts[1]} · H2 {hopCounts[2]} · H3 {hopCounts[3]}
          </span>
        </div>
        <div>
          <span className="text-muted text-xs">elapsed </span>
          <span className="figures tabular-nums">{formatElapsed(elapsedMs)}</span>
        </div>
        <div>
          <span className="text-muted text-xs">LLM calls </span>
          <span className="figures tabular-nums">{llmCalls}</span>
        </div>
        <div>
          <span className="text-muted text-xs">rejected by verifier </span>
          <span className={`figures tabular-nums ${rejected > 0 ? "text-flag" : ""}`}>{rejected}</span>
        </div>
      </div>

      <RunCanvas rows={chain.rows} gutter={gutter} onOpenReconstruction={setOpenLinkId} />

      {openLinkId && <ReconstructionViewer linkId={openLinkId} onClose={() => setOpenLinkId(null)} />}

      {metrics && <MetricsBand metrics={metrics} />}
    </div>
  )
}
