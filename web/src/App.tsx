import { Component, useState, type ErrorInfo, type ReactNode } from "react";
import { FilePlus2, LineChart } from "lucide-react";
import { LayoutProvider } from "./contexts/LayoutContext";
import { NodeProvider } from "./contexts/NodeContext";
import { FlowProvider, useFlows } from "./contexts/FlowContext";
import { TabsProvider, useTabs } from "./contexts/TabsContext";
import { RunProvider } from "./contexts/RunContext";
import { ComponentCatalog } from "./components/catalog/ComponentCatalog";
import { FlowCanvas } from "./components/flow/FlowCanvas";
import { FlowSidebar } from "./components/flows/FlowSidebar";
import { TabBar } from "./components/tabs/TabBar";
import { BottomPanel } from "./components/output/BottomPanel";
import { TopBar, type ProductPage } from "./components/layout/TopBar";
import { WorkbenchLayout } from "./components/layout/WorkbenchLayout";
import { BacktestWorkspace } from "./components/backtests/BacktestForm";
import { AnalysisExplainView } from "./components/native/AnalysisExplainView";
import { ApprovalView } from "./components/native/ApprovalView";
import { OrdersView } from "./components/native/OrdersView";
import { PortfolioView } from "./components/native/PortfolioView";
import {
  SettingsDialog,
  type NativeSession,
} from "./components/settings/SettingsDialog";
import { createCatalogNode, type CatalogItem } from "./data/node-catalog";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import "./style.css";

interface BoundaryProps {
  children: ReactNode;
}
interface BoundaryState {
  failed: boolean;
}

export class AppErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false };
  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }
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

function Workbench() {
  const { activeTab, updateDocument } = useTabs();
  const { saveFlow } = useFlows();
  const [page, setPage] = useState<ProductPage>("flows");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [nativeSession, setNativeSession] = useState<NativeSession>({
    role: "viewer",
    connected: false,
  });
  useKeyboardShortcuts({
    save: () => {
      if (activeTab) void saveFlow(activeTab.flow.id);
    },
  });
  const addComponent = (item: CatalogItem) => {
    if (!activeTab) return;
    const offset = activeTab.document.nodes.length * 18;
    updateDocument(activeTab.flow.id, {
      ...activeTab.document,
      nodes: [
        ...activeTab.document.nodes,
        createCatalogNode(item.key, { x: 100 + offset, y: 90 + offset }),
      ],
    });
  };
  const canvas = (
    <div className="canvas-workspace">
      <TabBar />
      <div className="canvas-stage">
        {activeTab ? (
          <FlowCanvas
            initialNodes={activeTab.document.nodes}
            initialEdges={activeTab.document.edges}
            onChange={(document) => updateDocument(activeTab.flow.id, document)}
          />
        ) : (
          <div className="canvas-empty">
            <div className="canvas-grid" />
            <div className="empty-card">
              <LineChart size={34} />
              <h1>Construye tu estrategia</h1>
              <p>
                Abre o crea un flujo y arrastra analistas para diseñar una
                estrategia.
              </p>
              <FilePlus2 size={18} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
  const top = (
    <TopBar
      page={page}
      onPageChange={setPage}
      onSettings={() => setSettingsOpen(true)}
      onRun={() =>
        (
          document.querySelector(
            "[aria-label='Símbolos']",
          ) as HTMLInputElement | null
        )?.focus()
      }
    />
  );
  const settings = (
    <SettingsDialog
      open={settingsOpen}
      onOpenChange={setSettingsOpen}
      onNativeSession={setNativeSession}
    />
  );
  if (page === "backtests")
    return (
      <div className="workbench-shell product-shell">
        {top}
        <BacktestWorkspace />
        {settings}
      </div>
    );
  if (page === "paper")
    return (
      <div className="workbench-shell product-shell">
        {top}
        <PaperWorkspace session={nativeSession} />
        {settings}
      </div>
    );
  return (
    <>
      <WorkbenchLayout
        top={top}
        left={<FlowSidebar />}
        canvas={canvas}
        right={<ComponentCatalog onAdd={addComponent} />}
        bottom={<BottomPanel />}
      />
      {settings}
    </>
  );
}

function PaperWorkspace({ session }: { session: NativeSession }) {
  return (
    <main className="product-page native-workspace">
      <header>
        <div>
          <span>EJECUCIÓN CONTROLADA</span>
          <h1>Mesa de inversión</h1>
          <p>
            Supervisa la cartera simulada, las órdenes y cada decisión que
            requiere aprobación humana.
          </p>
        </div>
        <strong
          className={
            session.connected ? "session-badge connected" : "session-badge"
          }
        >
          {session.connected
            ? `Conectado · ${session.role}`
            : "Sesión no conectada"}
        </strong>
      </header>
      <div className="native-grid">
        <PortfolioView />
        <OrdersView />
        <ApprovalView role={session.role} />
        <AnalysisExplainView />
      </div>
    </main>
  );
}

export function App() {
  return (
    <AppErrorBoundary>
      <LayoutProvider>
        <NodeProvider>
          <TabsProvider>
            <FlowProvider>
              <RunProvider>
                <Workbench />
              </RunProvider>
            </FlowProvider>
          </TabsProvider>
        </NodeProvider>
      </LayoutProvider>
    </AppErrorBoundary>
  );
}
