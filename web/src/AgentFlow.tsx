import "./AgentFlow.css";

export type AgentFlowData = {
  thread_id: string;
  run?: { status?: string };
  topology: {
    nodes: { id: string; label: string; kind?: string }[];
    edges: { source: string; target: string; label?: string }[];
  };
  execution: {
    steps_truncated?: boolean;
    steps?: {
      id: string;
      node: string | null;
      label: string;
      ts?: string;
      domain?: string;
      wave?: number;
      evidence_ids?: string[];
      provenance: "observed" | "derived";
    }[];
    waves?: {
      number: number;
      provenance: "observed" | "derived";
      tasks?: { domain: string; provenance: "observed" | "derived" }[];
      specialists?: {
        domain: string;
        evidence_ids?: string[];
        provenance: "observed" | "derived";
      }[];
    }[];
    findings?: {
      domain?: string;
      wave?: number;
      summary?: string;
      evidence_ids?: string[];
      provenance: "observed" | "derived";
    }[];
    evidence?: {
      id: string;
      integration?: string;
      tool?: string;
      summary?: string;
      provenance: "observed" | "derived";
    }[];
    verification?: {
      passed?: boolean | null;
      score?: number | null;
      claims?: { statement?: string; status?: string; reason?: string }[];
      missing_sections?: string[];
      provenance?: "observed" | "derived";
    };
    diagnosis?: {
      root_cause?: string;
      category?: string;
      confidence?: number;
      provenance?: "observed" | "derived";
    };
  };
};

const provenanceLabel = (value: "observed" | "derived") =>
  value === "observed" ? "Observado" : "Reconstruido";

function EvidenceLinks({ ids }: { ids?: string[] }) {
  if (!ids?.length) return null;
  return (
    <span className="agent-evidence-links">
      {ids.map((id) => (
        <a key={id} href={`#agent-evidence-${encodeURIComponent(id)}`}>
          {id}
        </a>
      ))}
    </span>
  );
}

