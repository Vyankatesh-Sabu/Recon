import { Money } from "./Money"
import type { ExceptionRecord, RefusalEvidence } from "../lib/api"

/**
 * RefusalCard — UI_SPEC.md §2.5, "build this deliberately".
 *
 * AMBIGUOUS_SETTLEMENT is not a table row. Two bank lines that reconstruct
 * to the same candidate subsets, on the same day, for the same amount to
 * the paisa, rendered as a mirrored pair: the mirroring IS the argument.
 * Every field on the left equals the field on the right, which is exactly
 * why no match was proposed.
 *
 * Nothing on this card offers a way to pick one. There is deliberately no
 * "accept A" control, no confidence score, no "most likely" hint — SPEC
 * §6.3's refusal ("Do NOT PICK ONE. Not even by earliest date.") is a
 * property of the product, not just of the engine.
 */

/** One side of the mirror. Every value here came from the exception's
 * stored evidence (recon/engine/hop2.py, migration 003) — nothing is
 * derived in the browser except the count and sizes of the subset arrays
 * the API returned. */
function Side({ label, exc, evidence }: { label: string; exc: ExceptionRecord; evidence: RefusalEvidence }) {
  const subsets = candidateSubsets(evidence)
  return (
    <div className="flex-1 min-w-0">
      <p className="text-xs text-muted tracking-wide mb-1">{label}</p>
      <p className="figures text-base text-figure mb-4">{evidence.bank_line}</p>

      <dl className="flex flex-col gap-2 text-sm">
        <Field label="value date">
          <span className="figures">{evidence.value_date}</span>
        </Field>
        <Field label="net">
          <Money amountP={exc.amount_at_risk_p} />
        </Field>
        <Field label="candidate subsets">
          <span className="figures">
            {subsets.length} (sizes {subsets.map((s) => s.length).join(", ")})
          </span>
        </Field>
        <Field label="utr">
          {/* The parser's extracted token, verbatim. On seed 42 both sides
              read SETTLEMENT — a word scraped out of the narration, not a
              reference number, and identical on both, which is the point.
              Null means nothing was recoverable at all. */}
          <span className="figures text-muted">{evidence.utr_extracted ?? "—"}</span>
        </Field>
        <Field label="narration">
          <span className="text-muted text-xs">{evidence.narration}</span>
        </Field>
      </dl>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 items-baseline">
      <dt className="text-muted text-xs shrink-0">{label}</dt>
      <dd className="text-right min-w-0 truncate">{children}</dd>
    </div>
  )
}

/** The disjoint readings hop2 found. The `Multiple` branch stores two
 * (subset_a / subset_b); the cross-collision branch stores one subset that
 * another bank line also claims — both are refusals, and both are counted
 * here from whatever the evidence actually contains. */
function candidateSubsets(evidence: RefusalEvidence): { id: string; net_p: number }[][] {
  if (evidence.subset_a && evidence.subset_b) return [evidence.subset_a, evidence.subset_b]
  if (evidence.subset) return [evidence.subset]
  return []
}

export function RefusalCard({ pair }: { pair: { exc: ExceptionRecord; evidence: RefusalEvidence }[] }) {
  const [a, b] = pair
  return (
    <div className="bg-paper border border-rule rounded-sm">
      <div className="border-b border-rule px-4 py-2.5 flex items-baseline gap-3">
        <span className="text-xs tracking-wide text-flag font-medium">REFUSED</span>
        <span className="text-xs text-muted">
          AMBIGUOUS_SETTLEMENT · critical · hop {a.exc.hop}
        </span>
      </div>

      <div className="p-6">
        <div className="flex gap-8 md:gap-16">
          <Side label="CANDIDATE A" exc={a.exc} evidence={a.evidence} />
          {/* The mirror line: the two sides are separated by a rule, not by
              a control that would imply a choice is available. */}
          <div className="w-px bg-rule shrink-0" />
          {b ? (
            <Side label="CANDIDATE B" exc={b.exc} evidence={b.evidence} />
          ) : (
            <div className="flex-1 text-sm text-muted">
              Its counterpart resolved elsewhere in this run; the refusal stands on its own.
            </div>
          )}
        </div>

        <p className="mt-8 pt-6 border-t border-rule text-sm text-muted max-w-xl mx-auto text-center leading-relaxed">
          These are indistinguishable from the available data. No match proposed. Resolve by
          confirming the settlement ID in the gateway dashboard.
        </p>
      </div>
    </div>
  )
}

/** Group refusals into mirrored pairs by (value date, amount at risk) —
 * the two properties that make them indistinguishable in the first place.
 * A group with an odd count still renders; the leftover gets a card whose
 * second side says so rather than being silently dropped. */
export function pairRefusals(
  exceptions: ExceptionRecord[],
): { exc: ExceptionRecord; evidence: RefusalEvidence }[][] {
  const withEvidence = exceptions
    .filter((e) => e.code === "AMBIGUOUS_SETTLEMENT" && e.evidence)
    .map((e) => ({ exc: e, evidence: e.evidence as RefusalEvidence }))

  const groups = new Map<string, { exc: ExceptionRecord; evidence: RefusalEvidence }[]>()
  for (const item of withEvidence) {
    const key = `${item.evidence.value_date}|${item.exc.amount_at_risk_p}`
    const group = groups.get(key)
    if (group) group.push(item)
    else groups.set(key, [item])
  }

  const pairs: { exc: ExceptionRecord; evidence: RefusalEvidence }[][] = []
  for (const group of groups.values()) {
    for (let i = 0; i < group.length; i += 2) pairs.push(group.slice(i, i + 2))
  }
  return pairs
}
