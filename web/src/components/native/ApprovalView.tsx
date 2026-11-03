import { Check, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ApiClient, apiClient } from "../../lib/api-client";
import type { JsonValue } from "../../types/compat";

export function ApprovalView({
  client = apiClient,
  role = "viewer",
  onDecision,
}: {
  client?: ApiClient;
  role?: string;
  onDecision?: () => void;
}) {
  const [requests, setRequests] = useState<Array<Record<string, JsonValue>>>(
    [],
  );
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setRequests(await client.native.pendingOrders());
    } catch {
      setError("No se pudieron cargar las aprobaciones");
    }
  }, [client]);
  useEffect(() => {
    void load();
  }, [load]);
  async function decide(
    item: Record<string, JsonValue>,
    decision: "approve" | "reject",
  ) {
    const thread = String(item.thread_id ?? "");
    const ids = Array.isArray(item.approval_ids)
      ? item.approval_ids.map(String)
      : [];
    try {
      await client.native.approveOrder(thread, ids, decision);
      await load();
      onDecision?.();
    } catch {
      setError("La aprobación cambió o el rol no tiene permiso");
    }
  }
  const canApprove = role === "approver" || role === "admin";
  return (
    <section className="native-card approval-view">
      <header>
        <div>
          <span>CONTROL HUMANO</span>
          <h2>Aprobaciones pendientes</h2>
        </div>
        <strong>Rol: {role}</strong>
      </header>
      {error && (
        <p role="alert" className="settings-error">
          {error}
        </p>
      )}
      {!requests.length ? (
        <p className="native-empty">No hay órdenes pendientes.</p>
      ) : (
        <div className="approval-list">
          {requests.map((item) => {
            const thread = String(item.thread_id);
            return (
              <article key={thread}>
                <div>
                  <strong>{thread}</strong>
                  <span>{String(item.status ?? "Pendiente")}</span>
                  <pre>{JSON.stringify(item.preview ?? {}, null, 2)}</pre>
                </div>
                {canApprove ? (
                  <aside>
                    <button
                      aria-label={`Aprobar ${thread}`}
                      onClick={() => void decide(item, "approve")}
                    >
                      <Check size={14} /> Aprobar
                    </button>
                    <button
                      aria-label={`Rechazar ${thread}`}
                      className="danger-button"
                      onClick={() => void decide(item, "reject")}
                    >
                      <X size={14} /> Rechazar
                    </button>
                  </aside>
                ) : (
                  <small>Se requiere rol approver.</small>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
