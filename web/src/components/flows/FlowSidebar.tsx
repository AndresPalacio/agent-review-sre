import { FilePlus2, RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useFlows } from "../../contexts/FlowContext";
import { FlowDialog } from "./FlowDialog";
import { FlowList } from "./FlowList";

export function FlowSidebar() {
  const { flows, loading, error, refresh, createFlow } = useFlows();
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const filtered = useMemo(() => {
    const token = query.trim().toLocaleLowerCase();
    return token ? flows.filter((flow) => `${flow.name} ${flow.description ?? ""}`.toLocaleLowerCase().includes(token)) : flows;
  }, [flows, query]);
  return <>
    <div className="panel-heading"><strong>Flujos</strong><span className="panel-actions"><button className="icon-button" aria-label="Actualizar flujos" onClick={() => void refresh()}><RefreshCw size={14} /></button><button className="icon-button" aria-label="Crear flujo" onClick={() => setCreating(true)}><FilePlus2 size={15} /></button></span></div>
    <label className="search-field"><Search size={14} /><span className="sr-only">Buscar flujos</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar flujos" /></label>
    {error && <div className="sidebar-error" role="alert">{error}</div>}
    {loading && !flows.length ? <p className="sidebar-state" role="status">Cargando flujos…</p> : <FlowList values={filtered} />}
    <FlowDialog open={creating} title="Nuevo flujo" submitLabel="Crear" onClose={() => setCreating(false)} onSubmit={async (name, description) => { await createFlow(name, description); }} />
  </>;
}

