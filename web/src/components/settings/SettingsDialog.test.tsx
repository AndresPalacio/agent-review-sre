import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../../lib/api-client";
import { SettingsDialog } from "./SettingsDialog";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("loads models and saves provider keys without retaining or rendering the secret", async () => {
  const requests: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      const path = String(url);
      requests.push([path, init]);
      if (path === "/language-models/")
        return new Response(
          JSON.stringify({
            models: [
              {
                display_name: "GPT 4.1",
                model_name: "gpt-4.1",
                provider: "OpenAI",
              },
            ],
          }),
        );
      if (path === "/api-keys/" && (init?.method ?? "GET") === "GET")
        return new Response("[]");
      if (path === "/api-keys/" && init?.method === "POST")
        return new Response(
          JSON.stringify({
            id: 1,
            provider: "OpenAI",
            key_value: "should-not-render",
            is_active: true,
            has_key: true,
            created_at: "now",
          }),
        );
      if (path === "/ollama/status")
        return new Response(
          JSON.stringify({
            installed: true,
            running: false,
            available_models: [],
            server_url: "http://ollama.internal",
          }),
        );
      return new Response("not found", { status: 404 });
    }),
  );
  const storage = vi.spyOn(Storage.prototype, "setItem");
  const logged = vi.spyOn(console, "log").mockImplementation(() => undefined);
  render(
    <SettingsDialog
      open
      onOpenChange={() => undefined}
      client={new ApiClient()}
    />,
  );
  expect(await screen.findByText("GPT 4.1")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Claves" }));
  fireEvent.change(screen.getByLabelText("Proveedor"), {
    target: { value: "OpenAI" },
  });
  fireEvent.change(screen.getByLabelText("Clave del proveedor"), {
    target: { value: "sk-super-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Guardar clave" }));
  await waitFor(() =>
    expect(
      requests.some(
        ([path, init]) => path === "/api-keys/" && init?.method === "POST",
      ),
    ).toBe(true),
  );
  expect(screen.getByLabelText("Clave del proveedor")).toHaveValue("");
  expect(document.body.textContent).not.toContain("sk-super-secret");
  expect(document.body.textContent).not.toContain("should-not-render");
  expect(location.href).not.toContain("sk-super-secret");
  expect(storage).not.toHaveBeenCalledWith(
    expect.anything(),
    expect.stringContaining("sk-super-secret"),
  );
  expect(logged).not.toHaveBeenCalled();
});

it("controls Ollama and clears native access input after authentication", async () => {
  const session = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      const path = String(url);
      if (path === "/language-models/")
        return new Response(JSON.stringify({ models: [] }));
      if (path === "/api-keys/") return new Response("[]");
      if (path === "/ollama/status")
        return new Response(
          JSON.stringify({
            installed: true,
            running: false,
            available_models: ["qwen3:8b"],
            server_url: "local",
          }),
        );
      if (path === "/ollama/start")
        return new Response(
          JSON.stringify({ success: true, message: "started" }),
        );
      if (path === "/v1/session") {
        expect(init?.headers).toMatchObject({ "X-API-Key": "native-secret" });
        session();
        return new Response(JSON.stringify({ role: "approver" }));
      }
      return new Response("not found", { status: 404 });
    }),
  );
  render(
    <SettingsDialog
      open
      onOpenChange={() => undefined}
      client={new ApiClient()}
    />,
  );
  fireEvent.click(screen.getByRole("tab", { name: "Ollama" }));
  await screen.findByText("qwen3:8b");
  fireEvent.click(screen.getByRole("button", { name: "Iniciar Ollama" }));
  await screen.findByText("started");
  fireEvent.click(screen.getByRole("tab", { name: "Acceso nativo" }));
  fireEvent.change(screen.getByLabelText("Clave nativa"), {
    target: { value: "native-secret" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Conectar sesión nativa" }),
  );
  await waitFor(() => expect(session).toHaveBeenCalled());
  expect(screen.getByLabelText("Clave nativa")).toHaveValue("");
  expect(document.body).not.toHaveTextContent("native-secret");
});
