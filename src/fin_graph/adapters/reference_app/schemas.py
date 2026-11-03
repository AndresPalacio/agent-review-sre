"""Frozen public HTTP and SSE contracts from the reference AI Hedge Fund app."""

from __future__ import annotations

import math
import os
import re
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelProvider(StrEnum):
    ALIBABA = "Alibaba"
    ANTHROPIC = "Anthropic"
    DEEPSEEK = "DeepSeek"
    GOOGLE = "Google"
    GROQ = "Groq"
    KIMI = "Kimi"
    META = "Meta"
    MISTRAL = "Mistral"
    OPENAI = "OpenAI"
    OLLAMA = "Ollama"
    OPENROUTER = "OpenRouter"
    GIGACHAT = "GigaChat"
    AZURE_OPENAI = "Azure OpenAI"
    XAI = "xAI"


def _normalize_provider(value: object) -> ModelProvider | None:
    if isinstance(value, ModelProvider):
        return value
    if not isinstance(value, str):
        return None
    token = re.sub(r"[\s_-]+", "", value).casefold()
    return next(
        (
            provider
            for provider in ModelProvider
            if token
            in {
                re.sub(r"[\s_-]+", "", provider.name).casefold(),
                re.sub(r"[\s_-]+", "", provider.value).casefold(),
            }
        ),
        None,
    )


def _default_model_name() -> str:
    return os.getenv("DEFAULT_MODEL_NAME") or "gpt-4.1"


def _default_model_provider() -> ModelProvider:
    return _normalize_provider(os.getenv("DEFAULT_MODEL_PROVIDER") or "") or ModelProvider.OPENAI


class FlowRunStatus(StrEnum):
    IDLE = "IDLE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"


class AgentModelConfig(BaseModel):
    agent_id: str
    model_name: str | None = None
    model_provider: ModelProvider | None = None


class PortfolioPosition(BaseModel):
    ticker: str
    quantity: float
    trade_price: float

    @field_validator("trade_price")
    @classmethod
    def price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Trade price must be positive!")
        return value


class GraphNode(BaseModel):
    id: str
    type: str | None = None
    data: dict[str, Any] | None = None
    position: dict[str, Any] | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str | None = None
    data: dict[str, Any] | None = None


_SUFFIX = re.compile(r"^[a-z0-9]{6}$")


def extract_base_agent_key(unique_id: str) -> str:
    """Reference-compatible removal of React Flow's six-character node suffix."""

    known = {
        "aswath_damodaran",
        "ben_graham",
        "bill_ackman",
        "cathie_wood",
        "charlie_munger",
        "michael_burry",
        "mohnish_pabrai",
        "nassim_taleb",
        "peter_lynch",
        "phil_fisher",
        "rakesh_jhunjhunwala",
        "stanley_druckenmiller",
        "warren_buffett",
        "technical_analyst",
        "fundamentals_analyst",
        "growth_analyst",
        "news_sentiment_analyst",
        "sentiment_analyst",
        "valuation_analyst",
        "portfolio_manager",
        "risk_management_agent",
    }
    if unique_id in known:
        return unique_id
    head, separator, suffix = unique_id.rpartition("_")
    return head if separator and _SUFFIX.fullmatch(suffix) else unique_id


class HedgeFundResponse(BaseModel):
    decisions: dict[str, Any]
    analyst_signals: dict[str, Any]


class ErrorResponse(BaseModel):
    message: str
    error: str | None = None


class BaseHedgeFundRequest(BaseModel):
    tickers: list[str]
    graph_nodes: list[GraphNode]
    graph_edges: list[GraphEdge]
    agent_models: list[AgentModelConfig] | None = None
    model_name: str | None = Field(default_factory=_default_model_name)
    model_provider: ModelProvider | None = Field(default_factory=_default_model_provider)
    margin_requirement: float = 0.0
    portfolio_positions: list[PortfolioPosition] | None = None
    api_keys: dict[str, str] | None = None

    def get_agent_ids(self) -> list[str]:
        return [node.id for node in self.graph_nodes]

    def get_agent_model_config(self, agent_id: str) -> tuple[str | None, ModelProvider | None]:
        if self.agent_models:
            base_agent_key = extract_base_agent_key(agent_id)
            for config in self.agent_models:
                if (
                    config.agent_id == agent_id
                    or extract_base_agent_key(config.agent_id) == base_agent_key
                ):
                    return (
                        config.model_name or self.model_name,
                        config.model_provider or self.model_provider,
                    )
        return self.model_name, self.model_provider


