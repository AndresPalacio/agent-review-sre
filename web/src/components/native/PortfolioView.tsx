import { useEffect, useState } from "react";
import { ApiClient, apiClient } from "../../lib/api-client";
import type { JsonValue } from "../../types/compat";

const record = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const money = (value: unknown) =>
  typeof value === "number"
    ? value.toLocaleString("en-US", { style: "currency", currency: "USD" })
    : "—";

export function PortfolioView({ client = apiClient }: { client?: ApiClient }) {
  const [data, setData] = useState<Record<string, JsonValue> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    client.native
      .portfolio(controller.signal)
      .then(setData)
      .catch((reason: unknown) => {
        if ((reason as { name?: string }).name !== "AbortError")
          setError("Conecta una sesión nativa con rol viewer");
      });
    return () => controller.abort();
  }, [client]);
  if (error)
    return (
      <section className="native-empty" role="alert">
        {error}
      </section>
    );
  if (!data)
    return (
      <p className="native-empty" role="status">
        Cargando cartera…
      </p>
    );
  const positions = Array.isArray(data.positions)
    ? data.positions.map(record)
    : [];
  return (
    <section className="native-card portfolio-view">
      <header>
        <div>
          <span>CARTERA DE PAPEL</span>
          <h2>Portfolio</h2>
        </div>
        <strong>{data.paper ? "Simulado" : "Real"}</strong>
      </header>
      <div className="portfolio-metrics">
        <article>
          <span>Efectivo</span>
          <strong>{money(data.cash)}</strong>
        </article>
        <article>
          <span>Valor neto</span>
          <strong>{money(data.net_liquidation_value)}</strong>
        </article>
        <article>
          <span>Margen usado</span>
          <strong>{money(data.margin_used)}</strong>
        </article>
        <article>
          <span>Órdenes en cola</span>
          <strong>{String(data.queued_orders ?? 0)}</strong>
        </article>
      </div>
      <h3>Posiciones</h3>
      {!positions.length ? (
        <p className="native-empty">No hay posiciones abiertas.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Símbolo</th>
              <th>Lado</th>
              <th>Acciones</th>
              <th>Precio</th>
              <th>Valor</th>
              <th>P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position, index) => (
              <tr key={`${String(position.ticker)}-${index}`}>
                <td>{String(position.ticker)}</td>
                <td>{String(position.side)}</td>
                <td>{String(position.shares)}</td>
                <td>{money(position.price)}</td>
                <td>{money(position.market_value)}</td>
                <td>{money(position.unrealized_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
