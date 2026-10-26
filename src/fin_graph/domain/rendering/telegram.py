"""The report as one Telegram message in HTML parse mode."""

from __future__ import annotations

from html import escape

from fin_graph.domain.rendering.common import (
    claim_text,
    footer,
    headline,
    remediation_lines,
    status_line,
    verdict_line,
)
from fin_graph.domain.report import Report

#: Telegram's limit for one message.
MESSAGE_LIMIT = 4096
_TRUNCATED = "\n… (truncated)"


def render_telegram(report: Report) -> str:
    """Whole sections only, in priority order, until the message limit is reached.

    Text is HTML-escaped, so a ``<`` in an error message cannot break the
    markup; a section that does not fit is dropped and the cut is marked.
    """
    diagnosis = report.diagnosis
    sections = [
        f"<b>{escape(report.title)}</b>\n{escape(headline(report))}",
        f"<b>Root cause</b>\n{escape(diagnosis.root_cause)}\n{escape(verdict_line(report))}",
        f"<b>Triage complete:</b> {escape(diagnosis.triage_summary)}\n"
        f"{escape(status_line(report))}",
    ]
    if report.claims:
        sections.append(_bullets("Claims", [claim_text(claim) for claim in report.claims]))
    remediation = remediation_lines(report)
    if remediation:
        sections.append(_bullets("Remediation", remediation))
    sections.append(f"<i>{escape(footer(report))}</i>")
    message = ""
    for section in sections:
        candidate = f"{message}\n\n{section}" if message else section
        if len(candidate) > MESSAGE_LIMIT - len(_TRUNCATED):
            return message + _TRUNCATED
        message = candidate
    return message


def _bullets(heading: str, items: list[str]) -> str:
    return f"<b>{heading}</b>\n" + "\n".join(f"• {escape(item)}" for item in items)