class BacktestRequest(BaseHedgeFundRequest):
    start_date: str
    end_date: str
    initial_capital: float = 100_000.0


class HedgeFundRequest(BaseHedgeFundRequest):
    end_date: str | None = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    start_date: str | None = None
    initial_cash: float = 100_000.0

    def get_start_date(self) -> str:
        if self.start_date:
            return self.start_date
        if self.end_date is None:
            raise ValueError("end_date cannot be null when start_date is omitted")
        return (datetime.strptime(self.end_date, "%Y-%m-%d") - timedelta(days=90)).strftime(
            "%Y-%m-%d"
        )


class BacktestDayResult(BaseModel):
    date: str
    portfolio_value: float
    cash: float
    decisions: dict[str, Any]
    executed_trades: dict[str, int]
    analyst_signals: dict[str, Any]
    current_prices: dict[str, float]
    long_exposure: float
    short_exposure: float
    gross_exposure: float
    net_exposure: float
    long_short_ratio: float | None = None


class BacktestPerformanceMetrics(BaseModel):
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown: float | None = None
    max_drawdown_date: str | None = None
    long_short_ratio: float | None = None
    gross_exposure: float | None = None
    net_exposure: float | None = None


class BacktestResponse(BaseModel):
    results: list[BacktestDayResult]
    performance_metrics: BacktestPerformanceMetrics
    final_portfolio: dict[str, Any]


class FlowCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    viewport: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    is_template: bool = False
    tags: list[str] | None = None


class FlowUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None
    viewport: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    is_template: bool | None = None
    tags: list[str] | None = None


class FlowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    viewport: dict[str, Any] | None
    data: dict[str, Any] | None
    is_template: bool
    tags: list[str] | None
    created_at: datetime
    updated_at: datetime | None


class FlowSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_template: bool
    tags: list[str] | None
    created_at: datetime
    updated_at: datetime | None


class FlowRunCreateRequest(BaseModel):
    request_data: dict[str, Any] | None = None


class FlowRunUpdateRequest(BaseModel):
    status: FlowRunStatus | None = None
    results: dict[str, Any] | None = None
    error_message: str | None = None


class FlowRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    flow_id: int
    status: FlowRunStatus
    run_number: int
    created_at: datetime
    updated_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    request_data: dict[str, Any] | None
    results: dict[str, Any] | None
    error_message: str | None


class FlowRunSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    flow_id: int
    status: FlowRunStatus
    run_number: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None


class ApiKeyCreateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    key_value: str = Field(min_length=1)
    description: str | None = None
    is_active: bool = True


class ApiKeyUpdateRequest(BaseModel):
    key_value: str | None = Field(default=None, min_length=1)
    description: str | None = None
    is_active: bool | None = None


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    key_value: str
    is_active: bool
    description: str | None
    created_at: datetime
    updated_at: datetime | None
    last_used: datetime | None


class ApiKeySummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    is_active: bool
    description: str | None
    created_at: datetime
    updated_at: datetime | None
    last_used: datetime | None
    has_key: bool = True


class ApiKeyBulkUpdateRequest(BaseModel):
    api_keys: list[ApiKeyCreateRequest]


class BaseEvent(BaseModel):
    type: str

    def to_sse(self) -> str:
        return f"event: {self.type.lower()}\ndata: {self.model_dump_json()}\n\n"


class StartEvent(BaseEvent):
    type: Literal["start"] = "start"
    timestamp: str | None = None


class ProgressUpdateEvent(BaseEvent):
    type: Literal["progress"] = "progress"
    agent: str
    ticker: str | None = None
    status: str
    timestamp: str | None = None
    analysis: str | None = None


class ErrorEvent(BaseEvent):
    type: Literal["error"] = "error"
    message: str
    code: str | None = None
    timestamp: str | None = None


class CompleteEvent(BaseEvent):
    type: Literal["complete"] = "complete"
    data: dict[str, Any]
    timestamp: str | None = None


