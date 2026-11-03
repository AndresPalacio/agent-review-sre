import { X } from "lucide-react";
import { useTabs } from "../../contexts/TabsContext";

export function TabBar() {
  const { tabs, activeId, activate, closeTab } = useTabs();
  return <div className="tab-bar" role="tablist" aria-label="Flujos abiertos">
    {tabs.map((tab) => <div className={`flow-tab ${activeId === tab.flow.id ? "active" : ""}`} key={tab.flow.id}>
      <button role="tab" aria-selected={activeId === tab.flow.id} onClick={() => activate(tab.flow.id)}>{tab.flow.name}{tab.dirty && <span aria-label="Cambios sin guardar"> ●</span>}</button>
      <button aria-label={`Cerrar ${tab.flow.name}`} onClick={() => closeTab(tab.flow.id)}><X size={12} /></button>
    </div>)}
    {!tabs.length && <span className="tab-empty">Abre o crea un flujo</span>}
  </div>;
}
