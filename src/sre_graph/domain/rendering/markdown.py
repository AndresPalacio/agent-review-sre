"""The full report as Markdown, for the CLI, the web UI and stored reports."""

from __future__ import annotations

from sre_graph.domain.rendering.common import (
    claim_text,
    evidence_lines,
    footer,
    headline,
    remediation_lines,
    status_line,
    verdict_line,
)
from sre_graph.domain.report import Report


def render_markdown(report: Report) -> str:
    """Every section of the report; empty sections are left out."""
    diagnosis = report.diagnosis
    lines = [f"# {report.title}", "", f"**{headline(report)}**", "", "## Root cause", ""]
    lines += [diagnosis.root_cause, "", verdict_line(report), "", "## Incident command", ""]
    lines += [f"Triage complete: {diagnosis.triage_summary}", "", status_line(report)]
    if diagnosis.hypotheses:
        lines += ["", "**Hypotheses**", ""]
        lines += [
            f"- {item.hypothesis_id} ({item.verdict.value}): {item.statement} — {item.reason}"
            for item in diagnosis.hypotheses
        ]
    for heading, items in (
        ("Verification", diagnosis.verification),
        ("Follow-up questions", diagnosis.follow_up_questions),
    ):
        if items:
            lines += ["", f"**{heading}**", ""] + [f"- {item}" for item in items]
    if diagnosis.remediation_tradeoffs:
        lines += ["", "**Remediation trade-offs**", "", diagnosis.remediation_tradeoffs]
    if diagnosis.causal_chain:
        lines += ["", "## Causal chain", ""]
        lines += [f"{number}. {step}" for number, step in enumerate(diagnosis.causal_chain, 1)]
    if report.claims:
        lines += ["", "## Claims", ""] + [f"- {claim_text(claim)}" for claim in report.claims]
    remediation = remediation_lines(report)
    if remediation:
        lines += ["", "## Remediation", ""]
        lines += [f"{number}. {item}" for number, item in enumerate(remediation, 1)]
    evidence = evidence_lines(report)
    if evidence:
        lines += ["", "## Evidence", ""] + [f"- {item}" for item in evidence]
    lines += ["", "---", footer(report)]
    return "\n".join(lines) + "\n"