export function AgentFlow({ data }: { data: AgentFlowData }) {
  const { topology, execution } = data;
  const nodeNames = Object.fromEntries(
    topology.nodes.map((node) => [node.id, node.label]),
  );
  const outgoing = (id: string) =>
    topology.edges.filter((edge) => edge.source === id);
  const stepTitle = (step: NonNullable<typeof execution.steps>[number]) =>
    step.node === "specialist" && step.domain
      ? `Especialista: ${step.domain}`
      : step.node
        ? (nodeNames[step.node] ?? step.label)
        : step.label;
  return (
    <div className="agent-flow">
      <section className="panel">
        <div className="section-head">
          <h2>Cómo funciona el sistema</h2>
          <span className="badge">Workflow + agentes</span>
        </div>
        <p>
          El grafo dirige la investigación. El plan decide qué especialistas
          consultan las fuentes; después, el sistema contrasta el diagnóstico
          con la evidencia y puede abrir otra ronda.
        </p>
        <div className="agent-topology">
          {topology.nodes.map((node) => (
            <article key={node.id} className="agent-node">
              <small>{node.kind ?? "etapa"}</small>
              <h3>{node.label}</h3>
              {outgoing(node.id).length > 0 && (
                <ul>
                  {outgoing(node.id).map((edge, index) => (
                    <li key={`${edge.target}-${index}`}>
                      <span aria-hidden="true">→</span>{" "}
                      {nodeNames[edge.target] ?? edge.target}
                      {edge.label && <em> · {edge.label}</em>}
                    </li>
                  ))}
                </ul>
              )}
            </article>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>Qué ocurrió en esta ejecución</h2>
        <p className="muted">
          Observado: evento guardado durante la ejecución. Reconstruido: resumen
          del estado persistido.
        </p>
        {execution.steps_truncated && (
          <p className="notice">
            Se muestran los primeros 500 eventos. Consulta el historial paginado
            para ver el resto.
          </p>
        )}
        {execution.steps?.length ? (
          <ol className="agent-steps">
            {execution.steps.map((step) => (
              <li key={step.id}>
                <div className="section-head">
                  <strong>{stepTitle(step)}</strong>
                  <span className="badge">
                    {provenanceLabel(step.provenance)}
                  </span>
                </div>
                <div className="agent-step-meta">
                  {step.wave !== undefined && <span>Ronda {step.wave}</span>}
                  {step.domain && <span>{step.domain}</span>}
                  {step.ts && <time dateTime={step.ts}>{step.ts}</time>}
                  <EvidenceLinks ids={step.evidence_ids} />
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">
            Aún no hay pasos observados para esta ejecución.
          </p>
        )}
      </section>

      {!!execution.waves?.length && (
        <section className="panel">
          <h2>Rondas de investigación</h2>
          <div className="agent-waves">
            {execution.waves.map((wave) => (
              <article key={wave.number}>
                <div className="section-head">
                  <h3>Ronda {wave.number}</h3>
                  <span className="badge">
                    {provenanceLabel(wave.provenance)}
                  </span>
                </div>
                {!!wave.tasks?.length && (
                  <ul>
                    {wave.tasks.map((task, index) => (
                      <li key={`${task.domain}-${index}`}>
                        Plan: {task.domain} · {provenanceLabel(task.provenance)}
                      </li>
                    ))}
                  </ul>
                )}
                {!!wave.specialists?.length && (
                  <ul>
                    {wave.specialists.map((specialist, index) => (
                      <li key={`${specialist.domain}-${index}`}>
                        {specialist.domain} ·{" "}
                        {provenanceLabel(specialist.provenance)}{" "}
                        <EvidenceLinks ids={specialist.evidence_ids} />
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            ))}
          </div>
        </section>
      )}

      {!!execution.findings?.length && (
        <section className="panel">
          <h2>Hallazgos</h2>
          {execution.findings.map((finding, index) => (
            <article key={index} className="agent-finding">
              <strong>
                {finding.domain ?? "Especialista"}
                {finding.wave !== undefined && ` · ronda ${finding.wave}`}
              </strong>
              <span className="badge">
                {provenanceLabel(finding.provenance)}
              </span>
              <p>{finding.summary}</p>
              <EvidenceLinks ids={finding.evidence_ids} />
            </article>
          ))}
        </section>
      )}

      <div className="agent-result">
        <section className="panel">
          <div className="section-head">
            <h2>Verificación</h2>
            {execution.verification?.provenance && (
              <span className="badge">
                {provenanceLabel(execution.verification.provenance)}
              </span>
            )}
          </div>
          {execution.verification?.passed == null ? (
            <p className="muted">Pendiente de verificación.</p>
          ) : (
            <>
              <p>
                {execution.verification.passed
                  ? "Aprobada con evidencia"
                  : "Requiere más evidencia"}
              </p>
              {typeof execution.verification.score === "number" && (
                <p className="muted">
                  Puntuación: {Math.round(execution.verification.score * 100)}%
                </p>
              )}
              {!!execution.verification.claims?.length && (
                <ul>
                  {execution.verification.claims.map((claim, index) => (
                    <li key={index}>
                      {claim.statement} · {claim.status}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
        <section className="panel">
          <div className="section-head">
            <h2>Diagnóstico resultante</h2>
            {execution.diagnosis?.provenance && (
              <span className="badge">
                {provenanceLabel(execution.diagnosis.provenance)}
              </span>
            )}
          </div>
          <p>
            {execution.diagnosis?.root_cause?.trim() || "Aún sin causa establecida."}
          </p>
        </section>
      </div>

      {!!execution.evidence?.length && (
        <section className="panel">
          <h2>Fuentes consultadas</h2>
          <div className="agent-sources">
            {execution.evidence.map((source) => (
              <article
                id={`agent-evidence-${encodeURIComponent(source.id)}`}
                key={source.id}
              >
                <strong>{source.id}</strong>
                <span className="badge">
                  {provenanceLabel(source.provenance)}
                </span>
                <small>
                  {[source.integration, source.tool]
                    .filter(Boolean)
                    .join(" · ")}
                </small>
                <p>{source.summary}</p>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
