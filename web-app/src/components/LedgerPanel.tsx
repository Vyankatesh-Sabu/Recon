import type { ReactNode } from "react"

/** A raised ledger surface: --paper background, hairline --rule border.
 * The base container every table/card in the app sits on. */
export function LedgerPanel({
  title,
  children,
  className = "",
}: {
  title?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={`bg-paper border border-rule rounded-sm ${className}`}>
      {title && (
        <div className="border-b border-rule px-4 py-2.5 text-xs tracking-wide text-muted font-medium">
          {title}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  )
}
