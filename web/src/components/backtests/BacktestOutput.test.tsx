import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { BacktestForm } from "./BacktestForm";
import { BacktestOutput } from "./BacktestOutput";

it("renders metrics, trades, equity and signal summaries", () => {
  render(
    <BacktestOutput
      job={{
        run_id: "bt-1",
        status: "complete",
        progress: 100,
        result: {
          metrics: {
            total_return: 0.18,
            sharpe_ratio: 1.42,
            max_drawdown: -0.08,
            trade_count: 3,
          },
          equity_curve: [{ date: "2026-01-02", equity: 118000, cash: 20000 }],
          trades: [
            {
              symbol: "AAPL",
              side: "long",
              entry_date: "2026-01-02",
              entry_price: 100,
              quantity: 10,
              pnl: 1800,
            },
          ],
          signal_summary: [
            {
              symbol: "AAPL",
              latest_raw_signal: 0.8,
              latest_applied_weight: 0.4,
            },
          ],
        },
      }}
    />,
  );
  expect(screen.getByText("18.00%")).toBeVisible();
  expect(screen.getByText("1.42")).toBeVisible();
  expect(screen.getAllByText("AAPL")).toHaveLength(2);
  expect(screen.getByText("$118,000.00")).toBeVisible();
});

it("validates dates before starting and exposes cancellation", () => {
  const start = vi.fn();
  const cancel = vi.fn();
  render(
    <BacktestForm
      algorithms={[{ key: "momentum", name: "Momentum", description: "Trend" }]}
      onStart={start}
      onCancel={cancel}
      running
    />,
  );
  fireEvent.change(screen.getByLabelText("Inicio del backtest"), {
    target: { value: "2026-12-31" },
  });
  fireEvent.change(screen.getByLabelText("Fin del backtest"), {
    target: { value: "2026-01-01" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ejecutar backtest" }));
  expect(screen.getByRole("alert")).toHaveTextContent("La fecha inicial");
  expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancelar backtest" }));
  expect(cancel).toHaveBeenCalledOnce();
});
