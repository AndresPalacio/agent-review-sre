import { StrictMode } from "react";
import { it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { App } from "./App";
afterEach(() => vi.unstubAllGlobals());
it("authenticates with headers only, gates viewer actions and never persists the key", async () => {
  const fetcher = vi.fn(async (url: unknown, options?: RequestInit) => {
    expect(options?.headers).toMatchObject({ "X-API-Key": "viewer-secret" });
    expect(String(url)).not.toContain("viewer-secret");
    return new Response(
      JSON.stringify(
        String(url).endsWith("/session") ? { role: "viewer" } : [],
      ),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetcher);
  const local = vi.spyOn(Storage.prototype, "setItem");
  render(<App />);
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "viewer-secret" },
  });
  fireEvent.click(screen.getByText("Iniciar sesión"));
  await screen.findByText("Rol: viewer");
  expect(screen.queryByText("Texto de la alerta")).not.toBeInTheDocument();
  expect(local).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Cerrar sesión"));
  expect(screen.getByLabelText("API key")).toHaveValue("");
  local.mockRestore();
});
it("sends and recovers a persisted chat under StrictMode", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const path = String(url);
      if (path.endsWith("/session"))
        return new Response(JSON.stringify({ role: "admin" }));
      if (path.endsWith("/threads")) return new Response("[]");
      if (path.endsWith("/messages"))
        return new Response(
          new ReadableStream({
            start(c) {
              c.enqueue(
                new TextEncoder().encode(
                  'event: message\ndata: {"text":"Respuesta pública"}\n\nevent: done\ndata: {}\n\n',
                ),
              );
              c.close();
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        );
      return new Response(
        JSON.stringify({
          thread_id: "restored",
          status: "completed",
          requests: [],
          approval_ids: [],
          messages: [
            { role: "user", text: "Pregunta" },
            { role: "assistant", text: "Respuesta pública" },
          ],
        }),
      );
    }),
  );
  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "admin" },
  });
  fireEvent.click(screen.getByText("Iniciar sesión"));
  await screen.findByText("Rol: admin");
  fireEvent.click(screen.getByRole("button", { name: "Chat" }));
  fireEvent.change(screen.getByLabelText("Mensaje"), {
    target: { value: "Pregunta" },
  });
  fireEvent.click(screen.getByText("Enviar mensaje"));
  await screen.findByText("Respuesta pública");
  await waitFor(() =>
    expect(screen.getByText("Enviar mensaje")).not.toBeDisabled(),
  );
  expect(screen.getByText("restored")).toBeInTheDocument();
});
it("keeps health-check IDs unique after removing and adding a check", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: unknown) =>
        new Response(
          JSON.stringify(
            String(url).endsWith("/session") ? { role: "admin" } : [],
          ),
        ),
    ),
  );
  render(<App />);
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "admin" },
  });
  fireEvent.click(screen.getByText("Iniciar sesión"));
  await screen.findByText("Rol: admin");
  fireEvent.click(screen.getByRole("button", { name: "Automatizaciones" }));
  await screen.findByText("Nueva programación");
  fireEvent.click(screen.getByText("Añadir comprobación"));
  fireEvent.click(screen.getByText("Quitar comprobación 1"));
  fireEvent.click(screen.getByText("Añadir comprobación"));
  const first = screen.getByLabelText(
    "ID de comprobación 1",
  ) as HTMLInputElement;
  const second = screen.getByLabelText(
    "ID de comprobación 2",
  ) as HTMLInputElement;
  expect(first.value).not.toBe(second.value);
});

it("shows the agent architecture and observed investigation path", async () => {
  const fetcher = vi.fn(async (url: unknown) => {
    const path = String(url);
    if (path.endsWith("/session"))
      return new Response(JSON.stringify({ role: "viewer" }));
    if (path.endsWith("/threads"))
      return new Response(
        JSON.stringify([{ thread_id: "case-1", status: "completed" }]),
      );
    if (path.endsWith("/investigations/case-1/explain"))
      return new Response(
        JSON.stringify({
          thread_id: "case-1",
          run: { status: "completed" },
          topology: {
            nodes: [
              { id: "plan", label: "Planificar", kind: "agent" },
              { id: "specialist", label: "Especialistas", kind: "agent" },
              { id: "verify", label: "Verificar", kind: "gate" },
            ],
            edges: [
              { source: "plan", target: "specialist" },
              { source: "specialist", target: "verify" },
            ],
          },
          execution: {
            steps: [
              {
                id: "step-1",
                node: "specialist",
                label: "metrics",
                type: "specialist",
                ts: "2026-10-08T12:00:00Z",
                domain: "metrics",
                wave: 1,
                evidence_ids: ["ev-1"],
                provenance: "observed",
              },
            ],
            waves: [
              {
                number: 1,
                provenance: "observed",
                tasks: [{ domain: "metrics", provenance: "observed" }],
                specialists: [
                  {
                    domain: "metrics",
                    evidence_ids: ["ev-1"],
                    provenance: "observed",
                  },
                ],
              },
            ],
            findings: [
              {
                domain: "metrics",
                wave: 1,
                summary: "La latencia subió",
                evidence_ids: ["ev-1"],
                provenance: "derived",
              },
            ],
            evidence: [
              {
                id: "ev-1",
                integration: "datadog",
                tool: "query_metrics",
                summary: "p95 elevada",
                provenance: "derived",
              },
            ],
            verification: {
              passed: true,
              score: 0.9,
              claims: [],
              missing_sections: [],
              provenance: "derived",
            },
            diagnosis: { root_cause: "Saturación", provenance: "derived" },
          },
        }),
      );
    if (path.endsWith("/investigations/case-1"))
      return new Response(
        JSON.stringify({
          run: { status: "completed" },
          alert: { title: "Latencia elevada" },
          evidence: [],
        }),
      );
    if (path.endsWith("/threads/case-1/history")) return new Response("[]");
    if (path.endsWith("/threads/case-1/stream"))
      return new Response(
        new ReadableStream({
          start(c) {
            c.close();
          },
        }),
      );
    return new Response("Not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "viewer" },
  });
  fireEvent.click(screen.getByText("Iniciar sesión"));
  fireEvent.click(await screen.findByRole("button", { name: "case-1" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Cómo investigó" }),
  );
  expect(await screen.findByText("La latencia subió")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Planificar" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Especialistas" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Verificar" })).toBeVisible();
  expect(screen.getByText("Especialista: metrics")).toBeVisible();
  expect(screen.getAllByText("Observado").length).toBeGreaterThan(0);
  expect(fetcher).toHaveBeenCalledWith(
    "/v1/investigations/case-1/explain",
    expect.objectContaining({
      headers: expect.objectContaining({ "X-API-Key": "viewer" }),
    }),
  );
});
