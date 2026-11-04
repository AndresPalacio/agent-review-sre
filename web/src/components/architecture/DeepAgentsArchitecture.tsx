import {
  ArrowRight,
  Boxes,
  Check,
  CircleDot,
  Clock3,
  Code2,
  ExternalLink,
  Gauge,
  GitMerge,
  Layers3,
  Network,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { useState, type ComponentType } from "react";
import "./DeepAgentsArchitecture.css";

type ArchitectureMode = "actual" | "target" | "literal";
type NodeTone = "legacy" | "agent" | "runtime" | "control";

interface ArchitectureNode {
  id: string;
  title: string;
  eyebrow: string;
  summary: string;
  purpose: string;
  implementation: string;
  rationale: string;
  path: string;
  tone: NodeTone;
  icon: ComponentType<{ size?: number }>;
}

interface ArchitectureModeDefinition {
  label: string;
  caption: string;
  verdict: string;
  explanation: string;
  nodes: ArchitectureNode[];
}

const modes: Record<ArchitectureMode, ArchitectureModeDefinition> = {
  actual: {
    label: "Actual",
    caption: "Punto de partida",
    verdict: "La topología anterior mezclaba dos fábricas de agentes",
    explanation:
      "Antes de esta rama, algunos bucles autónomos nacían con LangChain create_agent y otros con Deep Agents. El control determinista ya vivía —y sigue viviendo— en LangGraph.",
    nodes: [
      {
        id: "legacy-factory",
        title: "Fábrica LangChain heredada",
        eyebrow: "Bucle autónomo",
        summary: "Agentes pequeños construidos fuera del arnés común.",
        purpose:
          "Crear agentes de herramientas con una ruta histórica directa.",
        implementation:
          "La fábrica común llamaba a langchain.agents.create_agent.",
        rationale:
          "Explica la brecha que la migración cerró: dos arneses para el mismo tipo de decisión autónoma.",
        path: "src/fin_graph/agents/common/factory.py",
        tone: "legacy",
        icon: Code2,
      },
      {
        id: "legacy-deep",
        title: "Deep Agents parcial",
        eyebrow: "Casos especializados",
        summary: "Personas y delegación ya usaban capacidades del arnés.",
        purpose: "Resolver análisis especializados con contexto y subagentes.",
        implementation:
          "Integraciones puntuales coexistían con la fábrica heredada.",
        rationale:
          "Demostró el patrón, pero no establecía una frontera uniforme.",
        path: "src/fin_graph/agents/analysis/personas.py",
        tone: "agent",
        icon: Boxes,
      },
      {
        id: "legacy-runtime",
        title: "Runtime LangGraph",
        eyebrow: "Base compartida",
        summary: "Ambos arneses terminaban ejecutándose sobre LangGraph.",
        purpose: "Aportar estado, streaming, persistencia e interrupciones.",
        implementation:
          "LangChain y Deep Agents compilan agentes sobre el runtime de LangGraph.",
        rationale:
          "El runtime nunca fue la duplicación que había que eliminar.",
        path: "src/fin_graph/workflows/analysis/graph.py",
        tone: "runtime",
        icon: Layers3,
      },
      {
        id: "legacy-control",
        title: "Flujos deterministas",
        eyebrow: "Control financiero",
        summary: "Órdenes, aprobaciones y efectos seguían rutas explícitas.",
        purpose: "Mantener auditables los cambios con impacto financiero.",
        implementation:
          "StateGraph y código ordinario gobiernan pasos y side effects.",
        rationale:
          "Una decisión regulada no debe depender de improvisación del modelo.",
        path: "src/fin_graph/workflows/orders/graph.py",
        tone: "control",
        icon: ShieldCheck,
      },
    ],
  },
  target: {
    label: "Objetivo",
    caption: "Implementado en esta rama",
    verdict:
      "Todo bucle autónomo usa Deep Agents; LangGraph conserva el control determinista",
    explanation:
      "Deep Agents ≥0.7.32,<0.8 es el arnés único de los agentes model-driven. La fábrica restringida no habilita filesystem, shell ni task síncrona por defecto; la delegación se activa sólo donde corresponde.",
    nodes: [
      {
        id: "restricted-factory",
        title: "Fábrica restringida",
        eyebrow: "Entrada única",
        summary: "make_agent normaliza todos los bucles autónomos.",
        purpose:
          "Ofrecer una sola frontera para construir agentes pequeños y seguros.",
        implementation:
          "make_agent llama create_deep_agent y reemplaza filesystem y subagentes síncronos por slots vacíos de forma predeterminada.",
        rationale:
          "Los agentes financieros sólo ven herramientas aprobadas por el llamador; no reciben filesystem, shell ni task por accidente.",
        path: "src/fin_graph/agents/common/factory.py",
        tone: "agent",
        icon: ShieldCheck,
      },
      {
        id: "native-sync",
        title: "Delegación síncrona nativa",
        eyebrow: "Opt-in",
        summary:
          "CompiledSubAgent expone task sólo a supervisores autorizados.",
        purpose:
          "Delegar a grafos compilados y esperar su resultado dentro de la ejecución.",
        implementation:
          'make_supervisor convierte cada especialista a CompiledSubAgent(mode="isolated") y habilita el middleware nativo.',
        rationale:
          "La ejecución puede ser paralela internamente, pero bloquea al supervisor hasta que el hijo termina; por eso no es el patrón para trabajo en segundo plano.",
        path: "src/fin_graph/agents/common/subagents.py",
        tone: "agent",
        icon: GitMerge,
      },
      {
        id: "native-async",
        title: "Hijos asíncronos nativos",
        eyebrow: "Agent Server",
        summary: "Dos AsyncSubAgent y cinco herramientas de ciclo de vida.",
        purpose:
          "Ejecutar fundamentals y technicals como trabajos concurrentes y dirigibles.",
        implementation:
          "El solver declara AsyncSubAgent y usa start, update, cancel, check y list; conserva IDs, límites y notificaciones.",
        rationale:
          "Son jobs de Agent Server con ciclo de vida propio, no simples llamadas a ainvoke envueltas en una tarea local.",
        path: "src/fin_graph/agents/solver/supervisor.py",
        tone: "agent",
        icon: Clock3,
      },
      {
        id: "deterministic-core",
        title: "Control determinista",
        eyebrow: "LangGraph permanece",
        summary:
          "Órdenes, aprobaciones, CAS, ledgers y fan-out siguen explícitos.",
        purpose:
          "Coordinar estado y efectos financieros con rutas verificables.",
        implementation:
          "StateGraph y servicios ordinarios conservan routing, checkpoints, aprobaciones, CAS y deduplicación de efectos.",
        rationale:
          "La autonomía se reserva para razonar; el control plane conserva invariantes auditables.",
        path: "src/fin_graph/workflows/orders/graph.py",
        tone: "control",
        icon: Layers3,
      },
      {
        id: "package-boundary",
        title: "Paquetes independientes",
        eyebrow: "Límite de distribución",
        summary:
          "El runtime raíz y packages/workflow mantienen contratos separados.",
        purpose:
          "Evitar que la migración del producto altere instalaciones standalone.",
        implementation:
          "El raíz fija deepagents ≥0.7.32,<0.8; packages/workflow conserva su extra agents.",
        rationale:
          "Una arquitectura unificada no exige acoplar unidades de distribución distintas.",
        path: "packages/workflow/pyproject.toml",
        tone: "runtime",
        icon: Boxes,
      },
    ],
  },
  literal: {
    label: "Literal",
    caption: "Lectura correcta",
    verdict: "Sustituir LangGraph sería un error de categoría",
    explanation:
      "“Full Deep Agents” significa un arnés único para loops autónomos, no borrar el runtime que Deep Agents utiliza ni convertir rutas deterministas en decisiones probabilísticas.",
    nodes: [
      {
        id: "literal-harness",
        title: "Deep Agents = arnés",
        eyebrow: "Capa de agencia",
        summary: "Planificación, contexto, tools y delegación para el modelo.",
        purpose: "Dar una envoltura opinionada a bucles autónomos.",
        implementation:
          "create_deep_agent ensambla middleware y construye el agente.",
        rationale:
          "Es la capa correcta para estandarizar comportamiento model-driven.",
        path: "src/fin_graph/agents/common/factory.py",
        tone: "agent",
        icon: Network,
      },
      {
        id: "literal-runtime",
        title: "LangGraph = runtime",
        eyebrow: "Capa de ejecución",
        summary: "Estado, edges, checkpoints, streaming e interrupciones.",
        purpose: "Ejecutar agentes y workflows durables.",
        implementation: "Deep Agents compila sobre el runtime LangGraph.",
        rationale:
          "Quitar el runtime elimina una dependencia arquitectónica del propio arnés.",
        path: "src/fin_graph/workflows/analysis/graph.py",
        tone: "runtime",
        icon: Layers3,
      },
      {
        id: "literal-control",
        title: "Control determinista",
        eyebrow: "Capa de política",
        summary: "Secuencias fijas para órdenes, aprobaciones, CAS y ledgers.",
        purpose: "Hacer reproducibles las decisiones de control y los efectos.",
        implementation:
          "Grafos explícitos y código ordinario validan cada transición.",
        rationale:
          "Reemplazar invariantes por decisiones del LLM reduciría seguridad y auditabilidad.",
        path: "src/fin_graph/persistence/action_ledger.py",
        tone: "control",
        icon: ShieldCheck,
      },
    ],
  },
};

const migration = [
  {
    task: "01",
    title: "Runtime raíz",
    copy: "Deep Agents ≥0.7.32,<0.8 pasa a la instalación base; workflow queda independiente.",
  },
  {
    task: "02",
    title: "Fábrica restringida",
    copy: "make_agent adopta create_deep_agent sin ampliar herramientas por defecto.",
  },
  {
    task: "03",
    title: "Async nativo",
    copy: "El solver declara AsyncSubAgent y preserva su ciclo de vida completo.",
  },
  {
    task: "04",
    title: "Sync nativo",
    copy: "Los supervisores opt-in delegan con CompiledSubAgent y task.",
  },
];

const matrix = [
  {
    dimension: "Fábrica de agentes",
    gain: "Una sola seam, middleware y estado compartidos.",
    cost: "El arnés incorpora más convenciones que una fábrica mínima.",
  },
  {
    dimension: "Superficie de tools",
    gain: "Allowlist estricta para analistas financieros.",
    cost: "Cada capacidad adicional requiere opt-in explícito.",
  },
  {
    dimension: "Delegación",
    gain: "Contratos nativos sync y async con identidad y lifecycle.",
    cost: "La capacidad de workers debe planearse para el fan-out async.",
  },
  {
    dimension: "Control determinista",
    gain: "Órdenes y efectos conservan auditabilidad y reanudación.",
    cost: "Persisten dos niveles conceptuales: arnés y control plane.",
  },
];

const modeOrder: ArchitectureMode[] = ["actual", "target", "literal"];

export function DeepAgentsArchitecture() {
  const [mode, setMode] = useState<ArchitectureMode>("target");
  const [selectedId, setSelectedId] = useState(modes.target.nodes[0].id);
  const [children, setChildren] = useState(2);
  const [workers, setWorkers] = useState(4);
  const definition = modes[mode];
  const selected =
    definition.nodes.find((node) => node.id === selectedId) ??
    definition.nodes[0];
  const requiredSlots = 1 + children;
  const shortfall = Math.max(0, requiredSlots - workers);

  function selectMode(nextMode: ArchitectureMode) {
    setMode(nextMode);
    setSelectedId(modes[nextMode].nodes[0].id);
  }

  return (
    <main className="product-page architecture-page">
      <header className="architecture-hero" role="presentation">
        <div>
          <span>MAPA EJECUTABLE · RAMA FULL DEEP AGENTS</span>
          <h1>Arquitectura de agentes</h1>
          <p>
            Explora dónde razona el modelo, dónde se delega trabajo y dónde el
            control financiero sigue siendo deliberadamente determinista.
          </p>
        </div>
        <div className="architecture-version" aria-label="Versión objetivo">
          <CircleDot size={14} />
          <span>deepagents</span>
          <strong>≥0.7.32 &lt;0.8</strong>
        </div>
      </header>

      <div className="architecture-content">
        <section
          className="architecture-overview"
          aria-labelledby="architecture-verdict"
        >
          <div
            className="architecture-mode-picker"
            aria-label="Modo de arquitectura"
          >
            {modeOrder.map((modeKey) => (
              <button
                type="button"
                key={modeKey}
                aria-label={modes[modeKey].label}
                aria-pressed={mode === modeKey}
                onClick={() => selectMode(modeKey)}
              >
                <small>{modes[modeKey].caption}</small>
                {modes[modeKey].label}
              </button>
            ))}
          </div>
          <div
            className={`architecture-verdict architecture-verdict-${mode}`}
            role="status"
            aria-label="Veredicto de arquitectura"
            aria-live="polite"
          >
            <span>VEREDICTO</span>
            <h2 id="architecture-verdict">{definition.verdict}</h2>
            <p>{definition.explanation}</p>
          </div>
        </section>

        <section
          className="architecture-explorer"
          aria-labelledby="topology-title"
        >
          <div className="architecture-section-heading">
            <div>
              <span>TOPOLOGÍA · {definition.label.toUpperCase()}</span>
              <h2 id="topology-title">
                Selecciona una pieza para inspeccionarla
              </h2>
            </div>
            <p>Todos los nodos son botones y admiten activación por teclado.</p>
          </div>
          <div className="architecture-explorer-grid">
            <div
              className="architecture-topology"
              aria-label={`Topología ${definition.label}`}
            >
              {definition.nodes.map((node, index) => {
                const Icon = node.icon;
                return (
                  <div className="architecture-node-step" key={node.id}>
                    <button
                      type="button"
                      className={`architecture-node tone-${node.tone}`}
                      aria-label={node.title}
                      aria-pressed={selected.id === node.id}
                      aria-controls="architecture-node-inspector"
                      onClick={() => setSelectedId(node.id)}
                    >
                      <span className="architecture-node-icon">
                        <Icon size={18} />
                      </span>
                      <span>
                        <small>{node.eyebrow}</small>
                        <strong>{node.title}</strong>
                        <em>{node.summary}</em>
                      </span>
                    </button>
                    {index < definition.nodes.length - 1 && (
                      <ArrowRight
                        className="architecture-node-arrow"
                        size={17}
                        aria-hidden="true"
                      />
                    )}
                  </div>
                );
              })}
            </div>
            <article
              id="architecture-node-inspector"
              className="architecture-inspector"
              role="region"
              aria-label="Detalle del nodo"
              aria-live="polite"
            >
              <div className="architecture-inspector-title">
                <span className={`inspector-dot tone-${selected.tone}`} />
                <div>
                  <small>NODO SELECCIONADO</small>
                  <h3>{selected.title}</h3>
                </div>
              </div>
              <dl>
                <div>
                  <dt>Propósito</dt>
                  <dd>{selected.purpose}</dd>
                </div>
                <div>
                  <dt>Implementación</dt>
                  <dd>{selected.implementation}</dd>
                </div>
                <div>
                  <dt>Por qué existe</dt>
                  <dd>{selected.rationale}</dd>
                </div>
                <div className="architecture-path">
                  <dt>Ruta en el repositorio</dt>
                  <dd>
                    <code>{selected.path}</code>
                  </dd>
                </div>
              </dl>
            </article>
          </div>
        </section>

        <section className="factory-section" aria-labelledby="factory-title">
          <div className="architecture-section-heading">
            <div>
              <span>INSPECTOR DE FÁBRICA</span>
              <h2 id="factory-title">Una llamada, tres capas</h2>
            </div>
            <p>El arnés cambia; la base de ejecución sigue siendo LangGraph.</p>
          </div>
          <ol className="factory-chain">
            <li>
              <span>01</span>
              <div>
                <code>make_agent(...)</code>
                <small>Frontera local y allowlist</small>
              </div>
            </li>
            <li className="chain-connector" aria-hidden="true">
              <ArrowRight size={18} />
            </li>
            <li>
              <span>02</span>
              <div>
                <code>deepagents.create_deep_agent</code>
                <small>Arnés, estado y middleware</small>
              </div>
            </li>
            <li className="chain-connector" aria-hidden="true">
              <ArrowRight size={18} />
            </li>
            <li>
              <span>03</span>
              <div>
                <code>LangChain / LangGraph runtime</code>
                <small>Modelos, tools y ejecución durable</small>
              </div>
            </li>
          </ol>
        </section>

        <section
          className="concurrency-section"
          aria-labelledby="concurrency-title"
        >
          <div className="concurrency-copy">
            <span>SIMULADOR DE CONCURRENCIA</span>
            <h2 id="concurrency-title">Dimensiona el pool antes del fan-out</h2>
            <p>
              Cada ejecución ocupa un worker para el supervisor y uno adicional
              por cada hijo asíncrono activo: <strong>1 + hijos</strong>.
            </p>
            <div className="delegation-notes">
              <article>
                <GitMerge size={17} />
                <div>
                  <strong>Sync · paralelo pero bloqueante</strong>
                  <p>
                    El supervisor usa <code>task</code> y espera el resultado
                    del CompiledSubAgent.
                  </p>
                </div>
              </article>
              <article>
                <Clock3 size={17} />
                <div>
                  <strong>Async · jobs de Agent Server</strong>
                  <p>
                    Tienen ID, estado, update, cancel, check y list. No son
                    meramente <code>ainvoke</code>.
                  </p>
                </div>
              </article>
            </div>
          </div>
          <div className="concurrency-simulator">
            <div className="simulator-result">
              <Gauge size={24} />
              <div>
                <strong>{requiredSlots} slots requeridos</strong>
                <span>1 supervisor + {children} hijos</span>
              </div>
              <b className={shortfall ? "is-warning" : "is-ready"}>
                {shortfall ? "Capacidad insuficiente" : "Capacidad lista"}
              </b>
            </div>
            <label>
              <span>
                Hijos asíncronos <output>{children}</output>
              </span>
              <input
                type="range"
                aria-label="Hijos asíncronos"
                min="0"
                max="8"
                value={children}
                onChange={(event) => setChildren(Number(event.target.value))}
              />
            </label>
            <label>
              <span>
                Workers disponibles <output>{workers}</output>
              </span>
              <input
                type="range"
                aria-label="Workers disponibles"
                min="1"
                max="10"
                value={workers}
                onChange={(event) => setWorkers(Number(event.target.value))}
              />
            </label>
            {shortfall > 0 ? (
              <p className="capacity-warning" role="alert">
                <TriangleAlert size={16} /> Faltan {shortfall} workers: se
                necesitan {requiredSlots} y hay {workers}.
              </p>
            ) : (
              <p className="capacity-ready" role="status">
                <Check size={16} /> Hay {workers - requiredSlots} slots de
                reserva.
              </p>
            )}
          </div>
        </section>

        <section
          className="migration-section"
          aria-labelledby="migration-title"
        >
          <div className="architecture-section-heading">
            <div>
              <span>MIGRACIÓN POR ETAPAS</span>
              <h2 id="migration-title">
                Del dependency graph a la delegación nativa
              </h2>
            </div>
            <p>Las cuatro etapas ya están implementadas en esta rama.</p>
          </div>
          <ol className="migration-map">
            {migration.map((step) => (
              <li key={step.task}>
                <span>{step.task}</span>
                <Check aria-hidden="true" size={14} />
                <h3>{step.title}</h3>
                <p>{step.copy}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="matrix-section" aria-labelledby="matrix-title">
          <div className="architecture-section-heading">
            <div>
              <span>BALANCE ARQUITECTÓNICO</span>
              <h2 id="matrix-title">Qué ganamos y qué aceptamos</h2>
            </div>
          </div>
          <div className="matrix-scroll">
            <table>
              <thead>
                <tr>
                  <th>Dimensión</th>
                  <th>Ganancia</th>
                  <th>Coste / límite deliberado</th>
                </tr>
              </thead>
              <tbody>
                {matrix.map((row) => (
                  <tr key={row.dimension}>
                    <th scope="row">{row.dimension}</th>
                    <td>
                      <Check size={14} aria-hidden="true" />
                      {row.gain}
                    </td>
                    <td>
                      <TriangleAlert size={14} aria-hidden="true" />
                      {row.cost}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <footer className="architecture-sources">
          <div>
            <span>FUENTES PRIMARIAS</span>
            <h2>Continúa en la documentación oficial</h2>
          </div>
          <nav aria-label="Documentación oficial">
            <a
              href="https://docs.langchain.com/oss/python/deepagents/subagents"
              target="_blank"
              rel="noreferrer noopener"
            >
              Subagentes de Deep Agents <ExternalLink size={13} />
            </a>
            <a
              href="https://docs.langchain.com/oss/python/deepagents/async-subagents"
              target="_blank"
              rel="noreferrer noopener"
            >
              Subagentes asíncronos de Deep Agents <ExternalLink size={13} />
            </a>
            <a
              href="https://docs.langchain.com/oss/python/langgraph/overview"
              target="_blank"
              rel="noreferrer noopener"
            >
              Documentación de LangGraph <ExternalLink size={13} />
            </a>
          </nav>
        </footer>
      </div>
    </main>
  );
}
