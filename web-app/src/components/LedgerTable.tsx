import type { ReactNode } from "react"

export interface LedgerColumn<Row> {
  header: string
  align?: "left" | "right"
  render: (row: Row) => ReactNode
  /** Figures get the mono/tabular treatment; prose columns don't. */
  figures?: boolean
}

/** UI_SPEC.md §1/§2.4 — a ruled table, not a card grid: hairline row
 * rules, header labels in Inter (muted), figure columns in Plex Mono
 * tabular-nums aligned on the decimal. Used by the exception queue,
 * reconstruction viewer, and anywhere else a screen renders rows of
 * records rather than a single number. */
export function LedgerTable<Row>({
  columns,
  rows,
  rowKey,
  onRowClick,
}: {
  columns: LedgerColumn<Row>[]
  rows: Row[]
  rowKey: (row: Row) => string
  onRowClick?: (row: Row) => void
}) {
  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="border-b border-rule">
          {columns.map((col) => (
            <th
              key={col.header}
              className={`py-2 px-3 text-xs font-medium text-muted ${
                col.align === "right" ? "text-right" : "text-left"
              }`}
            >
              {col.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={rowKey(row)}
            className={`border-b border-rule/60 last:border-0 ${onRowClick ? "cursor-pointer hover:bg-ink/40" : ""}`}
            onClick={() => onRowClick?.(row)}
          >
            {columns.map((col) => (
              <td
                key={col.header}
                className={`py-2 px-3 align-top ${col.align === "right" ? "text-right" : "text-left"} ${
                  col.figures ? "figures tabular-nums" : ""
                }`}
              >
                {col.render(row)}
              </td>
            ))}
          </tr>
        ))}
        {rows.length === 0 && (
          <tr>
            <td colSpan={columns.length} className="py-6 px-3 text-center text-muted text-sm">
              No rows.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  )
}
