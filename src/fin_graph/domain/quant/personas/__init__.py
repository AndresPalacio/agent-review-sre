"""Deterministic persona scorers used by the compatibility analyst team."""

from __future__ import annotations

from collections.abc import Iterable

from fin_graph.domain.quant.common import Component


def midpoint_confidence(score: float, max_score: float) -> float:
    """Confidence as distance from the neutral midpoint, matching existing quant scorers."""

    if max_score <= 0:
        return 0.0
    return min(100.0, round(abs(score / max_score - 0.5) * 200, 2))


def component_total(components: Iterable[Component]) -> float:
    return sum(component.score or 0.0 for component in components)


def normalized_component(name: str, components: Iterable[Component]) -> Component:
    """Combine existing domain components on a persona's public 0-10 scale."""

    items = tuple(components)
    score = component_total(items)
    maximum = sum(component.max_score or 0.0 for component in items)
    normalized = 10 * score / maximum if maximum else 0.0
    return Component(
        name=name,
        score=normalized,
        max_score=10,
        details="; ".join(item.details for item in items if item.details),
        metrics={item.name: item.metrics for item in items},
    )
