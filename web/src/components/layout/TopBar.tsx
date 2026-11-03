import { BarChart3, Moon, Play, Settings, Sun } from "lucide-react";
import { useLayout } from "../../contexts/LayoutContext";

export function TopBar({ onRun }: { onRun?: () => void }) {
  const { theme, setTheme } = useLayout();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <header className="topbar">
      <div className="wordmark">
        <span className="wordmark-icon"><BarChart3 size={18} /></span>
        <strong>Fin Graph</strong>
        <span>AI Hedge Fund</span>
      </div>
      <div className="topbar-actions">
        <span className="connection-dot" aria-label="Backend conectado" />
        <button className="icon-button" aria-label={`Cambiar a tema ${next === "light" ? "claro" : "oscuro"}`} onClick={() => setTheme(next)}>
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </button>
        <button className="icon-button" aria-label="Configuración"><Settings size={17} /></button>
        <button className="run-button" onClick={onRun}><Play size={15} fill="currentColor" /> Ejecutar</button>
      </div>
    </header>
  );
}
