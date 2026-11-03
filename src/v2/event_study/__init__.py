"""Historical event-study API delegated to the native point-in-time engine."""

from fin_graph.quant.event_study.engine import run_event_study as compute_car
from fin_graph.quant.event_study.models import (
    AggregateResult,
    BootstrapCI,
    EventCAR,
    EventStudyRequest,
    EventStudyResult,
    MarketModelFit,
    WindowStats,
)
from fin_graph.quant.event_study.plot import plot_event_study

plot_car_by_source = plot_event_study
plot_car_distribution = plot_event_study
plot_cumulative_ar = plot_event_study

__all__ = [
    "AggregateResult",
    "BootstrapCI",
    "EventCAR",
    "EventStudyRequest",
    "EventStudyResult",
    "MarketModelFit",
    "WindowStats",
    "compute_car",
    "plot_car_by_source",
    "plot_car_distribution",
    "plot_cumulative_ar",
]
