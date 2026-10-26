"""The analysis report as Markdown, a Slack message and a Telegram message, in Spanish."""

from __future__ import annotations

from html import escape as html_escape
from typing import Any

from fin_graph.domain.analysis_report import AnalysisReport, CitedClaim, TickerSection
from fin_graph.domain.claims import ClaimStatus
from fin_graph.domain.orders import OrderAction
from fin_graph.domain.quant.common import Signal

#: Slack limits (header 150 characters, section 3000) and Telegram's per-message limit.
HEADER_LIMIT = 150
SECTION_LIMIT = 3000
MESSAGE_LIMIT = 4096
STANCE = {Signal.BULLISH: "alcista", Signal.BEARISH: "bajista", Signal.NEUTRAL: "neutral"}
ACTION = {
    OrderAction.BUY: "comprar",
    OrderAction.SELL: "vender",
    OrderAction.SHORT: "vender en corto",
    OrderAction.COVER: "cubrir corto",
    OrderAction.HOLD: "mantener",
}
_TRUNCATED = "\n… (recortado)"


def slack_escape(text: str) -> str:
    """Escape the three characters Slack's mrkdwn reserves."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def headline(report: AnalysisReport) -> str:
    """``cli · horizonte medium · AAPL, MSFT · 42s``."""
    request = report.request
    parts = [request.source.value, f"horizonte {request.horizon.value}", ", ".join(request.tickers)]
    if request.trigger:
        parts.append(request.trigger)
    if report.duration_seconds is not None:
        parts.append(f"{report.duration_seconds:.0f}s")
    return " · ".join(parts)


def verdict_line(section: TickerSection) -> str:
    """``alcista · quality_compounder · confianza 72% · verificación 0.64 (aprobada)``."""
    thesis = section.thesis
    parts = [STANCE[thesis.stance], thesis.category, f"confianza {thesis.confidence:.0f}%"]
    if section.verification is not None:
        state = "aprobada" if section.verification.passed else "no aprobada"
        parts.append(f"verificación {section.verification.score:.2f} ({state})")
    if thesis.degraded:
        parts.append("degradada")
    return " · ".join(parts)


def signal_lines(section: TickerSection) -> list[str]:
    """``fundamentals: alcista 75% — ROE 156%…`` per analyst."""
    return [
        f"{domain}: {STANCE[item.signal]} {item.confidence:.0f}%"
        + (f" — {item.rationale}" if item.rationale else "")
        for domain, item in section.signals.items()
    ]


def claim_text(claim: CitedClaim) -> str:
    """A claim with its ``[E1, E2]`` citations and, when in doubt, why."""
    text = claim.statement
    if claim.citations:
        text += f" [{', '.join(claim.citations)}]"
    if claim.status in (ClaimStatus.WEAK, ClaimStatus.UNSUPPORTED):
        text += f" (sin verificar: {claim.reason})"
    return text


def risk_line(section: TickerSection) -> str | None:
    """Price, volatility and how much the ticker may still take."""
    limit = section.limit
    if limit is None:
        return None
    if limit.error:
        return f"Riesgo: {limit.error}"
    return (
        f"Riesgo: precio {limit.current_price:,.2f}, volatilidad anual "
        f"{limit.volatility.annualized:.1%}, límite {limit.limit_pct:.1%} de la cartera, "
        f"disponible {max(limit.remaining_limit, 0):,.0f} ({limit.max_shares} acciones máx.)"
    )


def decision_line(section: TickerSection) -> str | None:
    """The decision and, when there is one, the order waiting for approval."""
    decision = section.decision
    if decision is None:
        return None
    text = f"Decisión: {ACTION[decision.action]}"
    if decision.action is not OrderAction.HOLD:
        text += f" {decision.quantity} acciones"
    if decision.reasoning:
        text += f" — {decision.reasoning}"
    if section.order is not None:
        text += (
            f". Orden paper {section.order.client_order_id} por {section.order.notional:,.0f}, "
            "pendiente de aprobación"
        )
    return text


def evidence_lines(report: AnalysisReport) -> list[str]:
    """``E1 · analytics/compute_valuation: AAPL valuation bearish 100%``."""
    return [
        f"{report.labels[item.id]} · {item.integration}/{item.tool}: {item.summary}"
        for item in report.evidence
    ]


def footer(report: AnalysisReport) -> str:
    if report.duration_seconds is None:
        return "Generado por fin-graph"
    return f"Generado por fin-graph en {report.duration_seconds:.0f}s"


def render_analysis_markdown(report: AnalysisReport) -> str:
    """Every section of the report; empty sections are left out."""
    lines = [f"# {report.title}", "", f"**{headline(report)}**"]
    for section in report.sections:
        thesis = section.thesis
        lines += ["", f"## {section.ticker} — {verdict_line(section)}", "", thesis.summary]
        signals = signal_lines(section)
        if signals:
            lines += ["", "**Señales**", ""] + [f"- {item}" for item in signals]
        for heading, items in (
            ("A favor", thesis.bull_case),
            ("En contra", thesis.bear_case),
            ("Catalizadores", thesis.catalysts),
            ("Riesgos", thesis.risks),
        ):
            if items:
                lines += ["", f"**{heading}**", ""] + [f"- {item}" for item in items]
        if section.claims:
            lines += ["", "**Afirmaciones**", ""]
            lines += [f"- {claim_text(claim)}" for claim in section.claims]
        for line in (risk_line(section), decision_line(section)):
            if line:
                lines += ["", line]
        check = section.verification
        for problem in (check.stance_conflict, check.stance_mismatch) if check else ():
            if problem:
                lines += ["", f"> Conflicto: {problem}"]
    evidence = evidence_lines(report)
    if evidence:
        lines += ["", "## Evidencia", ""] + [f"- {item}" for item in evidence]
    if report.degraded_steps:
        lines += ["", "## Pasos degradados", ""] + [f"- {item}" for item in report.degraded_steps]
    lines += ["", "---", f"_{report.disclaimer}_", "", footer(report)]
    return "\n".join(lines) + "\n"


def render_analysis_slack(report: AnalysisReport) -> dict[str, Any]:
    """``{"text": fallback, "blocks": [...]}`` ready for ``chat.postMessage``."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _cut(report.title, HEADER_LIMIT)},
        },
        _context(headline(report)),
    ]
    for section in report.sections:
        body = [f"*{section.ticker}* — {slack_escape(verdict_line(section))}", ""]
        body.append(slack_escape(section.thesis.summary))
        body += [f"• {slack_escape(item)}" for item in signal_lines(section)]
        body += [
            slack_escape(line) for line in (risk_line(section), decision_line(section)) if line
        ]
        blocks.append(_section("\n".join(body)))
    blocks.append(_context(report.disclaimer))
    fallback = (
        report.title
        + ": "
        + "; ".join(
            f"{section.ticker} {STANCE[section.thesis.stance]}" for section in report.sections
        )
    )
    return {"text": fallback, "blocks": blocks}


