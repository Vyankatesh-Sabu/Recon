import type { RunEvent } from "./api"

/** One row of the run console's four-column canvas (UI_SPEC.md §2.1):
 * Orders · Gateway · Bank · Ledger. Built up live from the real match
 * event stream — a row starts at hop1 (order<->capture) and grows
 * rightward as hop2/hop3 events reference the same record, exactly
 * mirroring "records travelling left to right... snap into a linked
 * group." Rows with no hop1 origin (refunds/chargebacks have none) start
 * at the Gateway column instead. */
export interface ChainRow {
  key: string
  orderId?: string
  gwId?: string
  bankId?: string
  glId?: string
  tier1?: number | null
  tier2?: number | null
  hop3Done?: boolean
}

export interface ChainState {
  rows: ChainRow[]
  rowByGw: Map<string, number> // gw payment id -> row index, for hop2 lookups
  rowsByBank: Map<string, number[]> // bank line id -> row indices, for hop3 fan-out
}

export function initialChainState(): ChainState {
  return { rows: [], rowByGw: new Map(), rowsByBank: new Map() }
}

/** Pure: never mutates `state` or anything reachable from it — every Map
 * and array is cloned before any write. Required, not just tidy: this is
 * called from a React state updater, and React 19's StrictMode
 * double-invokes updaters in dev to catch exactly this class of bug — an
 * in-place mutation would apply the same event twice to shared Map/array
 * references and silently double up rows. */
export function applyMatchEvent(state: ChainState, event: Extract<RunEvent, { kind: "match" }>): ChainState {
  const { hop, id_a, id_b, tier } = event
  const rows = [...state.rows]
  const rowByGw = new Map(state.rowByGw)
  const rowsByBank = new Map(state.rowsByBank)

  if (hop === 1) {
    rowByGw.set(id_b, rows.length)
    rows.push({ key: `row-${rows.length}`, orderId: id_a, gwId: id_b, tier1: tier })
    return { rows, rowByGw, rowsByBank }
  }

  if (hop === 2) {
    const existing = rowByGw.get(id_a)
    let rowIndex: number
    if (existing !== undefined) {
      rows[existing] = { ...rows[existing], bankId: id_b, tier2: tier }
      rowIndex = existing
    } else {
      // No hop1 origin (refund/chargeback/adjustment) — starts its own
      // row at the Gateway column.
      rowIndex = rows.length
      rows.push({ key: `row-${rowIndex}`, gwId: id_a, bankId: id_b, tier2: tier })
    }
    rowsByBank.set(id_b, [...(rowsByBank.get(id_b) ?? []), rowIndex])
    return { rows, rowByGw, rowsByBank }
  }

  // hop 3: bank -> gl, fans out to every row that settled through this
  // bank line (a many-orders-to-one-settlement batch completes them all
  // at once — the moment this fires, several rows go fully verified together).
  const rowIndices = rowsByBank.get(id_a) ?? []
  if (rowIndices.length === 0) {
    rows.push({ key: `row-${rows.length}`, bankId: id_a, glId: id_b, hop3Done: true })
  } else {
    for (const idx of rowIndices) {
      rows[idx] = { ...rows[idx], glId: id_b, hop3Done: true }
    }
  }
  return { rows, rowByGw, rowsByBank }
}

export function isFullyChained(row: ChainRow): boolean {
  return Boolean(row.orderId && row.gwId && row.bankId && row.glId)
}
