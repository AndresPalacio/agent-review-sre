import { FileJson2, FileText, TrendingDown, TrendingUp } from "lucide-react";
import type { JsonValue } from "../../types/compat";

interface OutputTabProps {
  result: Record<string, JsonValue> | null;
  onReport: () => void;
  onJson: () => void;
}

const record = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};

export function OutputTab({ result, onReport, onJson }: OutputTabProps) {
  if (!result) return <div className="output-empty"><p>Configura y ejecuta el flujo activo</p><span>Las decisiones aparecerán aquí cuando termine el análisis.</span></div>;
  const decisions = record(result.decisions);
  return <div className="run-output">
    <div className="output-toolbar"><strong>Decisiones de inversión</strong><span><button onClick={onReport}><FileText size={13} /> Informe</button><button onClick={onJson}><FileJson2 size={13} /> JSON</button></span></div>
    {!Object.keys(decisions).length ? <p className="output-empty">La ejecución terminó sin decisiones.</p> : <div className="decision-grid">
      {Object.entries(decisions).map(([ticker, raw]) => {
        const decision = record(raw);
        const action = String(decision.action ?? "hold").toUpperCase();
        const positive = action === "LONG" || action === "BUY" || action === "COVER";
        return <article className="decision-card" key={ticker}>
          <div><strong>{ticker}</strong><span className={positive ? "positive" : action === "HOLD" ? "neutral" : "negative"}>{positive ? <TrendingUp size={15} /> : <TrendingDown size={15} />}{action}</span></div>
          <dl><div><dt>Cantidad</dt><dd>{String(decision.quantity ?? "—")}</dd></div><div><dt>Confianza</dt><dd>{typeof decision.confidence === "number" ? `${decision.confidence.toFixed(1)}%` : "—"}</dd></div></dl>
        </article>;
      })}
    </div>}
  </div>;
}

