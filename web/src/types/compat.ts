export type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  | JsonPrimitive
  | JsonValue[]
  | { [key: string]: JsonValue };

export type ModelProvider =
  | "Alibaba"
  | "Anthropic"
  | "DeepSeek"
  | "Google"
  | "Groq"
  | "Kimi"
  | "Meta"
  | "Mistral"
  | "OpenAI"
  | "Ollama"
  | "OpenRouter"
  | "GigaChat"
  | "Azure OpenAI"
  | "xAI";

export interface Point {
  x: number;
  y: number;
}

export interface CompatibleNode {
  id: string;
  type?: string;
  position?: Point;
  data?: Record<string, JsonValue>;
  [key: string]: unknown;
}

export interface CompatibleEdge {
  id: string;
  source: string;
  target: string;
  type?: string;
  data?: Record<string, JsonValue>;
  [key: string]: unknown;
}

export interface FlowSummary {
  id: number;
  name: string;
  description?: string | null;
  is_template?: boolean;
  tags?: string[] | null;
  created_at?: string;
  updated_at?: string | null;
}

export interface Flow extends FlowSummary {
  nodes: CompatibleNode[];
  edges: CompatibleEdge[];
  viewport?: { x: number; y: number; zoom: number } | null;
  data?: Record<string, JsonValue> | null;
}

export type FlowInput = Omit<Flow, "id" | "created_at" | "updated_at">;
export type FlowUpdate = Partial<FlowInput>;

export type FlowRunStatus = "IDLE" | "IN_PROGRESS" | "COMPLETE" | "ERROR";

export interface FlowRun {
  id: number;
  flow_id: number;
  status: FlowRunStatus;
  run_number: number;
  created_at: string;
  updated_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  request_data?: Record<string, JsonValue> | null;
  results?: Record<string, JsonValue> | null;
  error_message?: string | null;
}

export interface AgentModelConfig {
  agent_id: string;
  model_name?: string;
  model_provider?: ModelProvider;
}

export interface PortfolioPosition {
  ticker: string;
  quantity: number;
  trade_price: number;
}

export interface HedgeFundRequest {
  tickers: string[];
  graph_nodes: CompatibleNode[];
  graph_edges: CompatibleEdge[];
  agent_models?: AgentModelConfig[];
  model_name?: string;
  model_provider?: ModelProvider;
  margin_requirement?: number;
  portfolio_positions?: PortfolioPosition[];
  start_date?: string;
  end_date?: string;
  initial_cash?: number;
  api_keys?: Record<string, string>;
}

export interface BacktestRequest extends Omit<HedgeFundRequest, "initial_cash"> {
  start_date: string;
  end_date: string;
  initial_capital?: number;
}

export interface LanguageModel {
  display_name: string;
  model_name: string;
  provider: ModelProvider;
}

export interface ApiKeySummary {
  id: number;
  provider: string;
  is_active: boolean;
  description?: string | null;
  created_at: string;
  updated_at?: string | null;
  last_used?: string | null;
  has_key: boolean;
}

export interface OllamaStatus {
  installed: boolean;
  running: boolean;
  available_models: string[];
  server_url: string;
  error?: string | null;
}

export type LegacyEvent =
  | { type: "start"; timestamp?: string }
  | {
      type: "progress";
      agent: string;
      ticker?: string;
      status: string;
      timestamp?: string;
      analysis?: string;
    }
  | { type: "error"; message: string; code?: string; timestamp?: string }
  | { type: "complete"; data: Record<string, JsonValue>; timestamp?: string };

export interface AlgorithmSummary {
  key: string;
  name: string;
  description?: string;
  [key: string]: unknown;
}

export interface AlgorithmBacktestJob {
  id?: string;
  run_id?: string;
  status: string;
  progress?: number;
  result?: Record<string, JsonValue> | null;
  error?: string | null;
  [key: string]: unknown;
}
