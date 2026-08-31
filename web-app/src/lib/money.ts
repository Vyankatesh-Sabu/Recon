/**
 * money.ts — the ONLY place paise become a rupee string on the frontend.
 *
 * Mirrors recon/moneymath.py's format_rupees() exactly (Indian digit
 * grouping) so a number never reads differently between the CLI/report
 * output and the UI. UI_SPEC.md §0's hard boundary: the frontend never
 * computes a number, only formats one the API already returned — this
 * function does arithmetic on digit-string grouping, never on the amount.
 */
export function formatRupees(amountP: number): string {
  const sign = amountP < 0 ? "-" : ""
  const abs = Math.abs(amountP)
  const whole = Math.floor(abs / 100)
  const frac = abs % 100
  const s = String(whole)

  let grouped: string
  if (s.length <= 3) {
    grouped = s
  } else {
    let head = s.slice(0, -3)
    const tail = s.slice(-3)
    const parts: string[] = []
    while (head.length > 2) {
      parts.unshift(head.slice(-2))
      head = head.slice(0, -2)
    }
    parts.unshift(head)
    grouped = parts.join(",") + "," + tail
  }

  return `${sign}₹${grouped}.${String(frac).padStart(2, "0")}`
}
