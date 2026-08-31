/** UI_SPEC.md §2.1: "Tier badge appears per match: T1 T2 T4." Deterministic
 * tiers (1/2) read as --verified; tier 4 (the LLM) is called out in
 * --trace to make it visually distinct that a model, not arithmetic,
 * decided this one. */
export function TierBadge({ tier }: { tier: number | null }) {
  if (tier === null) return <span className="text-muted text-xs">—</span>
  const isLLM = tier === 4
  return (
    <span
      className={`figures text-xs px-1.5 py-0.5 rounded-sm border ${
        isLLM ? "text-trace border-trace/40" : "text-verified border-verified/40"
      }`}
    >
      T{tier}
    </span>
  )
}
