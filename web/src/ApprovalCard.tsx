import { useState } from "react";
import { ApiError } from "./api";
export type ApprovalRequest = {
  action_requests: {
    name: string;
    args?: Record<string, unknown>;
    arguments?: Record<string, unknown>;
    description?: string;
  }[];
  review_configs?: { action_name: string; allowed_decisions: string[] }[];
};
export type Decision = {
  type: string;
  edited_action?: { name: string; args: Record<string, unknown> };
};
export function ApprovalCard({
  requests,
  ids,
  canApprove,
  onSubmit,
  onStale,
}: {
  requests: ApprovalRequest[];
  ids: string[];
  canApprove: boolean;
  onSubmit: (body: {
    decisions: Decision[];
    approval_ids: string[];
  }) => Promise<unknown>;
  onStale: () => void | Promise<void>;
}) {
  const actions = requests.flatMap((r) =>
    r.action_requests.map((a) => ({
      ...a,
      allowed:
        r.review_configs?.find((c) => c.action_name === a.name)
          ?.allowed_decisions ?? [],
    })),
  );
  const [choices, setChoices] = useState<Record<number, string>>({});
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    setError("");
    let decisions: Decision[];
    try {
      decisions = actions.map((a, i) => {
        const type =
          choices[i] ??
          (a.allowed.includes("approve") ? "approve" : a.allowed[0]);
        if (!type || !a.allowed.includes(type))
          throw new Error("La decisión no está permitida.");
        if (type !== "edit") return { type };
        const args: unknown = JSON.parse(
          edits[i] ?? JSON.stringify(a.args ?? a.arguments ?? {}),
        );
        if (!args || Array.isArray(args) || typeof args !== "object")
          throw new Error("Los argumentos deben ser un objeto JSON.");
        return {
          type,
          edited_action: {
            name: a.name,
            args: args as Record<string, unknown>,
          },
        };
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Argumentos inválidos");
      return;
    }
    setBusy(true);
    try {
      await onSubmit({ decisions, approval_ids: ids });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError(
          "La solicitud se actualizó o ya fue resuelta. Revisa el estado recargado antes de decidir.",
        );
        try {
          await onStale();
        } catch (reloadError) {
          setError(
            `La solicitud se actualizó. No se pudo recargar: ${reloadError instanceof Error ? reloadError.message : "error de conexión"}. Recarga el hilo antes de decidir.`,
          );
        }
      } else
        setError(
          e instanceof Error ? e.message : "No se pudo registrar la decisión",
        );
    } finally {
      setBusy(false);
    }
  }
  if (!actions.length) return null;
  return (
    <section className="approval panel">
      <div className="eyebrow">CONTROL HUMANO</div>
      <h2>Aprobación pendiente</h2>
      <p>
        Revisa la preparación y los argumentos. La acción solo se ejecuta
        después de confirmar una decisión permitida.
      </p>
      {actions.map((a, i) => (
        <article className="approval-action" key={i}>
          <h3>{a.name}</h3>
          {a.description && <p className="prewrap">{a.description}</p>}
          <details open>
            <summary>Argumentos de la acción</summary>
            <pre>{JSON.stringify(a.args ?? a.arguments ?? {}, null, 2)}</pre>
          </details>
          {canApprove && (
            <>
              <label>
                Decisión {a.name}
                <select
                  aria-label={`Decisión ${a.name}`}
                  value={
                    choices[i] ??
                    (a.allowed.includes("approve")
                      ? "approve"
                      : (a.allowed[0] ?? ""))
                  }
                  disabled={busy}
                  onChange={(e) =>
                    setChoices({ ...choices, [i]: e.target.value })
                  }
                >
                  {a.allowed.map((d) => (
                    <option key={d} value={d}>
                      {(
                        {
                          approve: "Aprobar",
                          reject: "Rechazar",
                          edit: "Editar",
                        } as Record<string, string>
                      )[d] ?? d}
                    </option>
                  ))}
                </select>
              </label>
              {choices[i] === "edit" && (
                <label>
                  Argumentos editados {a.name}
                  <textarea
                    aria-label={`Argumentos editados ${a.name}`}
                    value={
                      edits[i] ??
                      JSON.stringify(a.args ?? a.arguments ?? {}, null, 2)
                    }
                    onChange={(e) =>
                      setEdits({ ...edits, [i]: e.target.value })
                    }
                  />
                </label>
              )}
            </>
          )}
        </article>
      ))}
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {canApprove ? (
        <button
          onClick={submit}
          disabled={
            busy || !ids.length || actions.some((a) => !a.allowed.length)
          }
        >
          {busy ? "Registrando…" : "Confirmar decisiones"}
        </button>
      ) : (
        <div className="notice">
          Tu rol permite consultar esta solicitud. Se requiere approver para
          decidir.
        </div>
      )}
    </section>
  );
}
