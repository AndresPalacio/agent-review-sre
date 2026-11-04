import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../../lib/api-client";
import { FlowProvider, useFlows } from "../../contexts/FlowContext";
import { TabsProvider, useTabs } from "../../contexts/TabsContext";
import { FlowSidebar } from "./FlowSidebar";
import { TabBar } from "../tabs/TabBar";
import { useKeyboardShortcuts } from "../../hooks/useKeyboardShortcuts";

const flow = (id: number, name = `Flow ${id}`) => ({
  id,
  name,
  description: null,
  nodes: [],
  edges: [],
  viewport: null,
  data: null,
  is_template: false,
  tags: null,
  created_at: "2026-11-03T00:00:00Z",
  updated_at: "2026-11-03T00:00:00Z",
});

function Workspace() {
  const { activeTab, updateDocument } = useTabs();
  const { saveFlow } = useFlows();
  return <>
    <FlowSidebar />
    <TabBar />
    <output aria-label="flujo activo">{activeTab?.flow.name ?? "ninguno"}</output>
    <button onClick={() => activeTab && updateDocument(activeTab.flow.id, { nodes: [{ id: "dirty", type: "unknown-node", position: { x: 0, y: 0 }, data: { name: "Dirty", status: "Idle" } }], edges: [] })}>Modificar</button>
    <button onClick={() => activeTab && void saveFlow(activeTab.flow.id)}>Guardar actual</button>
  </>;
}

function Harness({ client = new ApiClient() }: { client?: ApiClient }) {
  return <TabsProvider><FlowProvider client={client}><Workspace /></FlowProvider></TabsProvider>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("flow CRUD and tabs", () => {
  it("does not republish tab context for an identical document", () => {
    let renders = 0;
    function IdentityHarness() {
      const tabs = useTabs();
      renders += 1;
      return <>
        <output aria-label="identidad activa">{tabs.activeTab?.flow.name ?? "ninguno"}</output>
        <button onClick={() => tabs.openTab(flow(1))}>Abrir identidad</button>
        <button onClick={() => {
          if (tabs.activeTab) tabs.updateDocument(tabs.activeTab.flow.id, tabs.activeTab.document);
        }}>Republicar identidad</button>
      </>;
    }

    render(<TabsProvider><IdentityHarness /></TabsProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Abrir identidad" }));
    expect(screen.getByLabelText("identidad activa")).toHaveTextContent("Flow 1");
    const before = renders;

    fireEvent.click(screen.getByRole("button", { name: "Republicar identidad" }));

    expect(renders).toBe(before);
  });

  it("lists, creates, renames, duplicates and deletes with exact legacy requests", async () => {
    const requests: Array<[string, string, unknown]> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
      const path = String(url);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      requests.push([path, method, body]);
      if (path === "/flows/" && method === "GET") return new Response(JSON.stringify([flow(1, "Value")]))
      if (path === "/flows/" && method === "POST") return new Response(JSON.stringify(flow(2, body.name)));
      if (path === "/flows/1/duplicate?new_name=Value%20copy") return new Response(JSON.stringify(flow(3, "Value copy")));
      if (path === "/flows/1" && method === "PUT") return new Response(JSON.stringify(flow(1, body.name)));
      if (path === "/flows/1" && method === "DELETE") return new Response(JSON.stringify({ message: "ok" }));
      if (path === "/flows/1") return new Response(JSON.stringify(flow(1, "Value")));
      return new Response("not found", { status: 404 });
    }));

    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Abrir Value" }));
    expect(await screen.findByLabelText("flujo activo")).toHaveTextContent("Value");

    fireEvent.click(screen.getByRole("button", { name: "Crear flujo" }));
    fireEvent.change(screen.getByLabelText("Nombre"), { target: { value: "Momentum" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear" }));
    await screen.findByRole("button", { name: "Abrir Momentum" });

    fireEvent.click(screen.getByRole("button", { name: "Acciones de Value" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Renombrar Value" }));
    fireEvent.change(screen.getByLabelText("Nombre"), { target: { value: "Quality" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar nombre" }));
    await screen.findByRole("button", { name: "Abrir Quality" });

    fireEvent.click(screen.getByRole("button", { name: "Acciones de Quality" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Duplicar Quality" }));
    await waitFor(() => expect(requests.some(([path]) => path.includes("/duplicate"))).toBe(true));

    vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Acciones de Quality" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Eliminar Quality" }));
    await waitFor(() => expect(requests).toContainEqual(["/flows/1", "DELETE", undefined]));

    expect(requests).toContainEqual(["/flows/", "POST", expect.objectContaining({ name: "Momentum", nodes: [], edges: [] })]);
    expect(requests).toContainEqual(["/flows/1", "PUT", { name: "Quality" }]);
  });

  it("isolates an out-of-order response from the newly selected flow", async () => {
    let resolveFirst!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
      const path = String(url);
      if (path === "/flows/") return new Response(JSON.stringify([flow(1), flow(2)]));
      if (path === "/flows/1") return await new Promise<Response>((resolve) => { resolveFirst = resolve; });
      if (path === "/flows/2") return new Response(JSON.stringify(flow(2, "Current")));
      return new Response("", { status: 404 });
    }));
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Abrir Flow 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Abrir Flow 2" }));
    expect(await screen.findByLabelText("flujo activo")).toHaveTextContent("Current");
    await act(async () => resolveFirst(new Response(JSON.stringify(flow(1, "Stale")))));
    expect(screen.getByLabelText("flujo activo")).toHaveTextContent("Current");
    expect(screen.queryByText("Stale")).not.toBeInTheDocument();
  });

  it("marks edits dirty, refreshes after save, and confirms a dirty close", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
      const path = String(url);
      if (path === "/flows/") return new Response(JSON.stringify([flow(1)]));
      if (path === "/flows/1" && init?.method === "PUT") return new Response(JSON.stringify({ ...flow(1, "Saved"), updated_at: "2026-11-03T01:00:00Z" }));
      return new Response(JSON.stringify(flow(1)));
    }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Abrir Flow 1" }));
    await screen.findByRole("tab", { name: "Flow 1" });
    fireEvent.click(screen.getByText("Modificar"));
    expect(screen.getByRole("tab", { name: /Flow 1/ })).toHaveTextContent("●");
    fireEvent.click(screen.getByRole("button", { name: "Cerrar Flow 1" }));
    expect(screen.getByRole("tab", { name: /Flow 1/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cerrar Flow 1" }));
    expect(screen.queryByRole("tab", { name: /Flow 1/ })).not.toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(2);
  });
});

it("does not run save/delete shortcuts while an input or dialog owns focus", () => {
  const save = vi.fn();
  const remove = vi.fn();
  function ShortcutHarness() {
    useKeyboardShortcuts({ save, remove });
    return <input aria-label="editor" />;
  }
  render(<ShortcutHarness />);
  const input = screen.getByLabelText("editor");
  input.focus();
  fireEvent.keyDown(input, { key: "s", ctrlKey: true });
  fireEvent.keyDown(input, { key: "Delete" });
  expect(save).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
  input.blur();
  fireEvent.keyDown(window, { key: "s", ctrlKey: true });
  expect(save).toHaveBeenCalledOnce();
});
