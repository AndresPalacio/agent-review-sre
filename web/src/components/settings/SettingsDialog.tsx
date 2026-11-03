import * as Tabs from "@radix-ui/react-tabs";
import { KeyRound, Laptop, Server, Settings2, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ApiClient, apiClient } from "../../lib/api-client";
import { CloudModels } from "./CloudModels";
import { OllamaSettings } from "./OllamaSettings";
import { ProviderKeys } from "./ProviderKeys";

export interface NativeSession {
  role: string;
  connected: boolean;
}

function NativeAccess({
  client,
  onSession,
}: {
  client: ApiClient;
  onSession?: (session: NativeSession) => void;
}) {
  const [key, setKey] = useState("");
  const [role, setRole] = useState("");
  const [message, setMessage] = useState("");
  async function connect(event: FormEvent) {
    event.preventDefault();
    const value = key;
    if (!value) return;
    client.setNativeApiKey(value);
    setKey("");
    try {
      const session = await client.native.session();
      setRole(session.role);
      setMessage("Sesión nativa conectada");
      onSession?.({ role: session.role, connected: true });
    } catch {
      client.clearSecrets();
      setRole("");
      setMessage("La clave nativa no fue aceptada");
      onSession?.({ role: "", connected: false });
    }
  }
  return (
    <section className="settings-section">
      <h3>Acceso nativo `/v1`</h3>
      <p>
        Sesión separada para cartera de papel, órdenes y aprobaciones. La clave
        permanece únicamente en memoria.
      </p>
      <form className="native-access" onSubmit={(event) => void connect(event)}>
        <input
          aria-label="Clave nativa"
          type="password"
          autoComplete="off"
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
        <button disabled={!key}>Conectar sesión nativa</button>
      </form>
      {role && (
        <p>
          Rol conectado: <strong>{role}</strong>
        </p>
      )}
      {message && (
        <p role="status" className="settings-message">
          {message}
        </p>
      )}
    </section>
  );
}

interface SettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  client?: ApiClient;
  onNativeSession?: (session: NativeSession) => void;
}

export function SettingsDialog({
  open,
  onOpenChange,
  client = apiClient,
  onNativeSession,
}: SettingsDialogProps) {
  const [activeTab, setActiveTab] = useState("models");
  if (!open) return null;
  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onOpenChange(false);
      }}
    >
      <section
        className="settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Configuración"
      >
        <header>
          <div>
            <Settings2 size={18} />
            <span>
              <strong>Configuración</strong>
              <small>Modelos, proveedores y acceso nativo</small>
            </span>
          </div>
          <button
            aria-label="Cerrar configuración"
            onClick={() => onOpenChange(false)}
          >
            <X size={17} />
          </button>
        </header>
        <Tabs.Root
          className="settings-tabs"
          value={activeTab}
          onValueChange={setActiveTab}
        >
          <Tabs.List aria-label="Secciones de configuración">
            <Tabs.Trigger value="models" onClick={() => setActiveTab("models")}>
              <Laptop size={14} /> Modelos
            </Tabs.Trigger>
            <Tabs.Trigger value="ollama" onClick={() => setActiveTab("ollama")}>
              <Server size={14} /> Ollama
            </Tabs.Trigger>
            <Tabs.Trigger value="keys" onClick={() => setActiveTab("keys")}>
              <KeyRound size={14} /> Claves
            </Tabs.Trigger>
            <Tabs.Trigger value="native" onClick={() => setActiveTab("native")}>
              <Settings2 size={14} /> Acceso nativo
            </Tabs.Trigger>
          </Tabs.List>
          <Tabs.Content value="models">
            <CloudModels client={client} />
          </Tabs.Content>
          <Tabs.Content value="ollama">
            <OllamaSettings client={client} />
          </Tabs.Content>
          <Tabs.Content value="keys">
            <ProviderKeys client={client} />
          </Tabs.Content>
          <Tabs.Content value="native">
            <NativeAccess client={client} onSession={onNativeSession} />
          </Tabs.Content>
        </Tabs.Root>
      </section>
    </div>
  );
}
