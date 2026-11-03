"""Optional event-study CAR plot writer."""

from __future__ import annotations

from pathlib import Path

from fin_graph.quant.event_study.models import EventStudyResult


def plot_event_study(result: EventStudyResult, output: Path) -> Path:
    import matplotlib.pyplot as chart
    import numpy as np

    output.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = chart.subplots()
    for event in result.events:
        axis.plot(np.cumsum(event.daily_ar), alpha=0.35, label=event.ticker)
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set(xlabel="Trading days after event", ylabel="Cumulative abnormal return")
    figure.tight_layout()
    figure.savefig(output)
    chart.close(figure)
    return output
