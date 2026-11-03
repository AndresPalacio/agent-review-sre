import type { JsonValue } from "../../types/compat";

const record = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};

export function InvestmentReportDialog({ open, result, onClose }: { open: boolean; result: Record<string, JsonValue> | null; onClose: () => void }) {
  if (!open || !result) return null;
  const decisions = record(result.decisions);
  const signals = record(result.analyst_signals);
  return <div className="dialog-backdrop" role="presentation"><section className="report-dialog" role="dialog" aria-modal="true" aria-label="Informe de inversión">
    <header><div><span>FIN GRAPH / INFORME</span><h2>Informe de inversión</h2></div><button aria-label="Cerrar informe" onClick={onClose}>×</button></header>
    <section><h3>Resumen de decisiones</h3><table><thead><tr><th>Símbolo</th><th>Acción</th><th>Cantidad</th><th>Confianza</th></tr></thead><tbody>{Object.entries(decisions).map(([ticker, raw]) => { const item = record(raw); return <tr key={ticker}><td>{ticker}</td><td>{String(item.action ?? "hold")}</td><td>{String(item.quantity ?? "—")}</td><td>{String(item.confidence ?? "—")}%</td></tr>; })}</tbody></table></section>
    <section><h3>Señales de analistas</h3>{Object.entries(signals).map(([agent, raw]) => <article className="signal-report" key={agent}><strong>{agent.replaceAll("_", " ")}</strong><pre>{JSON.stringify(raw, null, 2)}</pre></article>)}</section>
  </section></div>;
}

