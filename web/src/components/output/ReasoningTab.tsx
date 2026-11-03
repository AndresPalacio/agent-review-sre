import type { ProgressRecord } from "../../contexts/RunContext";

const displayAgent = (value: string) => value.replace(/_agent$/, "").replace(/_[a-z0-9]{6}$/i, "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

export function ReasoningTab({ progress }: { progress: ProgressRecord[] }) {
  if (!progress.length) return <p className="output-empty">Todavía no hay razonamiento disponible.</p>;
  return <ol className="reasoning-list">{progress.map((item, index) => <li key={`${item.id}-${index}`}>
    <span className="reasoning-index">{index + 1}</span><div><strong>{displayAgent(item.agent)}{item.ticker ? ` · ${item.ticker}` : ""}</strong><small>{item.status}</small>{item.analysis && <p>{item.analysis}</p>}</div>
  </li>)}</ol>;
}

