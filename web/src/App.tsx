import { Component, type ErrorInfo, type ReactNode } from "react";
import { FilePlus2, LineChart, Search } from "lucide-react";
import { LayoutProvider } from "./contexts/LayoutContext";
import { NodeProvider } from "./contexts/NodeContext";
import { ComponentCatalog } from "./components/catalog/ComponentCatalog";
import { FlowCanvas } from "./components/flow/FlowCanvas";
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

function BottomPlaceholder() {
  return <div className="result-shell">
    <div className="result-tabs"><button className="active">Salida</button><button>Razonamiento</button><button>Problemas <span>0</span></button><button>Terminal</button></div>
    <p>Ejecuta un flujo para ver decisiones, señales y razonamiento.</p>
  </div>;
}

export function App() {
  return <AppErrorBoundary><LayoutProvider><NodeProvider><WorkbenchLayout top={<TopBar />} left={<FlowPlaceholder />} canvas={<FlowCanvas />} right={<ComponentCatalog />} bottom={<BottomPlaceholder />} /></NodeProvider></LayoutProvider></AppErrorBoundary>;
}
