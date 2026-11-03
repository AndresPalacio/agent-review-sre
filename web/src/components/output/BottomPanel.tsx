import { Ban, Play, RotateCcw } from "lucide-react";
import { useState } from "react";
import { useRun } from "../../contexts/RunContext";
import { useTabs } from "../../contexts/TabsContext";
import { InvestmentReportDialog } from "./InvestmentReportDialog";
import { JsonOutputDialog } from "./JsonOutputDialog";
import { OutputTab } from "./OutputTab";
import { ProblemsTab } from "./ProblemsTab";
import { ReasoningTab } from "./ReasoningTab";
import { TerminalTab } from "./TerminalTab";

type OutputView = "output" | "reasoning" | "problems" | "terminal";
const statusLabel = {
  idle: "Listo",
  running: "En ejecución",
  complete: "Completado",
  error: "Error",
  cancelled: "Cancelado",
} as const;

export function BottomPanel() {
  const { activeTab } = useTabs();
  const run = useRun();
  const [view, setView] = useState<OutputView>("output");
  const [tickers, setTickers] = useState("AAPL");
  const [cash, setCash] = useState("100000");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [report, setReport] = useState(false);
  const [json, setJson] = useState(false);
  const symbols = tickers.split(",").map((value) => value.trim().toUpperCase()).filter(Boolean);
  const amount = Number(cash);
  const valid = symbols.length > 0 && Number.isFinite(amount) && amount > 0;
  return <div className="bottom-workbench">
    <div className="result-tabs">
      <button className={view === "output" ? "active" : ""} onClick={() => setView("output")}>Salida</button>
      <button className={view === "reasoning" ? "active" : ""} onClick={() => setView("reasoning")}>Razonamiento <span>{run.progress.length}</span></button>
      <button className={view === "problems" ? "active" : ""} onClick={() => setView("problems")}>Problemas <span>{run.error ? 1 : 0}</span></button>
      <button className={view === "terminal" ? "active" : ""} onClick={() => setView("terminal")}>Terminal</button>
      <strong className={`run-state ${run.status}`}>{statusLabel[run.status]}</strong>
    </div>
    {!activeTab ? <div className="output-empty"><p>Abre un flujo para comenzar</p><span>La configuración de ejecución aparecerá aquí.</span></div> : <div className="bottom-content">
      <form className="run-config" onSubmit={(event) => { event.preventDefault(); if (valid) void run.start({ tickers: symbols, initialCash: amount, ...(startDate ? { startDate } : {}), ...(endDate ? { endDate } : {}) }); }}>
        <p>Configura y ejecuta el flujo activo</p>
        <label>Símbolos<input aria-label="Símbolos" value={tickers} onChange={(event) => setTickers(event.target.value)} placeholder="AAPL, MSFT" /></label>
        <label>Capital inicial<input aria-label="Capital inicial" type="number" min="1" value={cash} onChange={(event) => setCash(event.target.value)} /></label>
        <label>Desde<input aria-label="Fecha inicial" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
        <label>Hasta<input aria-label="Fecha final" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
        {run.status === "running" ? <button type="button" className="cancel-run" aria-label="Cancelar ejecución" onClick={run.cancel}><Ban size={13} /> Cancelar</button> : <button type="submit" disabled={!valid} aria-label="Ejecutar análisis"><Play size={13} fill="currentColor" /> Ejecutar</button>}
        {run.status === "error" && <button type="submit" aria-label="Reintentar ejecución"><RotateCcw size={13} /> Reintentar</button>}
        {run.history.length > 0 && <label>Historial<select aria-label="Ejecución guardada" defaultValue="" onChange={(event) => { if (event.target.value) void run.loadHistory(Number(event.target.value)); }}><option value="" disabled>Seleccionar</option>{run.history.map((item) => <option value={item.id} key={item.id}>#{item.run_number} · {item.status}</option>)}</select></label>}
      </form>
      <div className="output-view">
        {view === "output" && <OutputTab result={run.result} onReport={() => setReport(true)} onJson={() => setJson(true)} />}
        {view === "reasoning" && <ReasoningTab progress={run.progress} />}
        {view === "problems" && <ProblemsTab error={run.error} />}
        {view === "terminal" && <TerminalTab progress={run.progress} status={run.status} />}
        {run.error && view !== "problems" && <div className="run-problem" role="alert">{run.error}</div>}
      </div>
    </div>}
    <InvestmentReportDialog open={report} result={run.result} onClose={() => setReport(false)} />
    <JsonOutputDialog open={json} result={run.result} onClose={() => setJson(false)} />
  </div>;
}
