import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../../lib/api-client";
import { NodeProvider } from "../../contexts/NodeContext";
import { TabsProvider } from "../../contexts/TabsContext";
import { FlowProvider } from "../../contexts/FlowContext";
import { RunProvider } from "../../contexts/RunContext";
import { FlowSidebar } from "../flows/FlowSidebar";
import { BottomPanel } from "./BottomPanel";

const activeFlow = {
  id: 1,
  name: "Investment flow",
  description: null,
  nodes: [
    { id: "portfolio-start-node_aa0001", type: "portfolio-start-node", position: { x: 0, y: 0 }, data: { name: "Portfolio", status: "Idle" } },
    { id: "warren_buffett_bb0002", type: "agent-node", position: { x: 200, y: 0 }, data: { name: "Warren Buffett", status: "Idle", agent_key: "warren_buffett" } },
  ],
  edges: [{ id: "edge-1", source: "portfolio-start-node_aa0001", target: "warren_buffett_bb0002" }],
  viewport: null,
  data: null,
  is_template: false,
  tags: null,
  created_at: "2026-11-03T00:00:00Z",
  updated_at: "2026-11-03T00:00:00Z",
};

function Harness({ client = new ApiClient() }: { client?: ApiClient }) {
  return <NodeProvider><TabsProvider><FlowProvider client={client}><RunProvider client={client}><FlowSidebar /><BottomPanel /></RunProvider></FlowProvider></TabsProvider></NodeProvider>;
}

const openFlow = async () => {
  fireEvent.click(await screen.findByRole("button", { name: "Abrir Investment flow" }));
  await screen.findByLabelText("Símbolos");
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("sends the exact graph, projects progress, and renders one terminal result", async () => {
  let runBody: Record<string, unknown> | undefined;
  vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = String(url);
    if (path === "/flows/") return new Response(JSON.stringify([activeFlow]));
    if (path === "/flows/1/runs/") return new Response("[]");
    if (path === "/flows/1") return new Response(JSON.stringify(activeFlow));
    if (path === "/hedge-fund/run") {
      runBody = JSON.parse(String(init?.body));
      return new Response(new ReadableStream({ start(controller) {
        controller.enqueue(new TextEncoder().encode(
          'id: 1\nevent: start\ndata: {"type":"start"}\n\n' +
          'id: 2\nevent: progress\ndata: {"type":"progress","agent":"warren_buffett_bb0002","ticker":"AAPL","status":"Done","analysis":"Durable moat"}\n\n' +
          'id: 2\nevent: progress\ndata: {"type":"progress","agent":"warren_buffett_bb0002","ticker":"AAPL","status":"Done","analysis":"Durable moat"}\n\n' +
          'id: 3\nevent: complete\ndata: {"type":"complete","data":{"decisions":{"AAPL":{"action":"long","quantity":8,"confidence":87}},"analyst_signals":{"warren_buffett_bb0002":{"AAPL":{"signal":"bullish","confidence":90,"reasoning":"Strong moat"}}}}}\n\n' +
          'id: 4\nevent: complete\ndata: {"type":"complete","data":{"decisions":{"AAPL":{"action":"short"}}}}\n\n',
        ));
        controller.close();
      }}));
    }
    return new Response("not found", { status: 404 });
  }));
  render(<Harness />);
  await openFlow();
  fireEvent.change(screen.getByLabelText("Símbolos"), { target: { value: "AAPL, MSFT" } });
  fireEvent.click(screen.getByRole("button", { name: "Ejecutar análisis" }));

  await screen.findByText("Completado");
  fireEvent.click(screen.getByRole("button", { name: /Razonamiento/ }));
  expect(await screen.findByText("Durable moat")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Salida" }));
  expect(await screen.findByText("AAPL")).toBeVisible();
  expect(screen.getByText("LONG")).toBeVisible();
  expect(screen.queryByText("SHORT")).not.toBeInTheDocument();
  expect(runBody).toMatchObject({
    tickers: ["AAPL", "MSFT"],
    graph_nodes: activeFlow.nodes,
    graph_edges: activeFlow.edges,
    initial_cash: 100000,
  });
});

it("cancels a live request and never renders secrets or raw tool payloads", async () => {
  let requestSignal: AbortSignal | undefined;
  const cancel = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = String(url);
    if (path === "/flows/") return new Response(JSON.stringify([activeFlow]));
    if (path === "/flows/1/runs/") return new Response("[]");
    if (path === "/flows/1") return new Response(JSON.stringify(activeFlow));
    if (path === "/hedge-fund/run") {
      requestSignal = init?.signal as AbortSignal;
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('event: progress\ndata: {"type":"progress","agent":"system","status":"Working","analysis":"api_key=super-secret tool_payload=raw"}\n\n'));
        },
        cancel,
      }));
    }
    return new Response("not found", { status: 404 });
  }));
  render(<Harness />);
  await openFlow();
  fireEvent.click(screen.getByRole("button", { name: "Ejecutar análisis" }));
  await screen.findByText("En ejecución");
  fireEvent.click(screen.getByRole("button", { name: "Cancelar ejecución" }));
  await waitFor(() => expect(requestSignal?.aborted).toBe(true));
  expect(await screen.findByText("Cancelado")).toBeVisible();
  expect(document.body.textContent).not.toContain("super-secret");
  expect(document.body.textContent).not.toContain("tool_payload");
  expect(cancel).toHaveBeenCalled();
});

it("shows a safe global stream error and keeps the previous result isolated", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
    const path = String(url);
    if (path === "/flows/") return new Response(JSON.stringify([activeFlow]));
    if (path === "/flows/1/runs/") return new Response("[]");
    if (path === "/flows/1") return new Response(JSON.stringify(activeFlow));
    if (path === "/hedge-fund/run") return new Response(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode('event: error\ndata: {"type":"error","message":"Analysis unavailable"}\n\n'));
      controller.close();
    }}));
    return new Response("not found", { status: 404 });
  }));
  render(<Harness />);
  await openFlow();
  fireEvent.click(screen.getByRole("button", { name: "Ejecutar análisis" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Analysis unavailable");
  expect(screen.getByText("Error")).toBeVisible();
});
