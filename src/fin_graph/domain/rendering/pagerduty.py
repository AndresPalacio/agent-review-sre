"""The report as a short plain-text PagerDuty incident note."""

from __future__ import annotations

from fin_graph.domain.rendering.common import claim_text, remediation_lines, verdict_line
from fin_graph.domain.report import Report

NOTE_LIMIT = 4000
_TOP = 3


def render_pagerduty_note(report: Report) -> str:
    """Root cause, verdict, the top three claims and the top three actions, in plain text."""
    lines = [
        f"fin-graph RCA: {report.title}",
        "",
        report.diagnosis.root_cause,
        verdict_line(report),
    ]
    if report.claims:
        lines += ["", "Key evidence:"] + [f"- {claim_text(c)}" for c in report.claims[:_TOP]]
    remediation = remediation_lines(report)
    if remediation:
        lines += ["", "Next steps:"] + [f"- {item}" for item in remediation[:_TOP]]
    note = "\n".join(lines)
    return note if len(note) <= NOTE_LIMIT else note[: NOTE_LIMIT - 1] + "…"
