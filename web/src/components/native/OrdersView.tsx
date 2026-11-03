import { useEffect, useState } from "react";
import { ApiClient, apiClient } from "../../lib/api-client";
import type { JsonValue } from "../../types/compat";

export function OrdersView({ client = apiClient }: { client?: ApiClient }) {
  const [orders, setOrders] = useState<Array<Record<string, JsonValue>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    client.native
      .orders(controller.signal)
      .then(setOrders)
      .catch((reason: unknown) => {
        if ((reason as { name?: string }).name !== "AbortError")
          setError("No se pudieron cargar las órdenes");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [client]);
  return (
    <section className="native-card orders-view">
      <header>
        <div>
          <span>MESA DE ÓRDENES</span>
          <h2>Órdenes</h2>
        </div>
      </header>
      {error ? (
        <p role="alert" className="settings-error">
          {error}
        </p>
      ) : loading ? (
        <p role="status" className="native-empty">
          Cargando órdenes…
        </p>
      ) : !orders.length ? (
        <p className="native-empty">No hay órdenes registradas.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Símbolo</th>
              <th>Lado</th>
              <th>Cantidad</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order, index) => (
              <tr key={String(order.client_order_id ?? index)}>
                <td>{String(order.client_order_id ?? "—")}</td>
                <td>{String(order.ticker ?? order.symbol ?? "—")}</td>
                <td>{String(order.side ?? order.action ?? "—")}</td>
                <td>{String(order.quantity ?? "—")}</td>
                <td>
                  <span className="order-status">
                    {String(order.status ?? "—")}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