class ModelRequest(BaseModel):
    model_name: str


class OllamaStatusResponse(BaseModel):
    installed: bool
    running: bool
    available_models: list[str]
    server_url: str
    error: str | None = None


class ActionResponse(BaseModel):
    success: bool
    message: str


class RecommendedModel(BaseModel):
    display_name: str
    model_name: str
    provider: str


class ProgressResponse(BaseModel):
    status: str
    percentage: float | None = None
    message: str | None = None
    phase: str | None = None
    bytes_downloaded: int | None = None
    total_bytes: int | None = None


class SaveJsonRequest(BaseModel):
    filename: str
    data: dict[str, Any]


class LanguageModelResponse(BaseModel):
    display_name: str
    model_name: str
    provider: ModelProvider


class LanguageModelsResponse(BaseModel):
    models: list[LanguageModelResponse]


class LanguageModelProviderResponse(BaseModel):
    name: str
    models: list[dict[str, str]]


class LanguageModelProvidersResponse(BaseModel):
    providers: list[LanguageModelProviderResponse]


class SkillSummaryResponse(BaseModel):
    skill_id: str
    name: str
    description: str
    category: str
    resources: tuple[str, ...]


class SkillDocumentResponse(SkillSummaryResponse):
    body: str


class SkillResourceResponse(BaseModel):
    skill_id: str
    resource_path: str
    content: str


class SignalResponse(BaseModel):
    ticker: str
    date: str
    value: float
    reasoning: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrainRequest(BaseModel):
    tickers: list[str] = Field(min_length=1)
    date: str | None = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))


class TrainResult(BaseModel):
    ticker: str
    success: bool
    error: str | None = None
    trained_through: str | None = None
    accuracy: float | None = None
    precision: float | None = None
    rmse: float | None = None
    n_train: int | None = None
    n_test: int | None = None
    top_features: list[dict[str, Any]] | None = None


class TrainResponse(BaseModel):
    results: list[TrainResult]


class VaRResponse(BaseModel):
    var: float
    cvar: float
    confidence: float
    horizon_days: int
    n_sims: int
    portfolio_value: float
    expected_pnl: float
    portfolio_sigma: float
    var_pct: float
    holdings: dict[str, float]
    date: str


ParameterType = Literal["integer", "number", "boolean", "enum"]
ParameterValue = int | float | bool | str


def _valid_parameter_value(kind: ParameterType, value: object) -> bool:
    if kind == "integer":
        return type(value) is int
    if kind == "number":
        return (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if kind == "boolean":
        return type(value) is bool
    return isinstance(value, str)


class AlgorithmParameter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: ParameterType
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    default: ParameterValue
    minimum: float | None = None
    maximum: float | None = None
    choices: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def coherent_definition(self) -> AlgorithmParameter:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        if not _valid_parameter_value(self.type, self.default):
            raise ValueError(f"default does not match parameter type {self.type}")
        if self.type == "enum":
            if not self.choices or self.default not in self.choices:
                raise ValueError("enum default must be one of choices")
        elif self.choices:
            raise ValueError("choices are only valid for enum parameters")
        if self.type not in {"integer", "number"} and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("bounds are only valid for numeric parameters")
        if self.type in {"integer", "number"}:
            numeric_default = float(self.default)
            if self.minimum is not None and numeric_default < self.minimum:
                raise ValueError("default is below minimum")
            if self.maximum is not None and numeric_default > self.maximum:
                raise ValueError("default is above maximum")
        return self


class AlgorithmSpecification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1_000)
    category: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    parameters: dict[str, AlgorithmParameter] = Field(default_factory=dict, max_length=32)
    supported_intervals: list[str] = Field(default_factory=lambda: ["1D"])
    supported_providers: list[str] = Field(default_factory=lambda: ["yahoo"])
    warnings: list[str] = Field(default_factory=list)


class AlgorithmSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    description: str
    category: str
    version: str
    parameter_count: int = Field(ge=0)


