import { formatRupees } from "../lib/money"

type Tone = "figure" | "verified" | "flag" | "caution" | "muted"

const toneClass: Record<Tone, string> = {
  figure: "text-figure",
  verified: "text-verified",
  flag: "text-flag",
  caution: "text-caution",
  muted: "text-muted",
}

/** Renders an integer-paise amount as "₹x,xxx.xx" — the ONLY place a
 * number crosses from data to prose on screen. Never pass a computed
 * value here that the API didn't already return (UI_SPEC.md §0). */
export function Money({ amountP, tone = "figure" }: { amountP: number; tone?: Tone }) {
  return <span className={`figures tabular-nums ${toneClass[tone]}`}>{formatRupees(amountP)}</span>
}
