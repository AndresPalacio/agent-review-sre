import type { AlgorithmBacktestJob } from "../../types/compat";

const record = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const list = (value: unknown): Array<Record<string, unknown>> =>
  Array.isArray(value)
    ? (value.filter(
        (item) => typeof item === "object" && item !== null,
      ) as Array<Record<string, unknown>>)
    : [];
const percent = (value: unknown) =>
  typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
const number = (value: unknown, digits = 2) =>
  typeof value === "number" ? value.toFixed(digits) : "—";
const money = (value: unknown) =>
  typeof value === "number"
    ? value.toLocaleString("en-US", { style: "currency", currency: "USD" })
    : "—";

export function BacktestOutput({ job }: { job: AlgorithmBacktestJob | null }) {
  if (!job)
    return (
      <div className="backtest-empty">
        <p>Sin resultados todavía</p>
        <span>Configura un intervalo y ejecuta el backtest.</span>
      </div>
    );
  const result = record(job.result);
  const metrics = record(result.metrics);
  const trades = list(result.trades);
  const equity = list(result.equity_curve);
  const signals = list(result.signal_summary);
  return (
    <div className="backtest-output">
      <div className="backtest-progress">
        <span>
          <strong>{job.status}</strong>
          <small>{number(job.progress, 0)}%</small>
        </span>
        <div>
          <i
            style={{ width: `${Math.min(100, Number(job.progress ?? 0))}%` }}
          />
        </div>
      </div>
      {job.error && (
        <p role="alert" className="settings-error">
          {job.error}
        </p>
      )}
      {Object.keys(metrics).length > 0 && (
        <div className="metric-grid">
          <article>
            <span>Retorno total</span>
            <strong>{percent(metrics.total_return)}</strong>
          </article>
          <article>
            <span>Sharpe</span>
            <strong>{number(metrics.sharpe_ratio)}</strong>
          </article>
          <article>
            <span>Drawdown máx.</span>
            <strong>{percent(metrics.max_drawdown)}</strong>
          </article>
          <article>
            <span>Operaciones</span>
            <strong>{number(metrics.trade_count, 0)}</strong>
          </article>
        </div>
      )}
      {equity.length > 0 && (
        <section>
          <h3>Curva de capital</h3>
          <div className="equity-list">
            {equity.map((point, index) => (
              <div key={String(point.date ?? index)}>
                <span>{String(point.date ?? "")}</span>
                <strong>{money(point.equity)}</strong>
              </div>
            ))}
          </div>
        </section>
      )}
      {trades.length > 0 && (
        <section>
          <h3>Operaciones</h3>
          <table>
            <thead>
              <tr>
                <th>Símbolo</th>
                <th>Lado</th>
                <th>Entrada</th>
                <th>Cantidad</th>
                <th>P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((trade, index) => (
                <tr key={`${String(trade.symbol)}-${index}`}>
                  <td>{String(trade.symbol)}</td>
                  <td>{String(trade.side)}</td>
                  <td>{money(trade.entry_price)}</td>
                  <td>{number(trade.quantity, 0)}</td>
                  <td>{money(trade.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {signals.length > 0 && (
        <section>
          <h3>Señales</h3>
          <div className="signal-chips">
            {signals.map((signal) => (
              <span key={String(signal.symbol)}>
                <strong>{String(signal.symbol)}</strong>{" "}
                {number(signal.latest_raw_signal)}
              </span>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
