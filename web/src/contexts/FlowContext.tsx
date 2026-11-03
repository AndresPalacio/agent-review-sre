import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiClient, apiClient } from "../lib/api-client";
import type { Flow, FlowSummary } from "../types/compat";
import { useTabs } from "./TabsContext";

interface FlowState {
  flows: FlowSummary[];
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  openFlow: (id: number) => Promise<void>;
  createFlow: (name: string, description?: string) => Promise<Flow | null>;
  saveFlow: (id: number) => Promise<Flow | null>;
  renameFlow: (id: number, name: string) => Promise<void>;
  duplicateFlow: (id: number, name: string) => Promise<void>;
  deleteFlow: (id: number) => Promise<void>;
}

const FlowContext = createContext<FlowState | null>(null);
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "No se pudo completar la operación";

export function FlowProvider({ children, client = apiClient }: { children: ReactNode; client?: ApiClient }) {
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const loadRef = useRef<{ sequence: number; controller: AbortController } | null>(null);
  const sequence = useRef(0);
  const { tabs, openTab, markSaved, renameTab, removeTab } = useTabs();

  const refresh = useCallback(async () => {
    const controller = new AbortController();
    setLoading(true);
    try {
      setFlows(await client.flows.list(controller.signal));
      setError("");
    } catch (reason) {
      if ((reason as { name?: string }).name !== "AbortError") setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => () => loadRef.current?.controller.abort(), []);

  const openFlow = useCallback(async (id: number) => {
    const existing = tabs.find((tab) => tab.flow.id === id);
    if (existing?.dirty) {
      openTab(existing.flow);
      return;
    }
    loadRef.current?.controller.abort();
    const controller = new AbortController();
    const current = ++sequence.current;
    loadRef.current = { sequence: current, controller };
    try {
      const flow = await client.flows.get(id, controller.signal);
      if (loadRef.current?.sequence !== current || controller.signal.aborted) return;
      openTab(flow);
      setError("");
    } catch (reason) {
      if ((reason as { name?: string }).name !== "AbortError") setError(errorMessage(reason));
    }
  }, [client, openTab, tabs]);

  const createFlow = useCallback(async (name: string, description = "") => {
    try {
      const created = await client.flows.create({
        name,
        description,
        nodes: [],
        edges: [],
        is_template: false,
      });
      setFlows((current) => [created, ...current]);
      openTab(created);
      setError("");
      return created;
    } catch (reason) {
      setError(errorMessage(reason));
      return null;
    }
  }, [client, openTab]);

  const saveFlow = useCallback(async (id: number) => {
    const tab = tabs.find((item) => item.flow.id === id);
    if (!tab) return null;
    try {
      const saved = await client.flows.update(id, {
        name: tab.flow.name,
        description: tab.flow.description,
        nodes: tab.document.nodes,
        edges: tab.document.edges,
        viewport: tab.document.viewport,
        data: tab.flow.data,
        is_template: tab.flow.is_template,
        tags: tab.flow.tags,
      } as never);
      markSaved(saved);
      setFlows((current) => current.map((item) => item.id === id ? saved : item));
      setError("");
      return saved;
    } catch (reason) {
      setError(errorMessage(reason));
      return null;
    }
  }, [client, markSaved, tabs]);

  const renameFlow = useCallback(async (id: number, name: string) => {
    try {
      const updated = await client.flows.update(id, { name });
      setFlows((current) => current.map((item) => item.id === id ? updated : item));
      renameTab(id, updated.name);
      setError("");
    } catch (reason) { setError(errorMessage(reason)); }
  }, [client, renameTab]);

  const duplicateFlow = useCallback(async (id: number, name: string) => {
    try {
      const copy = await client.flows.duplicate(id, name);
      setFlows((current) => [copy, ...current]);
      setError("");
    } catch (reason) { setError(errorMessage(reason)); }
  }, [client]);

  const deleteFlow = useCallback(async (id: number) => {
    try {
      await client.flows.delete(id);
      setFlows((current) => current.filter((item) => item.id !== id));
      removeTab(id);
      setError("");
    } catch (reason) { setError(errorMessage(reason)); }
  }, [client, removeTab]);

  const value = useMemo<FlowState>(() => ({
    flows, loading, error, refresh, openFlow, createFlow, saveFlow,
    renameFlow, duplicateFlow, deleteFlow,
  }), [flows, loading, error, refresh, openFlow, createFlow, saveFlow, renameFlow, duplicateFlow, deleteFlow]);
  return <FlowContext.Provider value={value}>{children}</FlowContext.Provider>;
}

export function useFlows(): FlowState {
  const value = useContext(FlowContext);
  if (!value) throw new Error("useFlows must be used inside FlowProvider");
  return value;
}

