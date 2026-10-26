import { useEffect, useState, useRef, type ReactNode } from "react";
import {
  Activity,
  Search,
  MessageSquare,
  CalendarClock,
  Plug,
  ShieldCheck,
  LogOut,
  ArrowUpRight,
  Layers,
  RefreshCw,
  ChevronRight,
} from "lucide-react";
import { Api, allowed, type Role } from "./api";
import { ApprovalCard, type ApprovalRequest } from "./ApprovalCard";
import "./style.css";
import { Overview, InvestigationContent } from "./InvestigationContent";
import { AgentFlow, type AgentFlowData } from "./AgentFlow";
type Row = Record<string, unknown>;
type Run = {
  thread_id: string;
  run_id?: string;
  id?: string;
  status: string;
  created_at?: string;
};
type Thread = {
  thread_id: string;
  status: string;
  requests: ApprovalRequest[];
  approval_ids: string[];
  messages?: { role: string; text: string }[];
  outcome?: string;
  result?: unknown;
  rollback?: unknown;
};
type Session = { api: Api; role: Role };
const stringify = (value: unknown) => JSON.stringify(value, null, 2);
const message = (e: unknown) =>
  e instanceof Error ? e.message : "No se pudo completar la operación.";
const statusLabels: Record<string, string> = {
  completed: "Completada",
  running: "En curso",
  queued: "En cola",
  failed: "Fallida",
  interrupted: "Interrumpida",
  recovering: "Recuperando",
  awaiting_approval: "Pendiente de aprobación",
  available: "Disponible",
  not_configured: "Sin configurar",
  healthy: "Saludable",
};
function Badge({ value }: { value: string }) {
  return (
    <span className={`badge ${value}`}>{statusLabels[value] ?? value}</span>
  );
}
function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <Layers size={28} />
      <p>{children}</p>
    </div>
  );
}
function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div role="alert" className="error">
      {error}
    </div>
  ) : null;
}
const fieldLabels: Record<string, string> = {
  hypotheses: "Hip\u00f3tesis",
  summary: "Resumen",
  title: "T\u00edtulo",
  confidence: "Confianza",
  statement: "Afirmaci\u00f3n",
  claims: "Afirmaciones",
  citations: "Citas",
  evidence_ids: "Evidencia citada",
  root_cause: "Causa ra\u00edz",
  status: "Estado",
  reason: "Motivo",
  observed: "Datos observados",
  source: "Fuente",
  integration: "Integración",
  description: "Descripci\u00f3n",
  actions: "Acciones",
  verification: "Verificaci\u00f3n",
  diagnosis: "Diagn\u00f3stico",
  evidence: "Evidencia",
  rationale: "Justificaci\u00f3n",
  tool: "Herramienta",
  args: "Argumentos",
  risk: "Riesgo",
  hypothesis: "Hip\u00f3tesis",
  findings: "Hallazgos",
};
function ReadableValue({
  value,
  depth = 0,
}: {
  value: unknown;
  depth?: number;
}): ReactNode {
  if (value === null || value === undefined)
    return <span className="muted">Sin información</span>;
  if (typeof value === "boolean") return value ? "S\u00ed" : "No";
  if (typeof value !== "object")
    return <span className="prewrap">{String(value)}</span>;
  if (Array.isArray(value))
    return value.length ? (
      <ul className="readable-list">
        {value.map((v, i) => (
          <li key={i}>
            <ReadableValue value={v} depth={depth + 1} />
          </li>
        ))}
      </ul>
    ) : (
      <span className="muted">Sin elementos</span>
    );
  if (depth > 6) return <pre>{stringify(value)}</pre>;
  return (
    <dl>
      {Object.entries(value).map(([key, v]) => (
        <div key={key}>
          <dt>{fieldLabels[key] ?? key.replaceAll("_", " ")}</dt>
          <dd>
            <ReadableValue value={v} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
function DataBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {value !== undefined &&
      value !== null &&
      (!Array.isArray(value) || value.length) ? (
        <>
          <ReadableValue value={value} />
          <details>
            <summary>Ver datos originales</summary>
            <pre className="data">{stringify(value)}</pre>
          </details>
        </>
      ) : (
        <p className="muted">Todav?a no hay informaci?n disponible.</p>
      )}
    </section>
  );
}
export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [key, setKey] = useState("");
  const [loginError, setLoginError] = useState("");
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState("Investigaciones");
  const [investigation, setInvestigation] = useState("");
  const currentSession = useRef<Session | null>(null);
  useEffect(() => () => currentSession.current?.api.close(), []);
  async function login(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setLoginError("");
    const api = new Api(key);
    try {
      const data = await api.request<{ role: Role }>("/session");
      if (!["viewer", "operator", "approver", "admin"].includes(data.role))
        throw new Error("Rol desconocido");
      const s = { api, role: data.role };
      currentSession.current = s;
      setSession(s);
      setKey("");
    } catch (e) {
      api.close();
      setLoginError(message(e));
    } finally {
      setBusy(false);
    }
  }
  if (!session)
    return (
      <main className="login">
        <div className="login-story">
          <div className="brand">
            <Activity /> SRE<span>GRAPH</span>
          </div>
          <div>
            <div className="eyebrow">INTELIGENCIA OPERACIONAL</div>
            <h1>
              De la señal
              <br />a la certeza.
            </h1>
            <p>
              Investiga incidentes, conecta la evidencia y remedia con control
              humano.
            </p>
            <div className="login-grid">
              <span>01 / Investigar</span>
              <span>02 / Verificar</span>
              <span>03 / Aprobar</span>
            </div>
          </div>
          <small>CONSOLA DE OPERACIONES · FIN GRAPH</small>
        </div>
        <section className="login-form">
          <div className="eyebrow">ACCESO A LA CONSOLA</div>
          <h2>Tu centro de operaciones</h2>
          <p className="muted">Inicia una sesión segura con tu API key.</p>
          <form onSubmit={login}>
            <label>
              API key
              <input
                type="password"
                autoComplete="off"
                required
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="Introduce tu API key"
              />
            </label>
            <ErrorBox error={loginError} />
            <button disabled={busy}>
              {busy ? "Validando…" : "Iniciar sesión"}{" "}
              <ArrowUpRight size={16} />
            </button>
          </form>
          <div className="notice">
            <ShieldCheck size={18} /> La clave permanece solo en memoria durante
            esta sesión.
          </div>
        </section>
      </main>
    );
  const nav = [
    { name: "Investigaciones", icon: Search },
    { name: "Chat", icon: MessageSquare },
    { name: "Remediaciones", icon: ShieldCheck },
    { name: "Automatizaciones", icon: CalendarClock },
    { name: "Integraciones", icon: Plug },
  ];
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <Activity /> SRE<span>GRAPH</span>
        </div>
        <div className="workspace">
          <span className="workspace-icon">S</span>
          <div>
            Operaciones<small>Consola SRE</small>
          </div>
        </div>
        <div className="nav-label">OPERACIONES</div>
        <nav aria-label="Navegación principal">
          {nav.map((n) => (
            <button
              key={n.name}
              className={page === n.name ? "nav active" : "nav"}
              onClick={() => {
                setPage(n.name);
                setInvestigation("");
              }}
            >
              <n.icon size={18} />
              {n.name}
              {page === n.name && <ChevronRight size={15} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="role">
            <span className="avatar">{session.role[0].toUpperCase()}</span>
            <div>
              Sesión activa<small>Rol: {session.role}</small>
            </div>
          </div>
          <button
            className="logout"
            onClick={() => {
              session.api.close();
              currentSession.current = null;
              setSession(null);
              setInvestigation("");
              setPage("Investigaciones");
            }}
          >
            <LogOut size={16} />
            Cerrar sesión
          </button>
        </div>
      </aside>
      <div className="content">
        <header>
          <span>
            Operaciones <ChevronRight size={13} /> {page}
          </span>
          <div>
            <span className="live-dot" /> Sesión autenticada{" "}
            <Badge value={session.role} />
          </div>
        </header>
        <main key={page}>
          {page === "Investigaciones" ? (
            investigation ? (
              <Investigation
                session={session}
                id={investigation}
                back={() => setInvestigation("")}
                remediate={() => setPage("Remediaciones")}
              />
            ) : (
              <Investigations session={session} open={setInvestigation} />
            )
          ) : page === "Chat" ? (
            <Chat session={session} />
          ) : page === "Remediaciones" ? (
            <Remediations session={session} />
          ) : page === "Automatizaciones" ? (
            <Schedules session={session} />
          ) : (
            <Integrations session={session} />
          )}
        </main>
        <footer>
          FIN GRAPH{" "}
          <span>Investigaciones de lectura · Acciones bajo aprobación</span>
        </footer>
      </div>
    </div>
  );
}
function Heading({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {children}
    </div>
  );
}
function Investigations({
  session,
  open,
}: {
  session: Session;
  open: (id: string) => void;
}) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const [creating, setCreating] = useState(false);
  async function load() {
    setLoading(true);
    setError("");
    try {
      setRuns(await session.api.request<Run[]>("/threads"));
    } catch (e) {
      setError(message(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, [session]);
  async function create(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError("");
    try {
      const r = await session.api.request<{ thread_id: string }>(
        "/investigations",
        "POST",
        { text },
      );
      open(r.thread_id);
    } catch (e) {
      setError(message(e));
    } finally {
      setCreating(false);
    }
  }
  return (
    <>
      <Heading
        eyebrow="OBSERVABILIDAD / INVESTIGACIÓN"
        title="Investigaciones"
        description="Cada incidente, una historia respaldada por evidencia."
      >
        <button className="secondary" onClick={load}>
          <RefreshCw size={16} />
          Actualizar
        </button>
      </Heading>
      <div className="stats">
        <div>
          <span>INVESTIGACIONES</span>
          <strong>{runs.length}</strong>
          <small>Últimas 50 ejecuciones</small>
        </div>
        <div>
          <span>EN CURSO</span>
          <strong>
            {
              runs.filter((r) =>
                ["running", "queued", "recovering"].includes(r.status),
              ).length
            }
          </strong>
          <small>Actividad del sistema</small>
        </div>
        <div>
          <span>COMPLETADAS</span>
          <strong>{runs.filter((r) => r.status === "completed").length}</strong>
          <small>Evidencia disponible</small>
        </div>
      </div>
      {allowed(session.role, "operator") ? (
        <form className="panel create" onSubmit={create}>
          <div>
            <h2>Nueva investigación</h2>
            <p>Describe la alerta o el síntoma que necesitas investigar.</p>
          </div>
          <label>
            Texto de la alerta
            <textarea
              required
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Ej. Latencia elevada en el servicio de pagos…"
            />
          </label>
          <button disabled={creating}>
            {creating ? "Iniciando…" : "Investigar"}
            <ArrowUpRight size={16} />
          </button>
        </form>
      ) : (
        <div className="notice">
          Modo de consulta. Tu rol viewer no permite iniciar investigaciones.
        </div>
      )}
      <ErrorBox error={error} />
      <section className="panel">
        <div className="section-head">
          <h2>Actividad reciente</h2>
          <span className="muted">{runs.length} ejecuciones</span>
        </div>
        {loading ? (
          <p role="status">Cargando investigaciones…</p>
        ) : !runs.length ? (
          <Empty>
            Aún no hay investigaciones. Las nuevas ejecuciones aparecerán aquí.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Investigación</th>
                  <th>Estado</th>
                  <th>Creada</th>
                  <th>
                    <span className="sr-only">Abrir</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r, i) => (
                  <tr key={r.run_id ?? r.id ?? i}>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => open(r.thread_id)}
                      >
                        {r.thread_id}
                      </button>
                    </td>
                    <td>
                      <Badge value={r.status} />
                    </td>
                    <td>
                      {r.created_at
                        ? new Date(r.created_at).toLocaleString("es")
                        : "—"}
                    </td>
                    <td>
                      <button
                        className="icon-button"
                        aria-label={`Abrir investigación ${r.thread_id}`}
                        onClick={() => open(r.thread_id)}
                      >
                        <ArrowUpRight size={17} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
function Investigation({
  session,
  id,
  back,
  remediate,
}: {
  session: Session;
  id: string;
  back: () => void;
  remediate: () => void;
}) {
  const [detail, setDetail] = useState<Row | null>(null);
  const [events, setEvents] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("Conectando");
  const [version, setVersion] = useState(0);
  const [showFlow, setShowFlow] = useState(false);
  const [explanation, setExplanation] = useState<AgentFlowData | null>(null);
  const [flowLoading, setFlowLoading] = useState(false);
  const [flowError, setFlowError] = useState("");
  const [flowVersion, setFlowVersion] = useState(0);
  useEffect(() => {
    if (!showFlow) return;
    let alive = true;
    setFlowLoading(true);
    setFlowError("");
    void session.api
      .request<AgentFlowData>(
        `/investigations/${encodeURIComponent(id)}/explain`,
      )
      .then((result) => {
        if (alive) setExplanation(result);
      })
      .catch((e) => {
        if (alive) setFlowError(message(e));
      })
      .finally(() => {
        if (alive) setFlowLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [id, session, showFlow, flowVersion]);
  useEffect(() => {
    const c = new AbortController();
    let alive = true;
    let cursor = "";
    let terminal = false;
    let failures = 0;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    const pause = () =>
      new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, 1000);
        c.signal.addEventListener(
          "abort",
          () => {
            clearTimeout(timer);
            resolve();
          },
          { once: true },
        );
      });
    const seen = new Set<string>();
    const load = async () => {
      const d = await session.api.request<Row>(
        `/investigations/${encodeURIComponent(id)}`,
      );
      if (alive) setDetail(d);
    };
    async function watch() {
      try {
        await load();
        const history = await session.api.request<Row[]>(
          `/threads/${encodeURIComponent(id)}/history`,
        );
        if (!alive) return;
        setEvents(history);
        for (const event of history) {
          const eid = String(event.id);
          seen.add(eid);
          cursor = eid;
        }
        while (!c.signal.aborted) {
          setConnection("En vivo");
          try {
            await session.api.stream(
              `/threads/${encodeURIComponent(id)}/stream`,
              (e) => {
                if (!alive) return;
                const data = JSON.parse(e.data) as Row;
                if (e.id && seen.has(e.id)) return;
                if (e.id) {
                  seen.add(e.id);
                  cursor = e.id;
                }
                setEvents((old) => [...old, data]);
                if (!refreshTimer)
                  refreshTimer = setTimeout(() => {
                    refreshTimer = undefined;
                    void load().catch((e) => {
                      if (alive) setError(message(e));
                    });
                  }, 250);
                const content = data.data as Row | undefined;
                if (
                  e.event === "status" &&
                  content &&
                  ["completed", "failed", "interrupted"].includes(
                    String(content.status),
                  )
                )
                  terminal = true;
              },
              { lastId: cursor, signal: c.signal },
            );
            failures = 0;
          } catch (streamError) {
            if (c.signal.aborted) return;
            if (++failures > 3) throw streamError;
            setConnection(`Reconectando (${failures}/3)`);
            await pause();
            continue;
          }
          await load();
          if (terminal || c.signal.aborted) break;
          const d = await session.api.request<Row>(
            `/investigations/${encodeURIComponent(id)}`,
          );
          if (
            ["completed", "failed", "interrupted"].includes(
              String((d.run as Row)?.status),
            )
          )
            break;
          await new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, 1000);
            c.signal.addEventListener(
              "abort",
              () => {
                clearTimeout(timer);
                resolve();
              },
              { once: true },
            );
          });
        }
        if (alive) setConnection("Sincronizado");
      } catch (e) {
        if (alive && !c.signal.aborted) {
          setError(message(e));
          setConnection("Desconectado");
        }
      }
    }
    void watch();
    return () => {
      alive = false;
      if (refreshTimer) clearTimeout(refreshTimer);
      c.abort();
    };
  }, [id, session, version]);
  const run = detail?.run as Row | undefined;
  const report = detail?.report as Row | undefined;
  return (
    <>
      <button className="text-button" onClick={back}>
        ← Todas las investigaciones
      </button>
      <Heading
        eyebrow="INVESTIGACIÓN / DETALLE"
        title={String(
          (detail?.alert as Row)?.title ?? "Detalle de investigación",
        )}
        description={id}
      >
        <div className="button-row">
          <Badge value={String(run?.status ?? "loading")} />
          <button
            className="secondary"
            aria-expanded={showFlow}
            onClick={() => setShowFlow((value) => !value)}
          >
            Cómo investigó
          </button>
        </div>
      </Heading>
      <ErrorBox error={error} />
      {!detail ? (
        <p role="status">Cargando detalle…</p>
      ) : (
        <>
          <div className="detail-grid">
            <section className="panel timeline">
              <div className="section-head">
                <h2>Timeline</h2>
                <span className="live-label">
                  <span className="live-dot" />
                  {connection}
                </span>
              </div>
              {events.length ? (
                <ol>
                  {events.map((e, i) => (
                    <li key={String(e.id ?? i)}>
                      <span className="timeline-dot" />
                      <strong>
                        {String(
                          (e.data as Row)?.stage ??
                            (e.data as Row)?.status ??
                            e.type,
                        )}
                      </strong>
                      <small>{String(e.ts ?? "")}</small>
                      <details>
                        <summary>Datos del evento</summary>
                        <pre>{stringify(e.data)}</pre>
                      </details>
                    </li>
                  ))}
                </ol>
              ) : (
                <Empty>
                  Esperando los primeros eventos de la investigación.
                </Empty>
              )}
              <button
                className="secondary"
                onClick={() => setVersion((v) => v + 1)}
              >
                Reconectar timeline
              </button>
              {allowed(session.role, "operator") &&
                ["interrupted", "failed", "recovering"].includes(
                  String(run?.status),
                ) && (
                  <button
                    onClick={async () => {
                      try {
                        await session.api.request(
                          `/threads/${encodeURIComponent(id)}/resume`,
                          "POST",
                        );
                        setVersion((v) => v + 1);
                      } catch (e) {
                        setError(message(e));
                      }
                    }}
                  >
                    Reanudar investigación
                  </button>
                )}
            </section>
            <div>
              <Overview detail={detail} />
            </div>
          </div>
          {showFlow && (
            <>
              <div className="button-row">
                <button
                  className="secondary"
                  onClick={() => setFlowVersion((value) => value + 1)}
                  disabled={flowLoading}
                >
                  <RefreshCw size={15} /> Actualizar explicación
                </button>
              </div>
              <ErrorBox error={flowError} />
              {flowLoading && !explanation && (
                <p role="status">Cargando explicación…</p>
              )}
              {explanation && <AgentFlow data={explanation} />}
            </>
          )}
          <InvestigationContent
            detail={detail}
            onRemediate={
              allowed(session.role, "operator") ? remediate : undefined
            }
          />
          {Array.isArray(detail.errors) && detail.errors.length > 0 && (
            <DataBlock
              title="Errores de la investigación"
              value={detail.errors}
            />
          )}
        </>
      )}
    </>
  );
}
function ThreadApproval({
  session,
  thread,
  kind,
  update,
  reload,
  setBusy,
}: {
  session: Session;
  thread: Thread;
  kind: "chat" | "remediations";
  update: (t: Thread) => void;
  reload: () => Promise<void>;
  setBusy: (busy: boolean) => void;
}) {
  return (
    <ApprovalCard
      key={thread.approval_ids?.join("|")}
      requests={thread.requests ?? []}
      ids={thread.approval_ids ?? []}
      canApprove={allowed(session.role, "approver")}
      onStale={async () => {
        setBusy(true);
        try {
          await reload();
        } finally {
          setBusy(false);
        }
      }}
      onSubmit={async (body) => {
        setBusy(true);
        try {
          const result = await session.api.request<Thread>(
            `/${kind === "chat" ? "chat/threads" : "remediations"}/${encodeURIComponent(thread.thread_id)}/approve`,
            "POST",
            body,
          );
          update(result);
          await reload();
        } finally {
          setBusy(false);
        }
      }}
    />
  );
}
function Chat({ session }: { session: Session }) {
  const [id, setId] = useState("");
  const [thread, setThread] = useState<Thread | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      abort.current?.abort();
    };
  }, []);
  async function reload(threadId = thread?.thread_id) {
    if (!threadId) return;
    const data = await session.api.request<Thread>(
      `/chat/threads/${encodeURIComponent(threadId)}`,
    );
    if (alive.current) {
      setThread(data);
      setId(threadId);
    }
  }
  async function open(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      await reload(id);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const threadId = thread?.thread_id ?? crypto.randomUUID();
    const submitted = text;
    setId(threadId);
    setText("");
    setThread((old) => ({
      ...old,
      thread_id: threadId,
      status: "running",
      requests: [],
      approval_ids: [],
      messages: [...(old?.messages ?? []), { role: "user", text: submitted }],
    }));
    const c = new AbortController();
    abort.current = c;
    try {
      await session.api.stream(
        `/chat/threads/${encodeURIComponent(threadId)}/messages`,
        (event) => {
          if (!alive.current) return;
          const data = JSON.parse(event.data) as Row;
          if (event.event === "message")
            setThread((old) =>
              old
                ? {
                    ...old,
                    messages: [
                      ...(old.messages ?? []),
                      { role: "assistant", text: String(data.text) },
                    ],
                  }
                : old,
            );
          if (event.event === "approval")
            setThread((old) =>
              old
                ? {
                    ...old,
                    status: "awaiting_approval",
                    requests: data.requests as ApprovalRequest[],
                    approval_ids: data.approval_ids as string[],
                  }
                : old,
            );
          if (event.event === "error") throw new Error(String(data.error));
        },
        { body: { message: submitted }, signal: c.signal },
      );
      if (alive.current) await reload(threadId);
    } catch (e) {
      if (alive.current && !c.signal.aborted) setError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <>
      <Heading
        eyebrow="ASISTENTE / CONVERSACIÓN"
        title="Chat operacional"
        description="Explora la evidencia y consulta al asistente desde un hilo persistido."
      />
      {!allowed(session.role, "operator") ? (
        <div className="notice">
          El chat requiere el rol operator o superior.
        </div>
      ) : (
        <>
          <form className="panel inline-form" onSubmit={open}>
            <label>
              ID del hilo
              <input
                required
                maxLength={64}
                value={id}
                onChange={(e) => setId(e.target.value)}
              />
            </label>
            <button className="secondary" disabled={busy}>
              Abrir hilo de chat
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => {
                setThread(null);
                setId("");
                setError("");
              }}
            >
              Nuevo chat
            </button>
          </form>
          <ErrorBox error={error} />
          <section
            className="panel conversation"
            aria-label="Historial de chat"
            aria-live="polite"
          >
            {thread?.messages?.length ? (
              thread.messages.map((m, i) => (
                <article key={i} className={`bubble ${m.role}`}>
                  <small>{m.role === "user" ? "Tú" : "Fin Graph"}</small>
                  <p className="prewrap">{m.text}</p>
                </article>
              ))
            ) : (
              <Empty>
                Inicia una conversación o recupera un hilo por su ID.
              </Empty>
            )}
            {busy && <p role="status">El asistente está trabajando…</p>}
          </section>
          {thread && (
            <>
              <div className="thread-meta">
                Hilo: <code>{thread.thread_id}</code>
                <Badge value={thread.status} />
              </div>
              <ThreadApproval
                session={session}
                thread={thread}
                kind="chat"
                update={setThread}
                reload={() => reload()}
                setBusy={setBusy}
              />
            </>
          )}
          <form className="panel composer" onSubmit={send}>
            <label>
              Mensaje
              <textarea
                required
                maxLength={16000}
                value={text}
                disabled={busy || !!thread?.requests?.length}
                onChange={(e) => setText(e.target.value)}
                placeholder="¿Qué necesitas investigar?"
              />
            </label>
            <button disabled={busy || !!thread?.requests?.length}>
              Enviar mensaje <ArrowUpRight size={16} />
            </button>
          </form>
        </>
      )}
    </>
  );
}
type Schema = {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  properties?: Record<string, Schema>;
  required?: string[];
};
type Action = { name: string; schema: Schema; rollback: unknown };
function Remediations({ session }: { session: Session }) {
  const [catalog, setCatalog] = useState<Action[]>([]);
  const [action, setAction] = useState("");
  const [args, setArgs] = useState<Record<string, unknown>>({});
  const [thread, setThread] = useState<Thread | null>(null);
  const [id, setId] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let alive = true;
    if (allowed(session.role, "operator")) {
      setLoading(true);
      session.api
        .request<Action[]>("/remediations/actions")
        .then((data) => {
          if (alive) {
            setCatalog(data);
            if (data.length) selectAction(data[0]);
          }
        })
        .catch((e) => {
          if (alive) setError(message(e));
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }
    return () => {
      alive = false;
    };
  }, [session]);
  function selectAction(a: Action) {
    setAction(a.name);
    setArgs(
      Object.fromEntries(
        Object.entries(a.schema.properties ?? {})
          .filter(([, s]) => s.default !== undefined)
          .map(([k, s]) => [k, s.default]),
      ),
    );
  }
  async function reload(threadId = thread?.thread_id) {
    if (!threadId) return;
    const data = await session.api.request<Thread>(
      `/remediations/${encodeURIComponent(threadId)}`,
    );
    setThread(data);
    setId(threadId);
  }
  async function prepare(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const data = await session.api.request<Thread>("/remediations", "POST", {
        tool: action,
        args,
      });
      setThread(data);
      setId(data.thread_id);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const selected = catalog.find((a) => a.name === action);
  return (
    <>
      <Heading
        eyebrow="SEGURIDAD / CONTROL DE CAMBIOS"
        title="Remediaciones"
        description="Prepara una acción, revisa su impacto y decide con evidencia."
      />
      <form
        className="panel inline-form"
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setError("");
          setBusy(true);
          try {
            await reload(id);
          } catch (e) {
            setError(message(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          ID de remediación
          <input
            required
            maxLength={64}
            value={id}
            onChange={(e) => setId(e.target.value)}
          />
        </label>
        <button className="secondary" disabled={busy}>
          Abrir remediación
        </button>
      </form>
      <ErrorBox error={error} />
      {allowed(session.role, "operator") ? (
        <form className="panel" onSubmit={prepare}>
          <div className="section-head">
            <h2>Preparar acción</h2>
            <span className="badge">Preparación sin ejecución</span>
          </div>
          {loading ? (
            <p role="status">Cargando catálogo…</p>
          ) : !catalog.length ? (
            <Empty>No hay acciones registradas disponibles.</Empty>
          ) : (
            <>
              <label>
                Acción
                <select
                  value={action}
                  onChange={(e) => {
                    const a = catalog.find((a) => a.name === e.target.value);
                    if (a) selectAction(a);
                  }}
                >
                  {catalog.map((a) => (
                    <option key={a.name}>{a.name}</option>
                  ))}
                </select>
              </label>
              <div className="fields">
                {Object.entries(selected?.schema.properties ?? {}).map(
                  ([name, schema]) => (
                    <label key={action + name}>
                      {name}
                      {schema.description && (
                        <small>{schema.description}</small>
                      )}
                      {schema.enum ? (
                        <select
                          aria-label={name}
                          required={selected?.schema.required?.includes(name)}
                          value={String(args[name] ?? "")}
                          onChange={(e) =>
                            setArgs({
                              ...args,
                              [name]:
                                e.target.value === ""
                                  ? undefined
                                  : schema.type === "integer" ||
                                      schema.type === "number"
                                    ? Number(e.target.value)
                                    : e.target.value,
                            })
                          }
                        >
                          <option value="">Seleccionar…</option>
                          {schema.enum.map((v) => (
                            <option key={String(v)} value={String(v)}>
                              {String(v)}
                            </option>
                          ))}
                        </select>
                      ) : schema.type === "boolean" ? (
                        <input
                          aria-label={name}
                          type="checkbox"
                          checked={Boolean(args[name])}
                          onChange={(e) =>
                            setArgs({ ...args, [name]: e.target.checked })
                          }
                        />
                      ) : ["string", "integer", "number"].includes(
                          schema.type ?? "string",
                        ) ? (
                        <input
                          aria-label={name}
                          required={selected?.schema.required?.includes(name)}
                          type={
                            schema.type === "integer" ||
                            schema.type === "number"
                              ? "number"
                              : "text"
                          }
                          step={schema.type === "integer" ? 1 : "any"}
                          min={schema.minimum}
                          max={schema.maximum}
                          minLength={schema.minLength}
                          maxLength={schema.maxLength}
                          pattern={schema.pattern}
                          value={String(args[name] ?? "")}
                          onChange={(e) =>
                            setArgs({
                              ...args,
                              [name]:
                                e.target.value === ""
                                  ? undefined
                                  : schema.type === "integer" ||
                                      schema.type === "number"
                                    ? Number(e.target.value)
                                    : e.target.value,
                            })
                          }
                        />
                      ) : (
                        <textarea
                          aria-label={name}
                          required={selected?.schema.required?.includes(name)}
                          placeholder="Objeto o lista JSON"
                          onChange={(e) => {
                            try {
                              setArgs({
                                ...args,
                                [name]: JSON.parse(e.target.value),
                              });
                              e.target.setCustomValidity("");
                            } catch {
                              e.target.setCustomValidity(
                                "Introduce JSON válido",
                              );
                            }
                          }}
                        />
                      )}
                    </label>
                  ),
                )}
              </div>
              <p className="muted">
                Rollback registrado: {selected?.rollback ? "Sí" : "No"}. La
                preparación puede consultar el estado actual de la integración.
              </p>
              <button disabled={busy}>
                {busy ? "Preparando…" : "Preparar acción"}
                <ShieldCheck size={16} />
              </button>
            </>
          )}
        </form>
      ) : (
        <div className="notice">
          Tu rol permite revisar remediaciones existentes. Preparar acciones
          requiere operator.
        </div>
      )}
      {thread && (
        <>
          <section className="panel">
            <div className="section-head">
              <h2>Estado de remediación</h2>
              <Badge value={thread.status} />
            </div>
            <code>{thread.thread_id}</code>
            {thread.outcome && (
              <p>
                Resultado: <strong>{thread.outcome}</strong>
              </p>
            )}
          </section>
          <ThreadApproval
            session={session}
            thread={thread}
            kind="remediations"
            update={setThread}
            reload={() => reload()}
            setBusy={setBusy}
          />
          {thread.result !== undefined && thread.result !== null && (
            <DataBlock title="Resultado de ejecución" value={thread.result} />
          )}{" "}
          {thread.rollback !== undefined && thread.rollback !== null && (
            <DataBlock title="Rollback" value={thread.rollback} />
          )}
        </>
      )}
    </>
  );
}
type Schedule = { id: string; definition: Row; enabled: boolean };
function Schedules({ session }: { session: Session }) {
  const [rows, setRows] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [id, setId] = useState("");
  const [kind, setKind] = useState("health_check");
  const [enabled, setEnabled] = useState(false);
  const [interval, setInterval] = useState(5);
  const [checks, setChecks] = useState<Row[]>([
    { id: "check-1", expression: "up", operator: "lt", threshold: 1 },
  ]);
  const [hour, setHour] = useState(9);
  const [minute, setMinute] = useState(0);
  const [zone, setZone] = useState("America/Bogota");
  const [slack, setSlack] = useState("");
  const [telegram, setTelegram] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    setLoading(true);
    try {
      setRows(await session.api.request<Schedule[]>("/schedules"));
    } catch (e) {
      setError(message(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, [session]);
  function edit(s: Schedule) {
    setEditing(true);
    setId(s.id);
    setKind(String(s.definition.kind));
    setEnabled(s.enabled);
    setInterval(Number(s.definition.interval_minutes ?? 5));
    setChecks(
      (s.definition.checks as Row[]) ?? [
        { id: "check-1", expression: "up", operator: "lt", threshold: 1 },
      ],
    );
    setHour(Number(s.definition.hour ?? 9));
    setMinute(Number(s.definition.minute ?? 0));
    setZone(String(s.definition.timezone ?? "America/Bogota"));
    const destinations = s.definition.destinations as Row;
    setSlack(String(destinations?.slack ?? ""));
    setTelegram(String(destinations?.telegram ?? ""));
  }
  async function operation(fn: () => Promise<unknown>) {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    const destinations = {
      ...(slack ? { slack } : {}),
      ...(telegram ? { telegram } : {}),
    };
    const definition =
      kind === "health_check"
        ? { kind, interval_minutes: interval, checks, destinations }
        : { kind, hour, minute, timezone: zone, destinations };
    await operation(() =>
      session.api.request(
        editing ? `/schedules/${encodeURIComponent(id)}` : "/schedules",
        editing ? "PUT" : "POST",
        { ...(editing ? {} : { id }), definition, enabled },
      ),
    );
  }
  return (
    <>
      <Heading
        eyebrow="OPERACIONES / PROGRAMACIÓN"
        title="Automatizaciones"
        description="Comprobaciones periódicas y resúmenes, con horarios explícitos."
      />
      <ErrorBox error={error} />
      {notice && (
        <div role="status" className="notice">
          {notice}
        </div>
      )}
      <section className="panel">
        <h2>Programaciones</h2>
        {loading ? (
          <p role="status">Cargando programaciones…</p>
        ) : !rows.length ? (
          <Empty>No hay automatizaciones configuradas.</Empty>
        ) : (
          <div className="schedule-list">
            {rows.map((s) => (
              <article key={s.id}>
                <div>
                  <h3>{s.id}</h3>
                  <p>
                    {s.definition.kind === "health_check"
                      ? `Health check · cada ${s.definition.interval_minutes} minutos`
                      : `Digest · ${String(s.definition.hour).padStart(2, "0")}:${String(s.definition.minute).padStart(2, "0")} · ${s.definition.timezone}`}
                  </p>
                  <Badge value={s.enabled ? "Habilitada" : "Deshabilitada"} />
                </div>
                <div className="button-row">
                  {allowed(session.role, "operator") && (
                    <button
                      className="secondary"
                      disabled={busy || !s.enabled}
                      onClick={() =>
                        operation(async () => {
                          await session.api.request(
                            `/schedules/${encodeURIComponent(s.id)}/run`,
                            "POST",
                          );
                          setNotice(`Ejecución completada: ${s.id}`);
                        })
                      }
                    >
                      Ejecutar {s.id}
                    </button>
                  )}
                  {allowed(session.role, "admin") && (
                    <>
                      <button className="secondary" onClick={() => edit(s)}>
                        Editar {s.id}
                      </button>
                      <button
                        className="secondary"
                        disabled={busy}
                        onClick={() =>
                          operation(() =>
                            session.api.request(
                              `/schedules/${encodeURIComponent(s.id)}`,
                              "PUT",
                              { definition: s.definition, enabled: !s.enabled },
                            ),
                          )
                        }
                      >
                        {s.enabled ? "Deshabilitar" : "Habilitar"} {s.id}
                      </button>
                      <button
                        className="danger"
                        disabled={busy}
                        onClick={() => {
                          if (
                            window.confirm(`¿Eliminar la programación ${s.id}?`)
                          )
                            void operation(() =>
                              session.api.request(
                                `/schedules/${encodeURIComponent(s.id)}`,
                                "DELETE",
                              ),
                            );
                        }}
                      >
                        Eliminar {s.id}
                      </button>
                    </>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      {allowed(session.role, "admin") ? (
        <form className="panel" onSubmit={save}>
          <div className="section-head">
            <h2>{editing ? "Editar programación" : "Nueva programación"}</h2>
            {editing && (
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  setEditing(false);
                  setId("");
                }}
              >
                Crear nueva
              </button>
            )}
          </div>
          <div className="fields">
            <label>
              ID de programación
              <input
                required
                disabled={editing}
                maxLength={64}
                pattern="[A-Za-z0-9_-]+"
                value={id}
                onChange={(e) => setId(e.target.value)}
              />
            </label>
            <label>
              Tipo de programación
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="health_check">Health check</option>
                <option value="digest">Digest</option>
              </select>
            </label>
          </div>
          {kind === "health_check" ? (
            <>
              <label>
                Intervalo en minutos
                <input
                  type="number"
                  min={1}
                  max={1440}
                  required
                  value={interval}
                  onChange={(e) => setInterval(Number(e.target.value))}
                />
              </label>
              {checks.map((check, i) => (
                <fieldset key={i}>
                  <legend>Comprobación {i + 1}</legend>
                  <div className="fields">
                    <label>
                      ID de comprobación {i + 1}
                      <input
                        required
                        maxLength={64}
                        value={String(check.id)}
                        onChange={(e) =>
                          setChecks(
                            checks.map((c, j) =>
                              j === i ? { ...c, id: e.target.value } : c,
                            ),
                          )
                        }
                      />
                    </label>
                    <label>
                      Expresión {i + 1}
                      <input
                        required
                        maxLength={1000}
                        value={String(check.expression)}
                        onChange={(e) =>
                          setChecks(
                            checks.map((c, j) =>
                              j === i
                                ? { ...c, expression: e.target.value }
                                : c,
                            ),
                          )
                        }
                      />
                    </label>
                    <label>
                      Operador {i + 1}
                      <select
                        value={String(check.operator)}
                        onChange={(e) =>
                          setChecks(
                            checks.map((c, j) =>
                              j === i ? { ...c, operator: e.target.value } : c,
                            ),
                          )
                        }
                      >
                        {["gt", "gte", "lt", "lte"].map((v) => (
                          <option key={v}>{v}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Umbral {i + 1}
                      <input
                        required
                        type="number"
                        step="any"
                        value={Number(check.threshold)}
                        onChange={(e) =>
                          setChecks(
                            checks.map((c, j) =>
                              j === i
                                ? { ...c, threshold: Number(e.target.value) }
                                : c,
                            ),
                          )
                        }
                      />
                    </label>
                  </div>
                  {checks.length > 1 && (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() =>
                        setChecks(checks.filter((_, j) => j !== i))
                      }
                    >
                      Quitar comprobación {i + 1}
                    </button>
                  )}
                </fieldset>
              ))}
              <button
                type="button"
                className="secondary"
                disabled={checks.length >= 20}
                onClick={() =>
                  setChecks([
                    ...checks,
                    {
                      id: `check-${crypto.randomUUID().slice(0, 8)}`,
                      expression: "",
                      operator: "gt",
                      threshold: 0,
                    },
                  ])
                }
              >
                Añadir comprobación
              </button>
            </>
          ) : (
            <div className="fields">
              <label>
                Hora
                <input
                  type="number"
                  min={0}
                  max={23}
                  required
                  value={hour}
                  onChange={(e) => setHour(Number(e.target.value))}
                />
              </label>
              <label>
                Minuto
                <input
                  type="number"
                  min={0}
                  max={59}
                  required
                  value={minute}
                  onChange={(e) => setMinute(Number(e.target.value))}
                />
              </label>
              <label>
                Zona horaria
                <input
                  required
                  value={zone}
                  onChange={(e) => setZone(e.target.value)}
                />
              </label>
            </div>
          )}
          <div className="fields">
            <label>
              Destino Slack
              <input
                maxLength={64}
                value={slack}
                onChange={(e) => setSlack(e.target.value)}
                placeholder="Opcional"
              />
            </label>
            <label>
              Destino Telegram
              <input
                maxLength={64}
                value={telegram}
                onChange={(e) => setTelegram(e.target.value)}
                placeholder="Opcional"
              />
            </label>
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            Habilitada
          </label>
          <button disabled={busy}>
            {editing ? "Guardar programación" : "Crear programación"}
          </button>
        </form>
      ) : (
        <div className="notice">
          Crear, editar, habilitar y eliminar requiere admin. Ejecutar requiere
          operator.
        </div>
      )}
    </>
  );
}
function Integrations({ session }: { session: Session }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    session.api
      .request<Row[]>("/integrations")
      .then((d) => {
        if (alive) setRows(d);
      })
      .catch((e) => {
        if (alive) setError(message(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [session]);
  async function verify() {
    setBusy(true);
    setError("");
    try {
      setRows(await session.api.request<Row[]>("/integrations/verify", "POST"));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Heading
        eyebrow="ECOSISTEMA / CONECTIVIDAD"
        title="Integraciones"
        description="Visibilidad de las fuentes que respaldan tus investigaciones."
      >
        {allowed(session.role, "operator") && (
          <button onClick={verify} disabled={busy}>
            <RefreshCw size={16} />
            {busy ? "Verificando…" : "Verificar integraciones"}
          </button>
        )}
      </Heading>
      <ErrorBox error={error} />
      {loading ? (
        <p role="status">Cargando integraciones…</p>
      ) : !rows.length ? (
        <section className="panel">
          <Empty>No hay integraciones registradas.</Empty>
        </section>
      ) : (
        <div className="integration-grid">
          {rows.map((r) => (
            <article className="panel integration" key={String(r.id)}>
              <span className="integration-icon">
                <Plug size={23} />
              </span>
              <h2>{String(r.id)}</h2>
              <Badge value={String(r.status)} />
              {!!r.message && <p>{String(r.message)}</p>}
            </article>
          ))}
        </div>
      )}
      <div className="notice">
        <ShieldCheck size={18} />
        Las credenciales se administran en el servidor. Esta consola muestra
        únicamente el estado y la verificación.
      </div>
    </>
  );
}
