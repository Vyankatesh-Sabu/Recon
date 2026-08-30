"""report.py — renders the run report to terminal, JSON, and HTML (SPEC §7).

`false_match_rate` is the headline number — printed prominently in every
form, per the user's explicit instruction. `₹` strings are built ONLY here,
via moneymath.format_rupees (CLAUDE.md rule 1); everywhere else stays paise.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from recon import moneymath


def render_terminal(ctx: dict) -> str:
    m = ctx["metrics"]
    lines = [
        f"RECON-4 RUN {ctx['finished_at']} · seed {ctx['seed']} · llm={ctx['llm_mode']}",
        f"Records: {m['records_processed']}   Runtime: {m['runtime_s']:.2f}s   "
        f"LLM calls: {m['llm_calls']['total']} "
        f"(accepted {m['llm_calls']['accepted']}, rejected {m['llm_calls']['rejected']}, "
        f"abstained {m['llm_calls']['abstained']})",
        f"Hop match:  H1 {m['hop_match']['h1']}  H2 {m['hop_match']['h2']}  "
        f"H3 {m['hop_match']['h3']} (not yet implemented — P3)     "
        f"Full chain (h1+h2): {m['full_chain_rate'] * 100:.1f}%",
        f"Link precision {m['link_precision'] * 100:.1f}% · recall {m['link_recall'] * 100:.1f}% · "
        f"FALSE-MATCH RATE {m['false_match_rate'] * 100:.1f}%",
        f"Exceptions: {m['exceptions']['open']} open "
        f"({m['exceptions']['critical']} critical / {m['exceptions']['warn']} warn / "
        f"{m['exceptions']['info']} info) · {moneymath.format_rupees(m['amount_at_risk_p'])} at risk",
        "Clearing control: pending P3 (hop3 + invariant V5 not yet implemented)",
    ]
    if ctx["top_exceptions"]:
        lines.append("Top exceptions by ₹ at risk:")
        for exc in ctx["top_exceptions"]:
            sev = {"critical": "crit", "warn": "warn", "info": "info"}[exc["severity"]]
            lines.append(
                f"  {exc['exc_id']:<24} {exc['code']:<26} "
                f"{moneymath.format_rupees(exc['amount_at_risk_p']):>14}  {sev:<4} "
                f"\"{exc['explanation']}\" → {exc['suggested_action']}"
            )
    return "\n".join(lines)


def render_json(ctx: dict) -> str:
    return json.dumps(ctx, indent=2, default=str)


def render_html(ctx: dict) -> str:
    m = ctx["metrics"]
    rows = "\n".join(
        f"<tr><td>{html.escape(e['exc_id'])}</td><td>{html.escape(e['code'])}</td>"
        f"<td>{html.escape(e['severity'])}</td>"
        f"<td class='amt'>{html.escape(moneymath.format_rupees(e['amount_at_risk_p']))}</td>"
        f"<td>{html.escape(e['explanation'])}</td>"
        f"<td>{html.escape(e['suggested_action'])}</td></tr>"
        for e in ctx["top_exceptions"]
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>RECON-4 run {html.escape(ctx['run_id'])}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #111; }}
h1 {{ font-size: 1.2rem; }}
.headline {{ font-size: 1.6rem; font-weight: bold; }}
table {{ border-collapse: collapse; margin-top: 1rem; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }}
th {{ background: #f0f0f0; }}
td.amt {{ text-align: right; font-variant-numeric: tabular-nums; }}
.meta {{ color: #555; }}
</style></head>
<body>
<h1>RECON-4 run {html.escape(ctx['run_id'])}</h1>
<p class="meta">seed {ctx['seed']} · llm={html.escape(ctx['llm_mode'])} · finished {html.escape(ctx['finished_at'])}</p>
<p class="headline">FALSE-MATCH RATE: {m['false_match_rate'] * 100:.1f}%</p>
<p>Link precision {m['link_precision'] * 100:.1f}% · recall {m['link_recall'] * 100:.1f}% ·
Full chain (h1+h2) {m['full_chain_rate'] * 100:.1f}%</p>
<p>Records: {m['records_processed']} · Runtime: {m['runtime_s']:.2f}s ·
Hop match: H1 {m['hop_match']['h1']} · H2 {m['hop_match']['h2']} · H3 {m['hop_match']['h3']} (P3)</p>
<p>Exceptions: {m['exceptions']['open']} open
({m['exceptions']['critical']} critical / {m['exceptions']['warn']} warn / {m['exceptions']['info']} info)
· {html.escape(moneymath.format_rupees(m['amount_at_risk_p']))} at risk</p>
<p>Clearing control: pending P3 (hop3 + invariant V5 not yet implemented)</p>
<h2>Top exceptions by ₹ at risk</h2>
<table>
<tr><th>ID</th><th>Code</th><th>Severity</th><th>₹ at risk</th><th>Explanation</th><th>Suggested action</th></tr>
{rows}
</table>
</body></html>
"""


def write_reports(ctx: dict, out_dir: Path | str) -> tuple[Path, Path]:
    """Write report.json and report.html to out_dir; returns their paths.

    The terminal form isn't written to disk — call render_terminal() and
    print it.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    html_path = out_dir / "report.html"
    json_path.write_text(render_json(ctx), encoding="utf-8")
    html_path.write_text(render_html(ctx), encoding="utf-8")
    return json_path, html_path
