import { useEffect, useState, type FormEvent } from "react";

interface FlowDialogProps {
  open: boolean;
  title: string;
  initialName?: string;
  submitLabel: string;
  onClose: () => void;
  onSubmit: (name: string, description: string) => void | Promise<void>;
}

export function FlowDialog({ open, title, initialName = "", submitLabel, onClose, onSubmit }: FlowDialogProps) {
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setName(initialName); setDescription(""); } }, [open, initialName]);
  if (!open) return null;
  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = name.trim();
    if (!value) return;
    setBusy(true);
    try { await onSubmit(value, description.trim()); onClose(); } finally { setBusy(false); }
  }
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="flow-dialog" role="dialog" aria-modal="true" aria-labelledby="flow-dialog-title">
      <h2 id="flow-dialog-title">{title}</h2>
      <form onSubmit={(event) => void submit(event)}>
        <label>Nombre<input aria-label="Nombre" autoFocus maxLength={200} value={name} onChange={(event) => setName(event.target.value)} /></label>
        {submitLabel === "Crear" && <label>Descripción<textarea aria-label="Descripción" value={description} onChange={(event) => setDescription(event.target.value)} /></label>}
        <div className="dialog-actions"><button type="button" onClick={onClose}>Cancelar</button><button type="submit" disabled={busy || !name.trim()}>{busy ? "Guardando…" : submitLabel}</button></div>
      </form>
    </section>
  </div>;
}

