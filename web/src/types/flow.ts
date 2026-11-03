import type { Edge, Node, Viewport } from "@xyflow/react";
import type { ModelProvider } from "./compat";

export type FlowNodeType =
  | "portfolio-start-node"
  | "stock-analyzer-node"
  | "agent-node"
  | "portfolio-manager-node"
  | "investment-report-node"
  | "json-output-node"
  | "unknown-node";

export type NodeStatus = "Idle" | "IN_PROGRESS" | "COMPLETE" | "ERROR";

export interface FlowNodeData extends Record<string, unknown> {
  name: string;
  description?: string;
  status: NodeStatus;
  agent_key?: string;
  model_name?: string;
  model_provider?: ModelProvider;
  original_type?: string;
}

export type FlowNode = Node<FlowNodeData, FlowNodeType>;
export type FlowEdge = Edge;

export interface FlowDocument {
  nodes: FlowNode[];
  edges: FlowEdge[];
  viewport?: Viewport;
}

