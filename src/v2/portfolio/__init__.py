"""Native signal consensus and portfolio sizing."""

from fin_graph.quant.portfolio import combine_signals, size_from_signal

build_consensus = combine_signals

__all__ = ["build_consensus", "combine_signals", "size_from_signal"]
