import {
  BarChart3,
  FlaskConical,
  GitBranch,
  Landmark,
  Moon,
  Play,
  Settings,
  Sun,
} from "lucide-react";
import { useLayout } from "../../contexts/LayoutContext";

export type ProductPage = "flows" | "backtests" | "paper";

interface TopBarProps {
  page?: ProductPage;
  onPageChange?: (page: ProductPage) => void;
  onRun?: () => void;
  onSettings?: () => void;
}

export function TopBar({
  page = "flows",
  onPageChange,
  onRun,
  onSettings,
}: TopBarProps) {
  const { theme, setTheme } = useLayout();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <header className="topbar">
      <div className="wordmark">
        <span className="wordmark-icon">
          <BarChart3 size={18} />
        </span>
        <strong>Fin Graph</strong>
        <span>AI Hedge Fund</span>
      </div>
      <nav className="product-navigation" aria-label="Secciones del producto">
        <button
          className={page === "flows" ? "active" : ""}
          aria-current={page === "flows" ? "page" : undefined}
          onClick={() => onPageChange?.("flows")}
        >
          <GitBranch size={14} /> Flujos
        </button>
        <button
          className={page === "backtests" ? "active" : ""}
          aria-current={page === "backtests" ? "page" : undefined}
          onClick={() => onPageChange?.("backtests")}
        >
          <FlaskConical size={14} /> Backtests
        </button>
        <button
          className={page === "paper" ? "active" : ""}
          aria-current={page === "paper" ? "page" : undefined}
          onClick={() => onPageChange?.("paper")}
        >
          <Landmark size={14} /> Paper trading
        </button>
      </nav>
      <div className="topbar-actions">
        <span className="connection-dot" aria-label="Backend conectado" />
        <button
          className="icon-button"
          aria-label={`Cambiar a tema ${next === "light" ? "claro" : "oscuro"}`}
          onClick={() => setTheme(next)}
        >
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </button>
        <button
          className="icon-button"
          aria-label="Configuración"
          onClick={onSettings}
        >
          <Settings size={17} />
        </button>
        {page === "flows" && (
          <button className="run-button" onClick={onRun}>
            <Play size={15} fill="currentColor" /> Ejecutar
          </button>
        )}
      </div>
    </header>
  );
}
