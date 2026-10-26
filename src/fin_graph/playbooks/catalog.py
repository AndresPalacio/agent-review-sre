"""Exact-id playbook selection from the scores; a playbook is guidance, never an order."""

from __future__ import annotations

import re
from collections.abc import Mapping

from fin_graph.domain.quant.common import QuantScore, Signal
from fin_graph.domain.tools import Domain

PLAYBOOKS: dict[str, str] = {
    "earnings-season": (
        "# Temporada de resultados\n"
        "Compara el último trimestre con la tendencia anual, no con un solo dato. Separa "
        "sorpresas de ingresos y de márgenes. Desconfía de titulares del mismo día: el "
        "precio ya los descuenta. Revisa si la guía cambió."
    ),
    "high-volatility-regime": (
        "# Régimen de alta volatilidad\n"
        "Con volatilidad anual alta el límite de posición baja solo. Prioriza balance y "
        "caja frente a momentum. Una señal técnica en régimen volátil vale menos. Indica el "
        "drawdown que soportaría la tesis."
    ),
    "deep-value-checklist": (
        "# Checklist valor profundo (Graham)\n"
        "1. Margen de seguridad frente al Graham Number o al NCAV, no solo P/E bajo.\n"
        "2. Ratio corriente ≥ 2 y pasivos < 50% de activos.\n"
        "3. Beneficios positivos y estables varios años.\n"
        "4. Busca la trampa de valor: ¿por qué está barata? Si la causa es estructural, "
        "dilo como riesgo."
    ),
    "quality-compounder-checklist": (
        "# Checklist compounder de calidad (Buffett)\n"
        "1. ROE > 15% de forma consistente y márgenes estables o crecientes.\n"
        "2. Poder de fijar precios: margen bruto alto que no cae.\n"
        "3. Recompras y dividendos sin dilución.\n"
        "4. Aun con calidad, sin margen de seguridad la postura no es alcista."
    ),
    "balance-sheet-stress": (
        "# Estrés de balance\n"
        "Revisa deuda frente a patrimonio, ratio corriente, cobertura de intereses y flujo "
        "de caja libre. Un patrimonio negativo o pasivos cerca de los activos es riesgo de "
        "dilución o quiebra antes que oportunidad."
    ),
    "insider-selling-review": (
        "# Ventas de insiders\n"
        "Distingue ventas planificadas o por impuestos de ventas discrecionales. Importa el "
        "tamaño frente a la tenencia y si hay compras en paralelo. Las ventas solas no "
        "bastan para una tesis bajista."
    ),
    "momentum-breakdown": (
        "# Ruptura de momentum\n"
        "EMA corta bajo la larga con ADX alto es tendencia bajista confirmada. Separa la "
        "señal técnica de la tesis fundamental: una puede invalidar el momento de entrada "
        "sin invalidar la otra."
    ),
}
MAX_PLAYBOOKS = 2
_EARNINGS = re.compile(r"\b(?:earnings|resultados|trimestr\w*|quarter\w*|guidance|guía)\b")
#: Annualised volatility above which the high-volatility playbook applies.
HIGH_VOLATILITY = 0.40


def load_playbook(playbook_id: str) -> str:
    """Only a packaged playbook, by its exact id."""
    return PLAYBOOKS[playbook_id]


def select_playbooks(
    category: str, scores: Mapping[Domain, QuantScore], *, question: str = ""
) -> list[dict[str, str]]:
    """Up to two playbooks for a ticker, from its suggested category, its scores and the request."""
    selected: list[str] = []
    if _EARNINGS.search(question.lower()):
        selected.append("earnings-season")
    by_category = {
        "deep_value": "deep-value-checklist",
        "quality_compounder": "quality-compounder-checklist",
        "balance_sheet_risk": "balance-sheet-stress",
        "overvalued": "deep-value-checklist",
    }
    if category in by_category:
        selected.append(by_category[category])
    technicals = scores.get(Domain.TECHNICALS)
    volatility = technicals.component("volatility") if technicals else None
    annualised = volatility.metrics.get("historical_volatility") if volatility else None
    if isinstance(annualised, float) and annualised > HIGH_VOLATILITY:
        selected.append("high-volatility-regime")
    trend = technicals.component("trend") if technicals else None
    if trend is not None and trend.signal is Signal.BEARISH:
        selected.append("momentum-breakdown")
    sentiment = scores.get(Domain.SENTIMENT)
    insiders = sentiment.component("insider_trading") if sentiment else None
    if insiders and insiders.metrics.get("bearish_trades", 0) > insiders.metrics.get(
        "bullish_trades", 0
    ):
        selected.append("insider-selling-review")
    unique = list(dict.fromkeys(selected))[:MAX_PLAYBOOKS]
    return [{"id": item, "content": load_playbook(item)} for item in unique]
