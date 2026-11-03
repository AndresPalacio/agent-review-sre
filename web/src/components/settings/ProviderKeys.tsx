import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { ApiClient } from "../../lib/api-client";
import type { ApiKeySummary } from "../../types/compat";

export function ProviderKeys({ client }: { client: ApiClient }) {
  const [keys, setKeys] = useState<ApiKeySummary[]>([]);
  const [provider, setProvider] = useState("OpenAI");
  const [secret, setSecret] = useState("");
  const [message, setMessage] = useState("");
  const load = useCallback(async () => {
    try {
      setKeys(await client.providerKeys.list());
    } catch {
      setMessage("No se pudieron cargar las claves");
    }
  }, [client]);
  useEffect(() => {
    void load();
  }, [load]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = secret;
    if (!value) return;
    try {
      await client.providerKeys.create({
        provider,
        key_value: value,
        is_active: true,
      });
      setSecret("");
      setMessage("Clave guardada de forma segura");
      await load();
    } catch {
      setSecret("");
      setMessage("No se pudo guardar la clave");
    }
  }
  return (
    <section className="settings-section">
      <h3>Claves de proveedores</h3>
      <p>
        Las claves guardadas nunca vuelven al navegador. Solo se muestra su
        estado.
      </p>
      <form className="provider-form" onSubmit={(event) => void submit(event)}>
        <label>
          Proveedor
          <select
            aria-label="Proveedor"
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
          >
            <option>OpenAI</option>
            <option>Anthropic</option>
            <option>Groq</option>
            <option>Google</option>
            <option>OpenRouter</option>
          </select>
        </label>
        <label>
          Clave
          <input
            type="password"
            autoComplete="new-password"
            aria-label="Clave del proveedor"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
          />
        </label>
        <button disabled={!secret}>Guardar clave</button>
      </form>
      <div className="key-list">
        {keys.map((key) => (
          <article key={key.provider}>
            <span className={key.is_active ? "online" : "offline"} />
            <strong>{key.provider}</strong>
            <small>{key.is_active ? "Activa" : "Inactiva"}</small>
            <button
              onClick={() =>
                void client.providerKeys.delete(key.provider).then(load)
              }
            >
              Eliminar
            </button>
          </article>
        ))}
      </div>
      {message && (
        <p role="status" className="settings-message">
          {message}
        </p>
      )}
    </section>
  );
}
