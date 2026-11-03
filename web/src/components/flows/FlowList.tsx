import { Copy, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { useFlows } from "../../contexts/FlowContext";
import type { FlowSummary } from "../../types/compat";
import { FlowDialog } from "./FlowDialog";

export function FlowList({ values }: { values: FlowSummary[] }) {
  const { openFlow, renameFlow, duplicateFlow, deleteFlow } = useFlows();
  const [menu, setMenu] = useState<number | null>(null);
  const [renaming, setRenaming] = useState<FlowSummary | null>(null);
  if (!values.length) return <div className="empty-panel"><p>Aún no hay flujos</p><span>Crea uno para diseñar tu estrategia.</span></div>;
  return <div className="flow-list">
    {values.map((flow) => <article className="flow-list-item" key={flow.id}>
      <button className="flow-open" aria-label={`Abrir ${flow.name}`} onClick={() => void openFlow(flow.id)}>
        <span>{flow.name.slice(0, 1).toUpperCase()}</span><div><strong>{flow.name}</strong><small>{flow.description || "Estrategia de inversión"}</small></div>
      </button>
      <button className="flow-menu-button" aria-label={`Acciones de ${flow.name}`} onClick={() => setMenu(menu === flow.id ? null : flow.id)}><MoreHorizontal size={15} /></button>
      {menu === flow.id && <div className="flow-menu" role="menu">
        <button role="menuitem" aria-label={`Renombrar ${flow.name}`} onClick={() => { setRenaming(flow); setMenu(null); }}><Pencil size={13} /> Renombrar</button>
        <button role="menuitem" aria-label={`Duplicar ${flow.name}`} onClick={() => { setMenu(null); void duplicateFlow(flow.id, `${flow.name} copy`); }}><Copy size={13} /> Duplicar</button>
        <button role="menuitem" aria-label={`Eliminar ${flow.name}`} className="danger" onClick={() => {
          setMenu(null);
          if (window.confirm(`Eliminar "${flow.name}" permanentemente?`)) void deleteFlow(flow.id);
        }}><Trash2 size={13} /> Eliminar</button>
      </div>}
    </article>)}
    <FlowDialog open={renaming !== null} title="Renombrar flujo" initialName={renaming?.name} submitLabel="Guardar nombre" onClose={() => setRenaming(null)} onSubmit={async (name) => { if (renaming) await renameFlow(renaming.id, name); }} />
  </div>;
}