AlgorithmBacktestStatus = Literal["queued", "running", "complete", "failed", "cancelled"]
AlgorithmBacktestErrorCode = Literal[
    "algorithm-not-found",
    "algorithm-definition-invalid",
    "parameter-invalid",
    "data-invalid",
    "data-unavailable",
    "worker-failed",
    "timeout",
    "cancelled",
    "result-unavailable",
]
_PARAMETER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class AlgorithmBacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=32)
    symbols: list[str] = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    interval: str = "1D"
    provider: str = "yahoo"
    initial_capital: float = Field(default=100_000.0, gt=0)
    commission_rate: float = Field(default=0.001, ge=0, le=0.1)
    slippage_bps: float = Field(default=5.0, ge=0, le=1_000)
    seed: int = 42

    @field_validator("parameters", mode="before")
    @classmethod
    def validate_parameters(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        clean: dict[str, ParameterValue] = {}
        for key, parameter in value.items():
            if not isinstance(key, str) or not _PARAMETER_NAME.fullmatch(key):
                raise ValueError("invalid algorithm parameter name")
            valid_scalar = isinstance(parameter, int | str) or (
                isinstance(parameter, float) and math.isfinite(parameter)
            )
            if valid_scalar:
                clean[key] = parameter
            else:
                raise ValueError(f"parameter {key!r} must be a finite scalar")
        return clean

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            symbol = str(item).strip().upper()
            if symbol and symbol not in seen:
                normalized.append(symbol)
                seen.add(symbol)
        return normalized

    @model_validator(mode="after")
    def validate_range(self) -> AlgorithmBacktestConfig:
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class AlgorithmTrade(BaseModel):
    symbol: str
    side: Literal["long", "short"]
    entry_date: str
    entry_price: float
    quantity: float
    exit_date: str | None = None
    exit_price: float | None = None
    pnl: float = 0.0
    return_pct: float = 0.0
    commission: float = 0.0
    reason: str = "rebalance"


class AlgorithmEquityPoint(BaseModel):
    date: str
    equity: float
    cash: float
    long_exposure: float = 0.0
    short_exposure: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    weights: dict[str, float] = Field(default_factory=dict)


class AlgorithmBacktestMetrics(BaseModel):
    total_return: float = 0.0
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown: float = 0.0
    win_rate: float | None = None
    trade_count: int = 0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0


class AlgorithmSignalPoint(BaseModel):
    date: str
    symbol: str
    raw_signal: float
    applied_weight: float


class AlgorithmSignalSummary(BaseModel):
    symbol: str
    first_date: str
    last_date: str
    latest_raw_signal: float
    latest_applied_weight: float
    long_count: int = Field(ge=0)
    short_count: int = Field(ge=0)
    flat_count: int = Field(ge=0)


class AlgorithmDataCoverage(BaseModel):
    first_date: str
    last_date: str
    row_count: int = Field(ge=0)


class AlgorithmSignalPage(BaseModel):
    run_id: str
    items: list[AlgorithmSignalPoint]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=2_000)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class AlgorithmBacktestResult(BaseModel):
    run_id: str
    algorithm_key: str
    algorithm_version: str
    algorithm_hash: str
    resolved_parameters: dict[str, Any] = Field(default_factory=dict)
    status: AlgorithmBacktestStatus
    metrics: AlgorithmBacktestMetrics | None = None
    equity_curve: list[AlgorithmEquityPoint] = Field(default_factory=list)
    trades: list[AlgorithmTrade] = Field(default_factory=list)
    signal_summary: list[AlgorithmSignalSummary] = Field(default_factory=list)
    data_coverage: dict[str, AlgorithmDataCoverage] = Field(default_factory=dict)
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error_code: AlgorithmBacktestErrorCode | None = None
    error: str | None = None


class AlgorithmBacktestEvent(BaseModel):
    sequence: int
    type: Literal["queued", "running", "progress", "complete", "error", "cancelled"]
    status: AlgorithmBacktestStatus
    message: str
    progress: float | None = None


class AlgorithmBacktestJob(BaseModel):
    run_id: str
    status: AlgorithmBacktestStatus
    config: AlgorithmBacktestConfig
    algorithm_version: str
    algorithm_hash: str
    resolved_parameters: dict[str, ParameterValue]
    created_at: datetime
    updated_at: datetime
    progress: float = 0.0
    error_code: AlgorithmBacktestErrorCode | None = None
    error: str | None = None
    result: AlgorithmBacktestResult | None = None
