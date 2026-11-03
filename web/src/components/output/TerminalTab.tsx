import type { ProgressRecord, RunStatus } from "../../contexts/RunContext";

export function TerminalTab({ progress, status }: { progress: ProgressRecord[]; status: RunStatus }) {
  return <div className="terminal-output" aria-label="Terminal segura">
    <p><span>$</span> estado: {status}</p>
    {progress.map((item, index) => <p key={`${item.id}-${index}`}><span>›</span> {item.agent} {item.ticker ?? ""} — {item.status}</p>)}
  </div>;
}

