import { useCallback, useEffect, useState } from "react";
import type { ApiClient } from "../../lib/api-client";
import type { OllamaStatus } from "../../types/compat";

export function OllamaSettings({ client }: { client: ApiClient }) {
  const [status, setStatus] = useState<OllamaStatus | null>(null);
  const [message, setMessage] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      setStatus(await client.ollama.status());
    } catch {
      setMessage("No se pudo consultar Ollama");
    }
  }, [client]);
  useEffect(() => {
    void load();
  }, [load]);
  async function action(kind: "start" | "stop") {
    setBusy(true);
    setMessage("");
    try {
      const value = await client.ollama[kind]();
      setMessage(value.message);
      await load();
    } catch {
      setMessage("La operación de Ollama falló");
    } finally {
      setBusy(false);
    }
  }
  async function download() {
    if (!model.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      const value = await client.ollama.download(model.trim());
      setMessage(value.message);
      setModel("");
      await load();
    } catch {
      setMessage("No se pudo iniciar la descarga");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="settings-section">
      <h3>Ollama local</h3>
      <p>Ejecuta modelos locales sin enviar prompts a un proveedor externo.</p>
      {!status ? (
        <p role="status">Consultando Ollama…</p>
      ) : (
        <>
          <div className="ollama-status">
            <span className={status.running ? "online" : "offline"} />{" "}
            <strong>{status.running ? "En ejecución" : "Detenido"}</strong>
            <small>{status.server_url}</small>
          </div>
          <div className="settings-actions">
            <button
              aria-label="Iniciar Ollama"
              disabled={busy || status.running}
              onClick={() => void action("start")}
            >
              Iniciar
            </button>
            <button
              aria-label="Detener Ollama"
              disabled={busy || !status.running}
              onClick={() => void action("stop")}
            >
              Detener
            </button>
          </div>
          <h4>Modelos instalados</h4>
          {status.available_models.length ? (
            <ul>
              {status.available_models.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : (
            <p className="settings-state">Ningún modelo instalado.</p>
          )}
          <div className="inline-form">
            <input
              aria-label="Modelo de Ollama"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="qwen3:8b"
            />
            <button
              disabled={busy || !model.trim()}
              onClick={() => void download()}
            >
              Descargar
            </button>
          </div>
        </>
      )}
      {message && (
        <p role="status" className="settings-message">
          {message}
        </p>
      )}
    </section>
  );
}
