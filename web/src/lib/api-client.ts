import type {
  AlgorithmBacktestJob,
  AlgorithmSummary,
  ApiKeySummary,
  BacktestRequest,
  Flow,
  FlowInput,
  FlowRun,
  FlowSummary,
  FlowUpdate,
  HedgeFundRequest,
  JsonValue,
  LanguageModel,
  OllamaStatus,
} from "../types/compat";
import { streamEvents, type StreamHandlers } from "./sse";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  nativeApiKey?: string;
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  native?: boolean;
};

const cleanBase = (base: string): string => base.replace(/\/+$/, "");

export class ApiClient {
  private readonly baseUrl: string;
  private nativeApiKey: string;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = cleanBase(options.baseUrl ?? "");
    this.nativeApiKey = options.nativeApiKey ?? "";
  }

  setNativeApiKey(value: string): void {
    this.nativeApiKey = value;
  }

  clearSecrets(): void {
    this.nativeApiKey = "";
  }

  private url(path: string): string {
    return `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
  }

  private async request<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const response = await fetch(this.url(path), {
      method: options.method ?? "GET",
      headers: {
        ...(options.body === undefined
          ? {}
          : { "Content-Type": "application/json" }),
        ...(options.native && this.nativeApiKey
          ? { "X-API-Key": this.nativeApiKey }
          : {}),
      },
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
    if (!response.ok) {
      let detail = `La operación falló (${response.status})`;
      try {
        const value = (await response.json()) as {
          detail?: unknown;
          message?: unknown;
        };
        const candidate = value.detail ?? value.message;
        if (typeof candidate === "string") detail = candidate.slice(0, 500);
      } catch {
        // Deliberately omit response bodies: they can contain provider output.
      }
      throw new ApiError(response.status, detail);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  ping = (signal?: AbortSignal) =>
    this.request<Record<string, JsonValue>>("/ping", { signal });

  flows = {
    list: (signal?: AbortSignal) =>
      this.request<FlowSummary[]>("/flows/", { signal }),
    search: (name: string, signal?: AbortSignal) =>
      this.request<FlowSummary[]>(`/flows/search/${encodeURIComponent(name)}`, {
        signal,
      }),
    get: (id: number, signal?: AbortSignal) =>
      this.request<Flow>(`/flows/${id}`, { signal }),
    create: (body: FlowInput, signal?: AbortSignal) =>
      this.request<Flow>("/flows/", { method: "POST", body, signal }),
    update: (id: number, body: FlowUpdate, signal?: AbortSignal) =>
      this.request<Flow>(`/flows/${id}`, { method: "PUT", body, signal }),
    delete: (id: number, signal?: AbortSignal) =>
      this.request<{ message: string }>(`/flows/${id}`, {
        method: "DELETE",
        signal,
      }),
    duplicate: (id: number, newName?: string, signal?: AbortSignal) =>
      this.request<Flow>(
        `/flows/${id}/duplicate${newName ? `?new_name=${encodeURIComponent(newName)}` : ""}`,
        { method: "POST", signal },
      ),
    runs: {
      list: (flowId: number, signal?: AbortSignal) =>
        this.request<FlowRun[]>(`/flows/${flowId}/runs/`, { signal }),
      get: (flowId: number, runId: number, signal?: AbortSignal) =>
        this.request<FlowRun>(`/flows/${flowId}/runs/${runId}`, { signal }),
      active: (flowId: number, signal?: AbortSignal) =>
        this.request<FlowRun | null>(`/flows/${flowId}/runs/active`, {
          signal,
        }),
      latest: (flowId: number, signal?: AbortSignal) =>
        this.request<FlowRun | null>(`/flows/${flowId}/runs/latest`, {
          signal,
        }),
      create: (
        flowId: number,
        requestData: Record<string, JsonValue>,
        signal?: AbortSignal,
      ) =>
        this.request<FlowRun>(`/flows/${flowId}/runs/`, {
          method: "POST",
          body: { request_data: requestData },
          signal,
        }),
      cancel: (flowId: number, runId: number, signal?: AbortSignal) =>
        this.request<FlowRun>(`/flows/${flowId}/runs/${runId}/cancel`, {
          method: "POST",
          signal,
        }),
      cycles: (flowId: number, runId: number, signal?: AbortSignal) =>
        this.request<Record<string, JsonValue>[]>(
          `/flows/${flowId}/runs/${runId}/cycles`,
          { signal },
        ),
    },
  };

  hedgeFund = {
    agents: (signal?: AbortSignal) =>
      this.request<{ agents: Array<Record<string, JsonValue>> }>(
        "/hedge-fund/agents",
        {
          signal,
        },
      ),
    run: (
      body: HedgeFundRequest,
      handlers: StreamHandlers,
      signal?: AbortSignal,
    ) =>
      streamEvents(
        this.url("/hedge-fund/run"),
        { method: "POST", body },
        handlers,
        signal,
      ),
    backtest: (
      body: BacktestRequest,
      handlers: StreamHandlers,
      signal?: AbortSignal,
    ) =>
      streamEvents(
        this.url("/hedge-fund/backtest"),
        { method: "POST", body },
        handlers,
        signal,
      ),
  };

  models = {
    list: async (signal?: AbortSignal) =>
      (
        await this.request<{ models: LanguageModel[] }>("/language-models/", {
          signal,
        })
      ).models,
    providers: (signal?: AbortSignal) =>
      this.request<{ providers: Array<Record<string, JsonValue>> }>(
        "/language-models/providers",
        { signal },
      ),
  };

  providerKeys = {
    list: (signal?: AbortSignal) =>
      this.request<ApiKeySummary[]>("/api-keys/", { signal }),
    get: (provider: string, signal?: AbortSignal) =>
      this.request<ApiKeySummary>(`/api-keys/${encodeURIComponent(provider)}`, {
        signal,
      }),
    create: (body: Record<string, JsonValue>, signal?: AbortSignal) =>
      this.request<ApiKeySummary>("/api-keys/", {
        method: "POST",
        body,
        signal,
      }),
    update: (
      provider: string,
      body: Record<string, JsonValue>,
      signal?: AbortSignal,
    ) =>
      this.request<ApiKeySummary>(`/api-keys/${encodeURIComponent(provider)}`, {
        method: "PUT",
        body,
        signal,
      }),
    deactivate: (provider: string, signal?: AbortSignal) =>
      this.request<ApiKeySummary>(
        `/api-keys/${encodeURIComponent(provider)}/deactivate`,
        {
          method: "PATCH",
          signal,
        },
      ),
    delete: (provider: string, signal?: AbortSignal) =>
      this.request<void>(`/api-keys/${encodeURIComponent(provider)}`, {
        method: "DELETE",
        signal,
      }),
  };

  ollama = {
    status: (signal?: AbortSignal) =>
      this.request<OllamaStatus>("/ollama/status", { signal }),
    start: (signal?: AbortSignal) =>
      this.request<{ success: boolean; message: string }>("/ollama/start", {
        method: "POST",
        signal,
      }),
    stop: (signal?: AbortSignal) =>
      this.request<{ success: boolean; message: string }>("/ollama/stop", {
        method: "POST",
        signal,
      }),
    download: (modelName: string, signal?: AbortSignal) =>
      this.request<{ success: boolean; message: string }>(
        "/ollama/models/download",
        {
          method: "POST",
          body: { model_name: modelName },
          signal,
        },
      ),
    progress: (modelName: string, signal?: AbortSignal) =>
      this.request<Record<string, JsonValue>>(
        `/ollama/models/download/progress/${encodeURIComponent(modelName)}`,
        { signal },
      ),
    remove: (modelName: string, signal?: AbortSignal) =>
      this.request<{ success: boolean; message: string }>(
        `/ollama/models/${encodeURIComponent(modelName)}`,
        { method: "DELETE", signal },
      ),
  };

  algorithms = {
    list: (signal?: AbortSignal) =>
      this.request<AlgorithmSummary[]>("/v2/algorithms", { signal }),
    get: (key: string, signal?: AbortSignal) =>
      this.request<Record<string, JsonValue>>(
        `/v2/algorithms/${encodeURIComponent(key)}`,
        {
          signal,
        },
      ),
    backtest: (body: Record<string, JsonValue>, signal?: AbortSignal) =>
      this.request<AlgorithmBacktestJob>("/v2/backtests", {
        method: "POST",
        body,
        signal,
      }),
    backtestStatus: (runId: string, signal?: AbortSignal) =>
      this.request<AlgorithmBacktestJob>(
        `/v2/backtests/${encodeURIComponent(runId)}`,
        {
          signal,
        },
      ),
    cancelBacktest: (runId: string, signal?: AbortSignal) =>
      this.request<AlgorithmBacktestJob>(
        `/v2/backtests/${encodeURIComponent(runId)}/cancel`,
        { method: "POST", signal },
      ),
    signals: (runId: string, signal?: AbortSignal) =>
      this.request<Record<string, JsonValue>>(
        `/v2/backtests/${encodeURIComponent(runId)}/signals`,
        { signal },
      ),
    events: (runId: string, handlers: StreamHandlers, signal?: AbortSignal) =>
      streamEvents(
        this.url(`/v2/backtests/${encodeURIComponent(runId)}/events`),
        { method: "GET" },
        handlers,
        signal,
      ),
  };

  native = {
    session: (signal?: AbortSignal) =>
      this.request<{ role: string }>("/v1/session", { native: true, signal }),
    portfolio: (signal?: AbortSignal) =>
      this.request<Record<string, JsonValue>>("/v1/portfolio", {
        native: true,
        signal,
      }),
    orders: (signal?: AbortSignal) =>
      this.request<Array<Record<string, JsonValue>>>("/v1/orders", {
        native: true,
        signal,
      }),
    pendingOrders: (signal?: AbortSignal) =>
      this.request<Array<Record<string, JsonValue>>>("/v1/orders/pending", {
        native: true,
        signal,
      }),
    approveOrder: (
      threadId: string,
      approvalIds: string[],
      decision: "approve" | "reject",
      signal?: AbortSignal,
    ) =>
      this.request<Record<string, JsonValue>>(
        `/v1/orders/${encodeURIComponent(threadId)}/approve`,
        {
          method: "POST",
          body: { decisions: [{ type: decision }], approval_ids: approvalIds },
          native: true,
          signal,
        },
      ),
    explainAnalysis: (threadId: string, signal?: AbortSignal) =>
      this.request<Record<string, JsonValue>>(
        `/v1/analyses/${encodeURIComponent(threadId)}/explain`,
        { native: true, signal },
      ),
  };
}

export const apiClient = new ApiClient({
  baseUrl: import.meta.env.VITE_API_URL as string | undefined,
});
