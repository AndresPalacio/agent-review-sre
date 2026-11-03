import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { FlowNodeData, NodeStatus } from "../types/flow";

interface NodeState {
  transient: Readonly<Record<string, Partial<FlowNodeData>>>;
  updateNode: (id: string, value: Partial<FlowNodeData>) => void;
  setStatus: (id: string, status: NodeStatus) => void;
  reset: () => void;
}

const NodeContext = createContext<NodeState | null>(null);

export function NodeProvider({ children }: { children: ReactNode }) {
  const [transient, setTransient] = useState<Record<string, Partial<FlowNodeData>>>({});
  const value = useMemo<NodeState>(() => ({
    transient,
    updateNode: (id, update) => setTransient((current) => ({
      ...current,
      [id]: { ...current[id], ...update },
    })),
    setStatus: (id, status) => setTransient((current) => ({
      ...current,
      [id]: { ...current[id], status },
    })),
    reset: () => setTransient({}),
  }), [transient]);
  return <NodeContext.Provider value={value}>{children}</NodeContext.Provider>;
}

export function useNodeState(): NodeState {
  const value = useContext(NodeContext);
  if (!value) throw new Error("useNodeState must be used inside NodeProvider");
  return value;
}

