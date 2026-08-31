import type { ReactNode } from "react"

type Severity = "critical" | "warn" | "info" | string

const severityColor: Record<string, string> = {
  critical: "var(--flag)",
  warn: "var(--caution)",
  info: "var(--trace)",
}

/** UI_SPEC.md §2.4: "severity rule (a coloured left edge, not a pill)".
 * Wrap a row's content in this instead of a badge/chip. */
export function SeverityRule({ severity, children }: { severity: Severity; children: ReactNode }) {
  const color = severityColor[severity] ?? "var(--muted)"
  return (
    <div className="border-l-2 pl-3" style={{ borderColor: color }}>
      {children}
    </div>
  )
}
