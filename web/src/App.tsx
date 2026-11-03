import { Component, type ErrorInfo, type ReactNode } from "react";
import { Blocks, ChevronDown, FilePlus2, LineChart, Search } from "lucide-react";
import { LayoutProvider } from "./contexts/LayoutContext";
import { TopBar } from "./components/layout/TopBar";
import { WorkbenchLayout } from "./components/layout/WorkbenchLayout";
import "./style.css";

interface BoundaryProps { children: ReactNode }
interface BoundaryState { failed: boolean }

export class AppErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false };
  static getDerivedStateFromError(): BoundaryState { return { failed: true }; }
  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Rendering errors stay local. Details may contain provider output.
  }
  render() {
    if (this.state.failed) {
      return (
        <main className="fatal-error" role="alert">
          <LineChart size={36} />
          <h1>No pudimos mostrar el espacio de trabajo</h1>
          <p>Recarga la aplicación para volver a un estado seguro.</p>
          <button onClick={() => window.location.reload()}>Recargar</button>
        </main>
      );
    }
    return this.props.children;
  }
}

function FlowPlaceholder() {
  return <>
    <div className="panel-heading"><strong>Flujos</strong><button className="icon-button" aria-label="Crear flujo"><FilePlus2 size={16} /></button></div>
    <label className="search-field"><Search size={14} /><span className="sr-only">Buscar flujos</span><input placeholder="Buscar flujos" /></label>
    <div className="empty-panel"><FilePlus2 size={25} /><p>Aún no hay flujos</p><span>Crea uno para diseñar tu estrategia.</span></div>
  </>;
}

function CanvasPlaceholder() {
  return <div className="canvas-empty">
    <div className="canvas-grid" />
    <div className="empty-card"><LineChart size={34} /><h1>Construye tu estrategia</h1><p>Arrastra analistas y componentes al lienzo para crear un flujo de inversión.</p><button><FilePlus2 size={16} /> Crear primer flujo</button></div>
  </div>;
}

function CatalogPlaceholder() {
  return <>
    <div className="panel-heading"><strong>Componentes</strong></div>
    <label className="search-field"><Search size={14} /><span className="sr-only">Buscar componentes</span><input placeholder="Buscar componentes" /></label>
    <div className="catalog-group"><button><ChevronDown size={14} /> Entrada</button><div className="catalog-item"><Blocks size={18} /><span><strong>Portafolio inicial</strong><small>Capital y posiciones</small></span></div></div>
    <div className="catalog-group"><button><ChevronDown size={14} /> Analistas</button></div>
    <div className="catalog-group"><button><ChevronDown size={14} /> Salida</button></div>
  </>;
}

function BottomPlaceholder() {
  return <div className="result-shell">
    <div className="result-tabs"><button className="active">Salida</button><button>Razonamiento</button><button>Problemas <span>0</span></button><button>Terminal</button></div>
    <p>Ejecuta un flujo para ver decisiones, señales y razonamiento.</p>
  </div>;
}

export function App() {
  return <AppErrorBoundary><LayoutProvider><WorkbenchLayout top={<TopBar />} left={<FlowPlaceholder />} canvas={<CanvasPlaceholder />} right={<CatalogPlaceholder />} bottom={<BottomPlaceholder />} /></LayoutProvider></AppErrorBoundary>;
}
