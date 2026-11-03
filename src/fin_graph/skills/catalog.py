"""Curated skills exposed by exact identifier: a short description first, the full guide on request.

The finance skills are condensed in Spanish from ai-hedge-fund's ``app/backend/skills/finance``
(MIT) and rewritten around this project's read-only tools. They guide reasoning; none of them
places an order or replaces the deterministic risk limits.
"""

from __future__ import annotations

from functools import lru_cache

from fin_graph.skills.loader import (
    bundled_finance_root,
    load_skill_catalog,
    read_skill_document,
)
from fin_graph.skills.models import SkillRecord

_CURATED_SKILLS = {
    "analyzing-equities": (
        "Analiza acciones con scores deterministas, tesis falsables y evidencia citada.",
        "# Análisis de acciones\n"
        "1. Fija tickers, fecha de análisis (point in time) y horizonte.\n"
        "2. Pide los scores: compute_fundamentals, compute_valuation, compute_technicals, "
        "compute_sentiment, compute_buffett y compute_graham. No recalcules a mano.\n"
        "3. Formula tesis falsables: qué la confirmaría y qué la refutaría.\n"
        "4. Cada afirmación cita el id de la evidencia que la sostiene; si falta un dato, "
        "es un hueco, no un cero.\n"
        "5. La postura sale del conjunto de analistas; los conflictos se dicen, no se ocultan.\n"
        "6. El tamaño de una orden lo deciden los límites de riesgo, nunca el modelo. "
        "Toda orden paper requiere aprobación humana. No es asesoría financiera.\n",
    ),
    "fundamental-filter": (
        "Filtra por P/E, P/B, ROE y márgenes para separar valor de crecimiento.",
        "# Filtro fundamental\n"
        "- Valor: P/E positivo y moderado, P/B bajo, ROE por encima del coste de capital.\n"
        "- Crecimiento: ingresos y beneficios > 10% anual con márgenes que no caen.\n"
        "- Excluye pérdidas recurrentes del filtro de valor: un P/E negativo no es barato.\n"
        "- Usa get_financial_metrics con la fecha de análisis; compara varios periodos.\n",
    ),
    "financial-statement": (
        "Lee estados financieros: calidad del beneficio, caja, deuda y dilución.",
        "# Estados financieros\n"
        "- Beneficio vs flujo de caja libre: si el FCF queda muy por debajo del beneficio, "
        "la calidad es baja.\n"
        "- Capex de mantenimiento frente a depreciación.\n"
        "- Deuda total menos caja; cobertura de intereses.\n"
        "- Acciones en circulación: emisión = dilución, recompra = retorno al accionista.\n"
        "- Usa search_line_items; los capex y dividendos llegan con signo negativo.\n",
    ),
    "risk-analysis": (
        "Mide volatilidad, drawdown y correlación antes de dimensionar.",
        "# Análisis de riesgo\n"
        "- Volatilidad anualizada y su percentil histórico: régimen tranquilo o agitado.\n"
        "- Correlación con lo que ya está en cartera: concentrar lo correlacionado suma riesgo.\n"
        "- El límite de posición sale de volatilidad por correlación sobre el valor de "
        "liquidación; respétalo siempre.\n"
        "- Expresa el riesgo de la tesis: qué precio o dato la invalidaría.\n",
    ),
    "sentiment-analysis": (
        "Interpreta titulares e insiders sin sobreponderar el ruido.",
        "# Sentimiento\n"
        "- Los titulares del día ya están en el precio; busca persistencia, no un artículo.\n"
        "- Las ventas de insiders pueden ser fiscales o planificadas; las compras discrecionales "
        "dicen más.\n"
        "- El sentimiento pesa poco frente a valoración y balance en horizontes largos.\n",
    ),
    "asset-allocation": (
        "Reparte el capital por riesgo, no por convicción.",
        "# Asignación de activos\n"
        "- Empieza por el perfil de riesgo del inversor y el efectivo disponible.\n"
        "- Limita cada posición por su volatilidad; diversifica entre activos poco "
        "correlacionados.\n"
        "- Mantener (hold) es una decisión válida cuando no hay ventaja.\n",
    ),
    "behavioral-finance": (
        "Detecta sesgos: anclaje, confirmación, aversión a la pérdida y exceso de confianza.",
        "# Finanzas conductuales\n"
        "- Anclaje: el precio de compra no dice nada del valor actual.\n"
        "- Confirmación: busca a propósito la evidencia que refutaría la tesis.\n"
        "- Exceso de confianza: una confianza alta exige evidencia de varios analistas.\n"
        "- Recencia: una racha reciente no es una tendencia estructural.\n",
    ),
    "investigating-incidents": (
        "Investiga incidentes con evidencia verificable; usa un runbook solo como guía.",
        "# Investigación de incidentes\n"
        "1. Define la alerta, el servicio afectado y la ventana temporal.\n"
        "2. Consulta métricas, logs, eventos y cambios con herramientas de solo lectura.\n"
        "3. Contrasta hipótesis con evidencia y cita sus identificadores.\n"
        "4. Expresa incertidumbre si la evidencia no sostiene una causa.\n"
        "5. Propón remedios con riesgo, radio de impacto y rollback. "
        "Toda acción mutante requiere aprobación humana.\n",
    ),
}


@lru_cache(maxsize=1)
def finance_skill_catalog() -> tuple[SkillRecord, ...]:
    """Return validated bundled finance skills in stable identifier order."""

    return load_skill_catalog(bundled_finance_root())


def finance_skill_roots(skill_ids: tuple[str, ...] | None = None) -> list[str]:
    """Filesystem roots suitable for Deep Agents' native Skills middleware."""

    records = {item.id: item for item in finance_skill_catalog()}
    selected = tuple(records) if skill_ids is None else skill_ids
    unknown = sorted(set(selected) - set(records))
    if unknown:
        raise KeyError(f"Unknown finance skill(s): {unknown}")
    return [records[skill_id].root for skill_id in selected]


def _public_skills() -> dict[str, tuple[str, str]]:
    combined = dict(_CURATED_SKILLS)
    try:
        for record in finance_skill_catalog():
            combined.setdefault(record.id, (record.description, ""))
    except FileNotFoundError:
        pass
    return combined


SKILLS = _public_skills()


def load_skill(skill_id: str, *, full: bool = False) -> str:
    """Reveal a short description first, then the full trusted guide on request."""

    summary, curated = SKILLS[skill_id]
    if not full:
        return summary
    if curated:
        return curated
    return read_skill_document(skill_id)