def render_analysis_telegram(report: AnalysisReport) -> str:
    """Whole sections only, until the message limit; the disclaimer always fits."""
    disclaimer = f"<i>{html_escape(report.disclaimer)}</i>"
    budget = MESSAGE_LIMIT - len(disclaimer) - len(_TRUNCATED) - 2
    message = f"<b>{html_escape(report.title)}</b>\n{html_escape(headline(report))}"
    for index, section in enumerate(report.sections):
        candidate = f"{message}\n\n{_telegram_section(section, section.thesis.summary)}"
        if len(candidate) <= budget:
            message = candidate
            continue
        if index == 0:
            # The first ticker always shows: shorten its summary, never split an HTML entity.
            summary = section.thesis.summary
            while summary and len(candidate) > budget:
                summary = summary[: int(len(summary) * 0.8)]
                candidate = f"{message}\n\n{_telegram_section(section, summary + '…')}"
            if len(candidate) <= budget:
                message = candidate
        return f"{message}{_TRUNCATED}\n\n{disclaimer}"
    return f"{message}\n\n{disclaimer}"


def _telegram_section(section: TickerSection, summary: str) -> str:
    lines = [
        f"<b>{html_escape(section.ticker)}</b> — {html_escape(verdict_line(section))}",
        html_escape(summary),
    ]
    lines += [f"• {html_escape(item)}" for item in signal_lines(section)]
    lines += [html_escape(line) for line in (risk_line(section), decision_line(section)) if line]
    return "\n".join(lines)


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _cut(text, SECTION_LIMIT)}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": slack_escape(text)}]}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
