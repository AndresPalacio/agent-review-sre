import { useEffect, useState } from "react";
import type { ApiClient } from "../../lib/api-client";
import type { LanguageModel } from "../../types/compat";

export function CloudModels({ client }: { client: ApiClient }) {
  const [models, setModels] = useState<LanguageModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    client.models
      .list(controller.signal)
      .then(setModels)
      .catch((reason: unknown) => {
        if ((reason as { name?: string }).name !== "AbortError")
          setError("No se pudo cargar el catálogo de modelos");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [client]);
  if (loading)
    return (
      <p role="status" className="settings-state">
        Cargando modelos…
      </p>
    );
  if (error)
    return (
      <p role="alert" className="settings-error">
        {error}
      </p>
    );
  return (
    <section className="settings-section">
      <h3>Modelos en la nube</h3>
      <p>Modelos disponibles para configurar cada analista del flujo.</p>
      {!models.length ? (
        <p className="settings-state">No hay modelos publicados.</p>
      ) : (
        <div className="model-grid">
          {models.map((model) => (
            <article key={`${model.provider}-${model.model_name}`}>
              <strong>{model.display_name}</strong>
              <span>{model.provider}</span>
              <code>{model.model_name}</code>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
