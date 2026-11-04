import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ApiClient, apiClient } from "../../lib/api-client";
import type { JsonValue } from "../../types/compat";

const record = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};

export function AnalysisExplainView({
  client = apiClient,
}: {
  client?: ApiClient;
}) {
  const [thread, setThread] = useState("");
  const [data, setData] = useState<Record<string, JsonValue> | null>(null);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!thread.trim()) return;
    setError("");
    try {
      setData(await client.native.explainAnalysis(thread.trim()));
    } catch {
      setError("No se encontró el análisis o la sesión no tiene acceso");
    }
  }
  const execution = record(data?.execution);
  const diagnosis = record(execution.diagnosis);
  const verification = record(execution.verification);
  return (
    <section className="native-card explain-view">
      <header>
        <div>
          <span>TRAZABILIDAD</span>
          <h2>Explicar análisis</h2>
        </div>
      </header>
      <form onSubmit={(event) => void submit(event)}>
        <input
          aria-label="ID de análisis"
          value={thread}
          onChange={(event) => setThread(event.target.value)}
          placeholder="thread id"
        />
        <button aria-label="Explicar análisis">
          <Search size={14} /> Explicar
        </button>
      </form>
      {error && (
        <p role="alert" className="settings-error">
          {error}
        </p>
      )}
      {data && (
        <div className="explanation">
          <article>
            <span>Conclusión del análisis</span>
            <strong>{String(diagnosis.root_cause ?? "No establecida")}</strong>
          </article>
          <article>
            <span>Verificación</span>
            <strong>
              {verification.passed === true
                ? "Superada"
                : verification.passed === false
                  ? "Fallida"
                  : "Pendiente"}
            </strong>
          </article>
          <details>
            <summary>Ver explicación completa</summary>
            <pre>{JSON.stringify(data, null, 2)}</pre>
          </details>
        </div>
      )}
    </section>
  );
}
