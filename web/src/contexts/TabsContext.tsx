import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { Flow } from "../types/compat";
import type { FlowDocument, FlowEdge, FlowNode } from "../types/flow";

export interface FlowTab {
  flow: Flow;
  document: FlowDocument;
  dirty: boolean;
  revision: string | null;
}

interface TabsState {
  tabs: FlowTab[];
  activeId: number | null;
  activeTab: FlowTab | null;
  activate: (id: number) => void;
  openTab: (flow: Flow) => void;
  closeTab: (id: number, force?: boolean) => boolean;
  updateDocument: (id: number, document: FlowDocument) => void;
  markSaved: (flow: Flow) => void;
  renameTab: (id: number, name: string) => void;
  removeTab: (id: number) => void;
}

const TabsContext = createContext<TabsState | null>(null);

const documentFrom = (flow: Flow): FlowDocument => ({
  nodes: flow.nodes as unknown as FlowNode[],
  edges: flow.edges as unknown as FlowEdge[],
  ...(flow.viewport ? { viewport: flow.viewport } : {}),
});

const sameDocument = (left: FlowDocument, right: FlowDocument) =>
  JSON.stringify(left) === JSON.stringify(right);

export function TabsProvider({ children }: { children: ReactNode }) {
  const [tabs, setTabs] = useState<FlowTab[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);

  const value = useMemo<TabsState>(() => ({
    tabs,
    activeId,
    activeTab: tabs.find((tab) => tab.flow.id === activeId) ?? null,
    activate: setActiveId,
    openTab: (flow) => {
      setTabs((current) => {
        const existing = current.find((tab) => tab.flow.id === flow.id);
        if (existing?.dirty) return current;
        const tab: FlowTab = {
          flow,
          document: documentFrom(flow),
          dirty: false,
          revision: flow.updated_at ?? flow.created_at ?? null,
        };
        return existing
          ? current.map((item) => item.flow.id === flow.id ? tab : item)
          : [...current, tab];
      });
      setActiveId(flow.id);
    },
    closeTab: (id, force = false) => {
      const target = tabs.find((tab) => tab.flow.id === id);
      if (!target) return true;
      if (target.dirty && !force && !window.confirm(`Cerrar "${target.flow.name}" sin guardar?`)) return false;
      const remaining = tabs.filter((tab) => tab.flow.id !== id);
      setTabs(remaining);
      if (activeId === id) setActiveId(remaining.at(-1)?.flow.id ?? null);
      return true;
    },
    updateDocument: (id, document) => setTabs((current) => current.map((tab) => {
      if (tab.flow.id !== id || sameDocument(tab.document, document)) return tab;
      return { ...tab, document, dirty: true };
    })),
    markSaved: (flow) => setTabs((current) => current.map((tab) => tab.flow.id === flow.id ? {
      flow,
      document: documentFrom(flow),
      dirty: false,
      revision: flow.updated_at ?? flow.created_at ?? null,
    } : tab)),
    renameTab: (id, name) => setTabs((current) => current.map((tab) =>
      tab.flow.id === id ? { ...tab, flow: { ...tab.flow, name } } : tab)),
    removeTab: (id) => {
      const remaining = tabs.filter((tab) => tab.flow.id !== id);
      setTabs(remaining);
      if (activeId === id) setActiveId(remaining.at(-1)?.flow.id ?? null);
    },
  }), [tabs, activeId]);

  return <TabsContext.Provider value={value}>{children}</TabsContext.Provider>;
}

export function useTabs(): TabsState {
  const value = useContext(TabsContext);
  if (!value) throw new Error("useTabs must be used inside TabsProvider");
  return value;
}

