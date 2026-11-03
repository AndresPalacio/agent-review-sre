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
import type { FlowRun, HedgeFundRequest, JsonValue } from "../types/compat";
import type { TypedSSEEvent } from "../lib/sse";
import { useNodeState } from "./NodeContext";
import { useTabs } from "./TabsContext";

export type RunStatus = "idle" | "running" | "complete" | "error" | "cancelled";

export interface RunConfig {
  tickers: string[];
  initialCash: number;
  startDate?: string;
  endDate?: string;
}

export interface ProgressRecord {
  id: string;
  agent: string;
  ticker?: string;
  status: string;
  analysis?: string;
  timestamp?: string;
}

interface RunState {
  status: RunStatus;
  progress: ProgressRecord[];
  result: Record<string, JsonValue> | null;
  error: string;
  history: FlowRun[];
  start: (config: RunConfig) => Promise<void>;
  cancel: () => void;
  loadHistory: (runId: number) => Promise<void>;
}

const RunContext = createContext<RunState | null>(null);
const protectedPattern = /(api[_-]?key|secret|password|bearer|tool[_-]?payload)\s*[=:]/i;

export function safeText(value: unknown): string {
  if (typeof value !== "string") return "";
  return protectedPattern.test(value) ? "[contenido protegido]" : value.slice(0, 10_000);
}

export function sanitizeForDisplay(value: unknown, depth = 0): JsonValue {
  if (depth > 8) return "[contenido anidado]";
  if (value === null || typeof value === "number" || typeof value === "boolean") return value;
  if (typeof value === "string") return safeText(value);
  if (Array.isArray(value)) return value.map((item) => sanitizeForDisplay(item, depth + 1));
  if (typeof value === "object") {
    const output: Record<string, JsonValue> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (/(api.?key|secret|password|token|tool.?payload)/i.test(key)) continue;
      output[key] = sanitizeForDisplay(item, depth + 1);
    }
    return output;
  }
  return String(value);
}

const objectValue = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};

export function RunProvider({ children, client = apiClient }: { children: ReactNode; client?: ApiClient }) {
  const { activeTab } = useTabs();
  const nodes = useNodeState();
  const [status, setStatus] = useState<RunStatus>("idle");
  const [progress, setProgress] = useState<ProgressRecord[]>([]);
  const [result, setResult] = useState<Record<string, JsonValue> | null>(null);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<FlowRun[]>([]);
  const current = useRef<{ sequence: number; controller: AbortController } | null>(null);
  const sequence = useRef(0);
  const eventCounter = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    if (!activeTab) { setHistory([]); return () => controller.abort(); }
    client.flows.runs.list(activeTab.flow.id, controller.signal)
      .then(setHistory)
      .catch((reason: unknown) => { if ((reason as { name?: string }).name !== "AbortError") setHistory([]); });
    return () => controller.abort();
  }, [activeTab?.flow.id, client]);

  useEffect(() => () => current.current?.controller.abort(), []);

  const handleEvent = useCallback((event: TypedSSEEvent, runSequence: number) => {
    if (current.current?.sequence !== runSequence) return;
    const payload = objectValue(event.data);
    if (event.event === "start") {
      nodes.reset();
      return;
    }
    if (event.event === "progress") {
      const agent = typeof payload.agent === "string" ? payload.agent : "system";
      const item: ProgressRecord = {
        id: event.id || `${runSequence}-${++eventCounter.current}`,
        agent,
        status: typeof payload.status === "string" ? payload.status : "Working",
        ...(typeof payload.ticker === "string" ? { ticker: payload.ticker } : {}),
        ...(typeof payload.analysis === "string" ? { analysis: safeText(payload.analysis) } : {}),
        ...(typeof payload.timestamp === "string" ? { timestamp: payload.timestamp } : {}),
      };
      setProgress((values) => values.some((value) => value.id === item.id) ? values : [...values, item]);
      nodes.setStatus(agent, item.status.toLocaleLowerCase() === "done" ? "COMPLETE" : "IN_PROGRESS");
      return;
    }
    if (event.event === "complete") {
      const completed = sanitizeForDisplay(payload.data);
      setResult(objectValue(completed) as Record<string, JsonValue>);
      setStatus("complete");
      setError("");
      return;
    }
    if (event.event === "error") {
      setError(safeText(payload.message) || "La ejecución no pudo completarse");
      setStatus("error");
    }
  }, [nodes]);

  const start = useCallback(async (config: RunConfig) => {
    if (!activeTab || !config.tickers.length) return;
    current.current?.controller.abort();
    const controller = new AbortController();
    const runSequence = ++sequence.current;
    current.current = { sequence: runSequence, controller };
    setStatus("running");
    eventCounter.current = 0;
    setProgress([]);
    setResult(null);
    setError("");
    nodes.reset();
    const request: HedgeFundRequest = {
      tickers: config.tickers,
      graph_nodes: activeTab.document.nodes as never,
      graph_edges: activeTab.document.edges as never,
      initial_cash: config.initialCash,
      ...(config.startDate ? { start_date: config.startDate } : {}),
      ...(config.endDate ? { end_date: config.endDate } : {}),
    };
    try {
      await client.hedgeFund.run(request, {
        onEvent: (event) => handleEvent(event, runSequence),
      }, controller.signal);
      if (current.current?.sequence === runSequence && !controller.signal.aborted) {
        setStatus((value) => value === "running" ? "complete" : value);
      }
    } catch (reason) {
      if (controller.signal.aborted || (reason as { name?: string }).name === "AbortError") return;
      if (current.current?.sequence !== runSequence) return;
      setError("Se perdió la conexión con la ejecución");
      setStatus("error");
    }
  }, [activeTab, client, handleEvent, nodes]);

  const cancel = useCallback(() => {
    current.current?.controller.abort();
    sequence.current += 1;
    current.current = null;
    setStatus("cancelled");
    setError("");
  }, []);

  const loadHistory = useCallback(async (runId: number) => {
    if (!activeTab) return;
    try {
      const saved = await client.flows.runs.get(activeTab.flow.id, runId);
      const value = sanitizeForDisplay(saved.results);
      setResult(objectValue(value) as Record<string, JsonValue>);
      setError(saved.error_message ? safeText(saved.error_message) : "");
      setStatus(saved.status === "COMPLETE" ? "complete" : saved.status === "ERROR" ? "error" : saved.status === "IN_PROGRESS" ? "running" : "idle");
    } catch {
      setError("No se pudo recuperar la ejecución guardada");
      setStatus("error");
    }
  }, [activeTab, client]);

  const value = useMemo<RunState>(() => ({
    status, progress, result, error, history, start, cancel, loadHistory,
  }), [status, progress, result, error, history, start, cancel, loadHistory]);
  return <RunContext.Provider value={value}>{children}</RunContext.Provider>;
}

export function useRun(): RunState {
  const value = useContext(RunContext);
  if (!value) throw new Error("useRun must be used inside RunProvider");
  return value;
}
