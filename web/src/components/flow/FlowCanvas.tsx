import {
  Background,
  BackgroundVariant,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type NodeTypes,
  type XYPosition,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "@xyflow/react/dist/style.css";
import { createCatalogNode } from "../../data/node-catalog";
import type { FlowDocument, FlowEdge, FlowNode, FlowNodeType } from "../../types/flow";
import { FlowControls } from "./FlowControls";
import { AnalystNode } from "./nodes/AnalystNode";
import { NodeShell } from "./nodes/NodeShell";
import { OutputNode } from "./nodes/OutputNode";
import { PortfolioManagerNode } from "./nodes/PortfolioManagerNode";
import { PortfolioStartNode } from "./nodes/PortfolioStartNode";

const knownTypes = new Set<FlowNodeType>([
  "portfolio-start-node", "stock-analyzer-node", "agent-node",
  "portfolio-manager-node", "investment-report-node", "json-output-node", "unknown-node",
]);

const nodeTypes: NodeTypes = {
  "portfolio-start-node": PortfolioStartNode,
  "stock-analyzer-node": PortfolioStartNode,
  "agent-node": AnalystNode,
  "portfolio-manager-node": PortfolioManagerNode,
  "investment-report-node": OutputNode,
  "json-output-node": OutputNode,
  "unknown-node": NodeShell,
};

const finitePosition = (position: XYPosition | undefined): position is XYPosition =>
  !!position && Number.isFinite(position.x) && Number.isFinite(position.y);

function repairNodes(values: FlowNode[]): { nodes: FlowNode[]; repaired: number } {
  let repaired = 0;
  return {
    nodes: values.map((node, index) => {
      const type = node.type && knownTypes.has(node.type) ? node.type : "unknown-node";
      const position = finitePosition(node.position) ? node.position : { x: 80 + index * 24, y: 80 + index * 24 };
      if (type !== node.type || position !== node.position) repaired += 1;
      return {
        ...node,
        type,
        position,
        data: {
          ...node.data,
          name: typeof node.data?.name === "string" ? node.data.name : "Componente",
          description: typeof node.data?.description === "string" ? node.data.description : "",
          status: node.data?.status === "IN_PROGRESS" || node.data?.status === "COMPLETE" || node.data?.status === "ERROR" ? node.data.status : "Idle",
          ...(type === "unknown-node" ? { original_type: node.type ?? "sin tipo" } : {}),
        },
      };
    }),
    repaired,
  };
}

export const serializeFlow = (nodes: FlowNode[], edges: FlowEdge[]): FlowDocument => ({
  nodes: structuredClone(nodes),
  edges: structuredClone(edges),
});

const baseId = (id: string) => id.replace(/_[a-z0-9]{6}$/i, "");

export function cloneNodes(
  nodes: FlowNode[],
  makeSuffix: () => string = () => Math.random().toString(36).slice(2, 8).padEnd(6, "0"),
): FlowNode[] {
  return nodes.map((node) => ({
    ...structuredClone(node),
    id: `${baseId(node.id)}_${makeSuffix()}`,
    selected: false,
    position: { x: node.position.x + 24, y: node.position.y + 24 },
  }));
}

const category = (node: FlowNode | undefined) => {
  if (!node) return "unknown";
  if (node.type === "portfolio-start-node" || node.type === "stock-analyzer-node") return "input";
  if (node.type === "agent-node") return "analyst";
  if (node.type === "portfolio-manager-node") return "manager";
  if (node.type === "investment-report-node" || node.type === "json-output-node") return "output";
  return "unknown";
};

export function isValidFlowConnection(
  connection: { source?: string | null; target?: string | null },
  nodes: FlowNode[],
  edges: FlowEdge[],
): boolean {
  if (!connection.source || !connection.target || connection.source === connection.target) return false;
  if (edges.some((edge) => edge.source === connection.source && edge.target === connection.target)) return false;
  const source = category(nodes.find((node) => node.id === connection.source));
  const target = category(nodes.find((node) => node.id === connection.target));
  return (source === "input" && target === "analyst") ||
    (source === "analyst" && target === "manager") ||
    (source === "manager" && target === "output");
}

const ownsKeyboard = (target: EventTarget | null): boolean => {
  const element = target instanceof HTMLElement ? target : null;
  return !!element?.closest("input, textarea, select, [contenteditable='true'], [role='dialog']");
};

export interface FlowCanvasProps {
  initialNodes?: FlowNode[];
  initialEdges?: FlowEdge[];
  onChange?: (document: FlowDocument) => void;
}

function Canvas({ initialNodes = [], initialEdges = [], onChange }: FlowCanvasProps) {
  const repaired = useMemo(() => repairNodes(initialNodes), [initialNodes]);
  const [nodes, setNodes] = useState<FlowNode[]>(repaired.nodes);
  const [edges, setEdges] = useState<FlowEdge[]>(initialEdges);
  const selected = useRef<string[]>([]);
  const clipboard = useRef<FlowNode[]>([]);

  useEffect(() => {
    setNodes((current) => JSON.stringify(current) === JSON.stringify(repaired.nodes) ? current : repaired.nodes);
  }, [repaired.nodes]);
  useEffect(() => {
    setEdges((current) => JSON.stringify(current) === JSON.stringify(initialEdges) ? current : initialEdges);
  }, [initialEdges]);

  useEffect(() => { onChange?.(serializeFlow(nodes, edges)); }, [nodes, edges, onChange]);

  const onNodesChange = useCallback((changes: NodeChange<FlowNode>[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange<FlowEdge>[]) => {
    setEdges((current) => applyEdgeChanges(changes, current));
  }, []);
  const onConnect = useCallback((connection: Connection) => {
    setEdges((current) => isValidFlowConnection(connection, nodes, current)
      ? addEdge({ ...connection, id: `edge-${crypto.randomUUID()}`, markerEnd: { type: MarkerType.ArrowClosed } }, current)
      : current);
  }, [nodes]);

  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (ownsKeyboard(event.target)) return;
      if ((event.key === "Delete" || event.key === "Backspace") && selected.current.length) {
        event.preventDefault();
        setNodes((current) => current.filter((node) => !selected.current.includes(node.id)));
        setEdges((current) => current.filter((edge) => !selected.current.includes(edge.source) && !selected.current.includes(edge.target)));
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c") {
        clipboard.current = nodes.filter((node) => selected.current.includes(node.id));
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v" && clipboard.current.length) {
        event.preventDefault();
        setNodes((current) => [...current, ...cloneNodes(clipboard.current)]);
      }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [nodes]);

  const addAt = (key: string, position: XYPosition) => {
    try { setNodes((current) => [...current, createCatalogNode(key, position)]); } catch { /* unknown drag data */ }
  };

  return <div
    className="flow-canvas"
    onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}
    onDrop={(event) => {
      event.preventDefault();
      addAt(event.dataTransfer.getData("application/fin-graph-component"), { x: event.clientX - 250, y: event.clientY - 70 });
    }}
  >
    {repaired.repaired > 0 && <div className="canvas-warning" role="alert">Se reparó {repaired.repaired} componente{repaired.repaired === 1 ? "" : "s"} con datos incompatibles.</div>}
    <FlowControls />
    <ReactFlow<FlowNode, FlowEdge>
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      isValidConnection={(connection) => isValidFlowConnection(connection, nodes, edges)}
      onSelectionChange={({ nodes: chosen }) => { selected.current = chosen.map((node) => node.id); }}
      onNodeClick={(_event, node) => { selected.current = [node.id]; setNodes((current) => current.map((item) => ({ ...item, selected: item.id === node.id }))); }}
      fitView
      proOptions={{ hideAttribution: true }}
      deleteKeyCode={null}
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
    </ReactFlow>
  </div>;
}

export function FlowCanvas(props: FlowCanvasProps) {
  return <ReactFlowProvider><Canvas {...props} /></ReactFlowProvider>;
}
