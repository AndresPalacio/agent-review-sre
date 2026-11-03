import { Focus, Redo2, Undo2 } from "lucide-react";
import { useReactFlow } from "@xyflow/react";

export function FlowControls() {
  const { fitView } = useReactFlow();
  return (
    <div className="flow-controls">
      <label><span className="sr-only">Nombre del flujo</span><input aria-label="Nombre del flujo" placeholder="Nombre del flujo" /></label>
      <button aria-label="Deshacer" disabled><Undo2 size={14} /></button>
      <button aria-label="Rehacer" disabled><Redo2 size={14} /></button>
      <button aria-label="Centrar flujo" onClick={() => void fitView({ padding: 0.25 })}><Focus size={14} /></button>
    </div>
  );
}
