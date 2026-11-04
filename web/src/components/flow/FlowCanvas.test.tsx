import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { analysts } from "../../data/analysts";
import { createCatalogNode, nodeCatalog } from "../../data/node-catalog";
import type { FlowEdge, FlowNode } from "../../types/flow";
import {
  FlowCanvas,
  cloneNodes,
  isValidFlowConnection,
  serializeFlow,
} from "./FlowCanvas";

const nodes: FlowNode[] = [
  {
    id: "portfolio-start-node_abc123",
    type: "portfolio-start-node",
    position: { x: 10, y: 20 },
    data: { name: "Portfolio Input", description: "Capital", status: "Idle" },
  },
  {
    id: "warren_buffett_def456",
    type: "agent-node",
    position: { x: 310, y: 20 },
    data: {
      name: "Warren Buffett",
      description: "Value",
      status: "Idle",
      agent_key: "warren_buffett",
    },
  },
];

describe("flow compatibility model", () => {
  it("keeps all nineteen analysts in canonical order", () => {
    expect(analysts).toHaveLength(19);
    expect(analysts.map((item) => item.key)).toEqual([
      "aswath_damodaran", "ben_graham", "bill_ackman", "cathie_wood",
      "charlie_munger", "michael_burry", "mohnish_pabrai", "nassim_taleb",
      "peter_lynch", "phil_fisher", "rakesh_jhunjhunwala",
      "stanley_druckenmiller", "warren_buffett", "technical_analyst",
      "fundamentals_analyst", "growth_analyst", "news_sentiment_analyst",
      "sentiment_analyst", "valuation_analyst",
    ]);
    expect(nodeCatalog.filter((item) => item.kind === "analyst")).toHaveLength(19);
  });

  it("creates reference node ids/types and serializes exact public fields", () => {
    const node = createCatalogNode("warren_buffett", { x: 40, y: 60 }, "zz0099");
    expect(node).toMatchObject({
      id: "warren_buffett_zz0099",
      type: "agent-node",
      position: { x: 40, y: 60 },
      data: { name: "Warren Buffett", status: "Idle", agent_key: "warren_buffett" },
    });
    expect(serializeFlow(nodes, [{ id: "edge-1", source: nodes[0].id, target: nodes[1].id }])).toEqual({
      nodes,
      edges: [{ id: "edge-1", source: nodes[0].id, target: nodes[1].id }],
    });
  });

  it("enforces graph direction and gives pasted nodes fresh compatible ids", () => {
    const edge: FlowEdge = { id: "edge-1", source: nodes[0].id, target: nodes[1].id };
    expect(isValidFlowConnection({ source: nodes[0].id, target: nodes[1].id }, nodes, [])).toBe(true);
    expect(isValidFlowConnection({ source: nodes[1].id, target: nodes[0].id }, nodes, [])).toBe(false);
    expect(isValidFlowConnection({ source: nodes[0].id, target: nodes[1].id }, nodes, [edge])).toBe(false);
    const copies = cloneNodes(nodes, () => "pq1234");
    expect(copies[0].id).toBe("portfolio-start-node_pq1234");
    expect(copies[0].position).toEqual({ x: 34, y: 44 });
  });
});

describe("FlowCanvas", () => {
  it("recovers malformed positions and unknown node types instead of crashing", () => {
    const malformed = [
      {
        id: "mystery_bad001",
        type: "future-node",
        position: { x: Number.NaN, y: Number.POSITIVE_INFINITY },
        data: { name: "Future strategy" },
      },
    ] as unknown as FlowNode[];
    render(<FlowCanvas initialNodes={malformed} initialEdges={[]} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Se reparó 1 componente");
    expect(screen.getByText("Componente desconocido")).toBeInTheDocument();
  });

  it("adds a catalog node and blocks deletion while an input owns focus", () => {
    const changed = vi.fn();
    render(<FlowCanvas initialNodes={nodes} initialEdges={[]} onChange={changed} />);
    fireEvent.click(screen.getByTestId(nodes[0].id));
    const input = screen.getByLabelText("Nombre del flujo");
    input.focus();
    fireEvent.keyDown(input, { key: "Delete" });
    expect(screen.getByTestId(nodes[0].id)).toBeInTheDocument();
    input.blur();
    fireEvent.keyDown(window, { key: "Delete" });
    expect(screen.queryByTestId(nodes[0].id)).not.toBeInTheDocument();
  });

  it("does not emit an unchanged document when only the callback identity changes", () => {
    const first = vi.fn();
    const second = vi.fn();
    const view = render(
      <FlowCanvas initialNodes={nodes} initialEdges={[]} onChange={first} />,
    );
    expect(first).toHaveBeenCalledOnce();

    view.rerender(
      <FlowCanvas initialNodes={nodes} initialEdges={[]} onChange={second} />,
    );

    expect(second).not.toHaveBeenCalled();
  });
});
