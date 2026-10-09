import "./InvestigationContent.css";

type Row = Record<string, unknown>;
const row = (value: unknown): Row =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Row)
    : {};
const rows = (value: unknown): Row[] =>
  Array.isArray(value) ? value.map(row) : [];
const texts = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
const text = (value: unknown, fallback = "") =>
  typeof value === "string" ? value : fallback;
const confidence = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value * 100)}%`
    : "Sin estimar";
const verdicts: Record<string, string> = {
  supports: "Respaldada",
  refutes: "Descartada",
  inconclusive: "Inconclusa",
  supported: "Respaldada",
  weak: "Respaldo parcial",
  unsupported: "Sin respaldo",
};

function Raw({
  value,
  label = "Ver datos completos",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <details className="source-data">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export function Overview({ detail }: { detail: Row }) {
  const plan = row(detail.plan);
  const diagnosis = row(detail.diagnosis ?? row(detail.report).diagnosis);
  const status = row(diagnosis.status);
  const outcomes = rows(diagnosis.hypotheses);
  return (
    <div className="investigation-overview">
      <section className="panel">
        <h2>Hipótesis y plan</h2>
        {rows(plan.hypotheses).length ? (
          rows(plan.hypotheses).map((hypothesis, index) => {
            const outcome = outcomes.find(
              (item) => item.hypothesis_id === hypothesis.id,
            );
            return (
              <article
                className="investigation-item"
                key={text(hypothesis.id, String(index))}
              >
                <div className="section-head">
                  <strong>{text(hypothesis.id)}</strong>
                  <span className="badge">
                    {verdicts[text(outcome?.verdict)] ?? "Sin resolver"}
                  </span>
                </div>
                <p>{text(hypothesis.statement)}</p>
                {!!outcome?.reason && (
                  <p className="muted">{text(outcome.reason)}</p>
                )}
                <details>
                  <summary>Criterios de comprobación</summary>
                  <p>
                    <strong>Confirmar: </strong>
                    {text(hypothesis.confirm_if)}
                  </p>
                  <p>
                    <strong>Descartar: </strong>
                    {text(hypothesis.rule_out_if)}
                  </p>
                </details>
              </article>
            );
          })
        ) : (
          <p className="muted">
            Las hipótesis aparecerán cuando termine la planificación.
          </p>
        )}
        {rows(plan.tasks).length > 0 && (
          <details>
            <summary>Trabajo de especialistas</summary>
            {rows(plan.tasks).map((task, index) => (
              <p key={index}>
                <strong>{text(task.domain)}: </strong>
                {text(task.objective)}
              </p>
            ))}
          </details>
        )}
      </section>
      <section className="panel">
        <div className="section-head">
          <h2>Diagnóstico</h2>
          <span className="badge">
            Confianza: {confidence(diagnosis.confidence)}
          </span>
        </div>
        <p>
          {text(
            diagnosis.triage_summary,
            "Recopilando señales para el diagnóstico.",
          )}
        </p>
        {!!diagnosis.root_cause && (
          <p className="conclusion">{text(diagnosis.root_cause)}</p>
        )}
        <dl className="incident-status">
          {Object.entries({
            confirmed: "Confirmado",
            open: "Por resolver",
            next: "Siguiente paso",
            owner: "Responsable",
          }).map(([key, label]) =>
            status[key] ? (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{text(status[key])}</dd>
              </div>
            ) : null,
          )}
        </dl>
      </section>
    </div>
  );
}

export function InvestigationContent({
  detail,
  onRemediate,
}: {
  detail: Row;
  onRemediate?: () => void;
}) {
  const report = row(detail.report);
  const diagnosis = row(report.diagnosis ?? detail.diagnosis);
  const verification = row(report.verification ?? detail.verification);
  const evidence = rows(detail.evidence ?? report.evidence);
  const labels = row(report.labels);
  const hasReport =
    detail.report !== null &&
    detail.report !== undefined &&
    Object.keys(report).length > 0;
  const labelToId = Object.fromEntries(
    Object.entries(labels).map(([id, label]) => [String(label), id]),
  );
  function citations(values: string[]) {
    return (
      <span className="evidence-citations">
        {values.map((citation) => {
          const id = labelToId[citation] ?? citation;
          const label = text(labels[id], citation);
          return evidence.some((item) => item.id === id) ? (
            <a key={citation} href={`#evidence-${id}`}>
              {label}
            </a>
          ) : (
            <span className="badge" key={citation}>
              {label}
            </span>
          );
        })}
      </span>
    );
  }
  return (
    <div className="investigation-content">
      <section className="panel">
        <h2>Informe</h2>
        {hasReport ? (
          <>
            <p className="report-summary">{text(diagnosis.triage_summary)}</p>
            <div className="section-head">
              <span className="badge">
                {text(diagnosis.category, "Sin clasificar")}
              </span>
              <span className="muted">
                Confianza: {confidence(diagnosis.confidence)}
              </span>
            </div>
            <h3>Causa raíz</h3>
            <p>
              {text(
                diagnosis.root_cause,
                "La investigación no estableció una causa raíz.",
              )}
            </p>
            {texts(diagnosis.causal_chain).length > 0 && (
              <ol>
                {texts(diagnosis.causal_chain).map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ol>
            )}
            {rows(report.claims).length > 0 && (
              <>
                <h3>Afirmaciones y respaldo</h3>
                {rows(report.claims).map((claim, index) => (
                  <article className="investigation-item" key={index}>
                    <p>
                      {text(claim.statement)}{" "}
                      {citations(texts(claim.citations))}
                    </p>
                    <span className="badge">
                      {verdicts[text(claim.status)] ?? "Sin verificar"}
                    </span>
                    {!!claim.reason && (
                      <p className="muted">{text(claim.reason)}</p>
                    )}
                  </article>
                ))}
              </>
            )}
            {texts(diagnosis.follow_up_questions).length > 0 && (
              <>
                <h3>Preguntas abiertas</h3>
                <ul>
                  {texts(diagnosis.follow_up_questions).map((question, i) => (
                    <li key={i}>{question}</li>
                  ))}
                </ul>
              </>
            )}
            <Raw value={report} label="Informe completo en JSON" />
          </>
        ) : (
          <p className="muted">
            El informe estará disponible al finalizar la investigación.
          </p>
        )}
      </section>

      <section className="panel">
        <h2>Verificación</h2>
        {typeof verification.passed === "boolean" ? (
          <>
            <p
              className={
                verification.passed
                  ? "verification-pass"
                  : "verification-review"
              }
            >
              {verification.passed
                ? "Verificación aprobada"
                : "Requiere revisión"}
            </p>
            <p className="muted">
              Puntuación de respaldo: {confidence(verification.score)}
            </p>
            {texts(verification.missing_sections).length > 0 && (
              <p>
                Falta información:{" "}
                {texts(verification.missing_sections).join(", ")}.
              </p>
            )}
            {[verification.category_mismatch, verification.healthy_conflict]
              .filter(Boolean)
              .map((reason, i) => (
                <p key={i}>{text(reason)}</p>
              ))}
            {rows(verification.claims)
              .filter((claim) => claim.status !== "supported")
              .map((claim, i) => (
                <p key={i}>
                  <strong>{text(claim.statement)} </strong>
                  {text(claim.reason)}
                </p>
              ))}
          </>
        ) : (
          <p className="muted">La verificación todavía no está disponible.</p>
        )}
      </section>

      <section className="panel">
        <h2>Hallazgos</h2>
        {rows(detail.findings).length ? (
          rows(detail.findings).map((finding, index) => (
            <article className="investigation-item" key={index}>
              <div className="section-head">
                <h3>{text(finding.domain, "Especialista")}</h3>
                <span className="muted">
                  Confianza: {confidence(finding.confidence)}
                </span>
              </div>
              <p>{text(finding.summary)}</p>
              {citations(texts(finding.evidence_ids))}
              {texts(finding.gaps).length > 0 && (
                <p className="muted">
                  Pendiente: {texts(finding.gaps).join("; ")}
                </p>
              )}
            </article>
          ))
        ) : (
          <p className="muted">
            Esperando los resultados de los especialistas.
          </p>
        )}
      </section>

      <section className="panel">
        <div className="section-head">
          <h2>Evidencia</h2>
          <span className="badge">{evidence.length} fuentes observadas</span>
        </div>
        {evidence.length ? (
          evidence.map((item, index) => {
            const summary = text(item.summary);
            return (
              <article
                className="investigation-item"
                id={`evidence-${text(item.id, String(index))}`}
                key={text(item.id, String(index))}
              >
                <div className="section-head">
                  <h3>{text(item.tool, "Observación")}</h3>
                  <span className="badge">
                    {text(labels[text(item.id)], text(item.integration))}
                  </span>
                </div>
                <p className="muted">
                  {text(item.integration)}
                  {item.collected_at
                    ? ` · ${new Date(text(item.collected_at)).toLocaleString("es")}`
                    : ""}
                </p>
                {summary && (
                  <p>
                    {summary.length > 320
                      ? `${summary.slice(0, 320)}…`
                      : summary}
                  </p>
                )}
                {!!item.error && (
                  <p className="error">Lectura fallida: {text(item.error)}</p>
                )}
                <Raw value={item.data} label="Datos observados" />
                {summary.length > 320 && (
                  <details>
                    <summary>Resumen completo</summary>
                    <p>{summary}</p>
                  </details>
                )}
                <Raw value={item.args} label="Consulta realizada" />
              </article>
            );
          })
        ) : (
          <p className="muted">Todavía no se ha recopilado evidencia.</p>
        )}
      </section>

      <section className="panel">
        <h2>Acciones propuestas</h2>
        <p className="muted">
          Revisa el alcance. Preparar una remediación es un paso separado que
          requiere aprobación.
        </p>
        {rows(detail.actions ?? report.actions).map((action, index) => (
          <article className="investigation-item" key={index}>
            <h3>{text(action.title)}</h3>
            <p>{text(action.rationale)}</p>
            <p className="muted">
              Riesgo:{" "}
              {(
                { low: "bajo", medium: "medio", high: "alto" } as Record<
                  string,
                  string
                >
              )[text(action.risk)] ?? "sin evaluar"}{" "}
              · Alcance: {text(action.blast_radius, "por definir")} ·{" "}
              {action.reversible === true
                ? "Reversible"
                : "Reversibilidad no confirmada"}
            </p>
            {!!action.rollback && (
              <p>
                <strong>Rollback: </strong>
                {text(action.rollback)}
              </p>
            )}
          </article>
        ))}
        {onRemediate && (
          <button onClick={onRemediate}>Preparar remediación</button>
        )}
      </section>
    </div>
  );
}
