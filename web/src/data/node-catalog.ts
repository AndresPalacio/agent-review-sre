import type { XYPosition } from "@xyflow/react";
import { analysts } from "./analysts";
import type { FlowNode, FlowNodeType } from "../types/flow";

export type CatalogKind = "input" | "analyst" | "manager" | "output";

export interface CatalogItem {
  key: string;
  name: string;
  description: string;
  kind: CatalogKind;
  nodeType: FlowNodeType;
}

const fixedItems: CatalogItem[] = [
  { key: "portfolio-start-node", name: "Portfolio Input", description: "Capital, tickers and current positions", kind: "input", nodeType: "portfolio-start-node" },
  { key: "stock-analyzer-node", name: "Stock Input", description: "Individual securities to analyze", kind: "input", nodeType: "stock-analyzer-node" },
  { key: "portfolio_manager", name: "Portfolio Manager", description: "Combines signals into investment decisions", kind: "manager", nodeType: "portfolio-manager-node" },
  { key: "investment-report", name: "Investment Report", description: "Readable investment recommendation", kind: "output", nodeType: "investment-report-node" },
  { key: "json-output", name: "JSON Output", description: "Complete structured result", kind: "output", nodeType: "json-output-node" },
];

export const nodeCatalog: readonly CatalogItem[] = [
  ...fixedItems.slice(0, 2),
  ...analysts.map((analyst) => ({
    key: analyst.key,
    name: analyst.displayName,
    description: analyst.investingStyle,
    kind: "analyst" as const,
    nodeType: "agent-node" as const,
  })),
  ...fixedItems.slice(2),
];

const suffix = (): string => {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  return Array.from({ length: 6 }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
};

export function createCatalogNode(
  key: string,
  position: XYPosition,
  idSuffix = suffix(),
): FlowNode {
  const item = nodeCatalog.find((candidate) => candidate.key === key);
  if (!item) throw new Error(`Unknown component: ${key}`);
  const prefix = item.key;
  return {
    id: `${prefix}_${idSuffix}`,
    type: item.nodeType,
    position,
    data: {
      name: item.name,
      description: item.description,
      status: "Idle",
      ...(item.kind === "analyst" ? { agent_key: item.key } : {}),
    },
  };
}

