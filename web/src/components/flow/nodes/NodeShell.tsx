import { Handle, Position, type NodeProps } from "@xyflow/react";
import { AlertTriangle, Bot, FileJson2, PieChart, Target } from "lucide-react";
import type { FlowNode } from "../../../types/flow";

const icons = {
  "portfolio-start-node": PieChart,
  "stock-analyzer-node": Target,
  "agent-node": Bot,
  "portfolio-manager-node": PieChart,
  "investment-report-node": Target,
  "json-output-node": FileJson2,
  "unknown-node": AlertTriangle,
};

export function NodeShell({ id, type, data, selected }: NodeProps<FlowNode>) {
  const kind = type ?? "unknown-node";
  const Icon = icons[kind];
  const isInput = kind === "portfolio-start-node" || kind === "stock-analyzer-node";
  const isOutput = kind === "investment-report-node" || kind === "json-output-node";
  const unknown = kind === "unknown-node";
  return (
    <article className={`flow-node flow-node-${kind} ${selected ? "selected" : ""}`} data-testid={id}>
      {!isInput && <Handle type="target" position={Position.Left} />}
      <div className="node-icon"><Icon size={17} /></div>
      <div className="node-copy">
        <strong>{unknown ? "Componente desconocido" : data.name}</strong>
        <small>{unknown ? String(data.original_type ?? "Tipo no reconocido") : data.description}</small>
      </div>
      <span className={`node-status ${String(data.status).toLowerCase()}`} title={String(data.status)} />
      {!isOutput && <Handle type="source" position={Position.Right} />}
    </article>
  );
}

