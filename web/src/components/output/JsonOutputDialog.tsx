import type { JsonValue } from "../../types/compat";

export function JsonOutputDialog({ open, result, onClose }: { open: boolean; result: Record<string, JsonValue> | null; onClose: () => void }) {
  if (!open || !result) return null;
  return <div className="dialog-backdrop" role="presentation"><section className="json-dialog" role="dialog" aria-modal="true" aria-label="Resultado JSON"><header><h2>Resultado JSON</h2><button aria-label="Cerrar JSON" onClick={onClose}>×</button></header><pre>{JSON.stringify(result, null, 2)}</pre></section></div>;
}
