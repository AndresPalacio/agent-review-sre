import { Ban, Play } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { ApiClient, apiClient } from "../../lib/api-client";
import type {
  AlgorithmBacktestJob,
  AlgorithmSummary,
  BacktestRequest,
  JsonValue,
} from "../../types/compat";
import { useTabs } from "../../contexts/TabsContext";
import { BacktestOutput } from "./BacktestOutput";

export interface BacktestFormValue {
  algorithm_key: string;
  symbols: string[];
  start_date: string;
  end_date: string;
  initial_capital: number;
  parameters: Record<string, JsonValue>;
  interval: string;
  provider: string;
}

interface BacktestFormProps {
  algorithms: AlgorithmSummary[];
  onStart: (value: BacktestFormValue) => void;
  onCancel: () => void;
  running: boolean;
}

export function BacktestForm({
  algorithms,
  onStart,
  onCancel,
  running,
}: BacktestFormProps) {
  const [algorithm, setAlgorithm] = useState(algorithms[0]?.key ?? "");
  const [symbols, setSymbols] = useState("AAPL");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [capital, setCapital] = useState("100000");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!algorithm && algorithms[0]) setAlgorithm(algorithms[0].key);
  }, [algorithm, algorithms]);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!start || !end) {
      setError("Selecciona el intervalo del backtest");
      return;
    }
    if (start > end) {
      setError("La fecha inicial debe ser anterior o igual a la fecha final");
      return;
    }
    const tickers = symbols
      .split(",")
      .map((value) => value.trim().toUpperCase())
      .filter(Boolean);
    const amount = Number(capital);
    if (
      !algorithm ||
      !tickers.length ||
      !Number.isFinite(amount) ||
      amount <= 0
    ) {
      setError("Completa símbolos, algoritmo y capital");
      return;
    }
    setError("");
    onStart({
      algorithm_key: algorithm,
      symbols: tickers,
      start_date: start,
      end_date: end,
      initial_capital: amount,
      parameters: {},
      interval: "1D",
      provider: "yahoo",
    });
  }
  return (
    <form className="backtest-form" onSubmit={submit}>
      <div>
        <label>
          Algoritmo
          <select
            aria-label="Algoritmo"
            value={algorithm}
            onChange={(event) => setAlgorithm(event.target.value)}
          >
            {algorithms.map((item) => (
              <option value={item.key} key={item.key}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Símbolos
          <input
            aria-label="Símbolos del backtest"
            value={symbols}
            onChange={(event) => setSymbols(event.target.value)}
          />
        </label>
      </div>
      <div>
        <label>
          Inicio
          <input
            aria-label="Inicio del backtest"
            type="date"
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </label>
        <label>
          Fin
          <input
            aria-label="Fin del backtest"
            type="date"
            value={end}
            onChange={(event) => setEnd(event.target.value)}
          />
        </label>
        <label>
          Capital
          <input
            aria-label="Capital del backtest"
            type="number"
            value={capital}
            onChange={(event) => setCapital(event.target.value)}
          />
        </label>
      </div>
      {error && (
        <p role="alert" className="settings-error">
          {error}
        </p>
      )}
      <div className="backtest-actions">
        <button
          type="submit"
          aria-label="Ejecutar backtest"
          disabled={!algorithms.length}
        >
          <Play size={14} /> Ejecutar backtest
        </button>
        {running && (
          <button
            type="button"
            aria-label="Cancelar backtest"
            className="danger-button"
            onClick={onCancel}
          >
            <Ban size={14} /> Cancelar backtest
          </button>
        )}
      </div>
    </form>
  );
}

export function BacktestWorkspace({
  client = apiClient,
}: {
  client?: ApiClient;
}) {
  const { activeTab } = useTabs();
  const [algorithms, setAlgorithms] = useState<AlgorithmSummary[]>([]);
  const [job, setJob] = useState<AlgorithmBacktestJob | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [mode, setMode] = useState<"algorithm" | "flow">("algorithm");
  const [controller, setController] = useState<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    client.algorithms
      .list(abort.signal)
      .then(setAlgorithms)
      .catch(() => setError("No se pudo cargar el catálogo de algoritmos"));
    return () => abort.abort();
  }, [client]);
  async function start(value: BacktestFormValue) {
    const abort = new AbortController();
    controller?.abort();
    setController(abort);
    setRunning(true);
    setError("");
    setJob(null);
    try {
      if (mode === "flow") {
        if (!activeTab)
          throw new Error(
            "Abre un flujo para ejecutar el backtest multiagente",
          );
        const request: BacktestRequest = {
          tickers: value.symbols,
          start_date: value.start_date,
          end_date: value.end_date,
          initial_capital: value.initial_capital,
          graph_nodes: activeTab.document.nodes as never,
          graph_edges: activeTab.document.edges as never,
        };
        await client.hedgeFund.backtest(
          request,
          {
            onEvent: (event) => {
              const payload =
                typeof event.data === "object" && event.data !== null
                  ? (event.data as Record<string, unknown>)
                  : {};
              if (event.event === "progress")
                setJob({
                  run_id: "legacy",
                  status: "running",
                  progress: 50,
                  result: { message: String(payload.status ?? "Procesando") },
                });
              if (event.event === "complete")
                setJob({
                  run_id: "legacy",
                  status: "complete",
                  progress: 100,
                  result: (payload.data ?? {}) as Record<string, JsonValue>,
                });
              if (event.event === "error")
                setError(String(payload.message ?? "El backtest falló"));
            },
          },
          abort.signal,
        );
      } else {
        const created = await client.algorithms.backtest(
          value as unknown as Record<string, JsonValue>,
          abort.signal,
        );
        setJob(created);
        const runId = created.run_id ?? created.id;
        if (!runId)
          throw new Error(
            "El servidor no devolvió el identificador del backtest",
          );
        await client.algorithms.events(
          runId,
          {
            onEvent: (event) => {
              const payload =
                typeof event.data === "object" && event.data !== null
                  ? (event.data as Record<string, unknown>)
                  : {};
              setJob((current) =>
                current
                  ? {
                      ...current,
                      status: String(payload.status ?? current.status),
                      progress:
                        typeof payload.progress === "number"
                          ? payload.progress
                          : current.progress,
                    }
                  : current,
              );
            },
            onTerminal: () => {
              void client.algorithms.backtestStatus(runId).then(setJob);
            },
          },
          abort.signal,
        );
        setJob(await client.algorithms.backtestStatus(runId, abort.signal));
      }
    } catch (reason) {
      if (!abort.signal.aborted)
        setError(
          reason instanceof Error ? reason.message : "El backtest falló",
        );
    } finally {
      if (!abort.signal.aborted) setRunning(false);
    }
  }
  async function cancel() {
    controller?.abort();
    const runId = job?.run_id ?? job?.id;
    if (mode === "algorithm" && runId) {
      try {
        let latest = await client.algorithms.cancelBacktest(runId);
        setJob(latest);
        for (
          let attempt = 0;
          attempt < 40 &&
          !["cancelled", "complete", "failed"].includes(latest.status);
          attempt += 1
        ) {
          await new Promise((resolve) => window.setTimeout(resolve, 50));
          latest = await client.algorithms.backtestStatus(runId);
          setJob(latest);
        }
      } catch {
        setError("No se pudo cancelar el backtest");
      }
    }
    setRunning(false);
  }
  return (
    <main className="product-page">
      <header>
        <div>
          <span>LABORATORIO CUANTITATIVO</span>
          <h1>Backtests</h1>
          <p>
            Evalúa algoritmos deterministas o el flujo multiagente con datos
            históricos.
          </p>
        </div>
        <label>
          Motor
          <select
            aria-label="Motor de backtest"
            value={mode}
            onChange={(event) =>
              setMode(event.target.value as "algorithm" | "flow")
            }
          >
            <option value="algorithm">Algoritmos v2</option>
            <option value="flow">Flujo multiagente</option>
          </select>
        </label>
      </header>
      <div className="backtest-layout">
        <BacktestForm
          algorithms={algorithms}
          onStart={(value) => void start(value)}
          onCancel={() => void cancel()}
          running={running}
        />
        <section className="backtest-result">
          {error && (
            <p role="alert" className="settings-error">
              {error}
            </p>
          )}
          <BacktestOutput job={job} />
        </section>
      </div>
    </main>
  );
}
