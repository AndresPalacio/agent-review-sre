"""The report as a Slack message: Block Kit blocks plus a plain-text fallback."""

from __future__ import annotations

from typing import Any

from fin_graph.domain.rendering.common import (
    claim_text,
    evidence_lines,
    footer,
    headline,
    remediation_lines,
    status_line,
    verdict_line,
)
from fin_graph.domain.report import Report

#: Slack limits: header text 150 characters, section text 3000.
HEADER_LIMIT = 150
SECTION_LIMIT = 3000


def escape(text: str) -> str:
    """Escape the three characters Slack's mrkdwn reserves."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_slack(report: Report) -> dict[str, Any]:
    """``{"text": fallback, "blocks": [...]}`` ready for ``chat.postMessage``."""
    diagnosis = report.diagnosis
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _cut(report.title, HEADER_LIMIT)},
        },
        _context(f"{headline(report)} · {verdict_line(report)}"),
        _section(f"*Root cause*\n{escape(diagnosis.root_cause)}"),
        _section(
            f"*Triage complete:* {escape(diagnosis.triage_summary)}\n{escape(status_line(report))}"
        ),
    ]
    if diagnosis.hypotheses:
        items = [
            f"{item.hypothesis_id} ({item.verdict.value}): {item.statement}"
            for item in diagnosis.hypotheses
        ]
        blocks.append(_bullets("Hypotheses", items))
    if report.claims:
        blocks.append(_bullets("Claims", [claim_text(claim) for claim in report.claims]))
    remediation = remediation_lines(report)
    if remediation:
        blocks.append(_bullets("Remediation", remediation))
    evidence = evidence_lines(report)
    if evidence:
        blocks.append(_bullets("Evidence", evidence))
    blocks.append(_context(footer(report)))
    fallback = f"{report.alert.severity.value.upper()} {report.title}: {diagnosis.root_cause}"
    return {"text": fallback, "blocks": blocks}


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _cut(text, SECTION_LIMIT)}}


def _bullets(heading: str, items: list[str]) -> dict[str, Any]:
    return _section(f"*{heading}*\n" + "\n".join(f"• {escape(item)}" for item in items))


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": escape(text)}]}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
