import {
  Component,
  Suspense,
  lazy,
  useCallback,
  useState,
  type ErrorInfo,
  type ReactNode,
} from "react";
import { FilePlus2, Landmark, LineChart } from "lucide-react";
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
import type { NativeSession } from "./components/settings/SettingsDialog";
import { createCatalogNode, type CatalogItem } from "./data/node-catalog";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import type { FlowDocument } from "./types/flow";
import "./style.css";

const BacktestWorkspace = lazy(() =>
  import("./components/backtests/BacktestForm").then((module) => ({
    default: module.BacktestWorkspace,
  })),
);
const AnalysisExplainView = lazy(() =>
  import("./components/native/AnalysisExplainView").then((module) => ({
    default: module.AnalysisExplainView,
  })),
);
const ApprovalView = lazy(() =>
  import("./components/native/ApprovalView").then((module) => ({
    default: module.ApprovalView,
  })),
);
const OrdersView = lazy(() =>
  import("./components/native/OrdersView").then((module) => ({
    default: module.OrdersView,
  })),
);
const PortfolioView = lazy(() =>
  import("./components/native/PortfolioView").then((module) => ({
    default: module.PortfolioView,
  })),
);
const SettingsDialog = lazy(() =>
  import("./components/settings/SettingsDialog").then((module) => ({
    default: module.SettingsDialog,
  })),
);

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

function LoadingView() {
  return (
    <div className="loading-view" role="status" aria-label="Cargando vista">
      Cargando...
    </div>
  );
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
  const activeFlowId = activeTab?.flow.id;
  const updateCanvas = useCallback(
    (document: FlowDocument) => {
      if (activeFlowId !== undefined) updateDocument(activeFlowId, document);
    },
    [activeFlowId, updateDocument],
  );
  const canvas = (
    <div className="canvas-workspace">
      <TabBar />
      <div className="canvas-stage">
        {activeTab ? (
          <FlowCanvas
            initialNodes={activeTab.document.nodes}
            initialEdges={activeTab.document.edges}
            onChange={updateCanvas}
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
      onSave={() => {
        if (activeTab) void saveFlow(activeTab.flow.id);
      }}
      onRun={() =>
        (
          document.querySelector(
            "[aria-label='Símbolos']",
          ) as HTMLInputElement | null
        )?.focus()
      }
    />
  );
  const settings = settingsOpen ? (
    <Suspense fallback={<LoadingView />}>
      <SettingsDialog
        open
        onOpenChange={setSettingsOpen}
        onNativeSession={setNativeSession}
      />
    </Suspense>
  ) : null;
  if (page === "backtests")
    return (
      <div className="workbench-shell product-shell">
        {top}
        <Suspense fallback={<LoadingView />}>
          <BacktestWorkspace />
        </Suspense>
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
  const [revision, setRevision] = useState(0);
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
      {!session.connected ? (
        <section className="native-connect-state">
          <Landmark size={28} />
          <h2>Conecta el acceso nativo</h2>
          <p>
            Abre ConfiguraciÃ³n e inicia una sesiÃ³n `/v1` para consultar la
            cartera y las aprobaciones.
          </p>
        </section>
      ) : (
        <Suspense fallback={<LoadingView />}>
          <div className="native-grid" key={`${session.role}-${revision}`}>
            <PortfolioView />
            <OrdersView />
            <ApprovalView
              role={session.role}
              onDecision={() => setRevision((value) => value + 1)}
            />
            <AnalysisExplainView />
          </div>
        </Suspense>
      )}
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
