import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../../lib/api-client";
import { PortfolioView } from "./PortfolioView";
import { OrdersView } from "./OrdersView";
import { ApprovalView } from "./ApprovalView";
import { AnalysisExplainView } from "./AnalysisExplainView";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("loads the paper portfolio and orders with memory-only native auth", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      expect(init?.headers).toMatchObject({ "X-API-Key": "viewer-key" });
      if (String(url) === "/v1/portfolio")
        return new Response(
          JSON.stringify({
            paper: true,
            currency: "USD",
            cash: 9960,
            net_liquidation_value: 10000,
            margin_used: 0,
            queued_orders: 0,
            positions: [
              {
                ticker: "AAPL",
                side: "long",
                shares: 5,
                price: 8,
                market_value: 40,
                unrealized_pnl: 2,
              },
            ],
          }),
        );
      if (String(url) === "/v1/orders")
        return new Response(
          JSON.stringify([
            {
              client_order_id: "ord-1",
              ticker: "AAPL",
              side: "long",
              quantity: 5,
              status: "filled",
            },
          ]),
        );
      return new Response("[]");
    }),
  );
  const client = new ApiClient({ nativeApiKey: "viewer-key" });
  render(
    <>
      <PortfolioView client={client} />
      <OrdersView client={client} />
    </>,
  );
  expect(await screen.findByText("$9,960.00")).toBeVisible();
  expect(await screen.findAllByText("AAPL")).toHaveLength(2);
  expect(document.body).not.toHaveTextContent("viewer-key");
});

it("approves and rejects pending orders using current approval ids", async () => {
  const decisions: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      if (String(url) === "/v1/orders/pending")
        return new Response(
          JSON.stringify([
            {
              thread_id: "thread-1",
              status: "awaiting_approval",
              approval_ids: ["approval-1"],
              preview: { ticker: "AAPL", quantity: 5 },
            },
          ]),
        );
      if (String(url) === "/v1/orders/thread-1/approve") {
        decisions.push(JSON.parse(String(init?.body)));
        return new Response(JSON.stringify({ outcome: "filled" }));
      }
      return new Response("not found", { status: 404 });
    }),
  );
  render(
    <ApprovalView
      client={new ApiClient({ nativeApiKey: "approver-key" })}
      role="approver"
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Aprobar thread-1" }),
  );
  await waitFor(() =>
    expect(decisions).toContainEqual({
      decisions: [{ type: "approve" }],
      approval_ids: ["approval-1"],
    }),
  );
});

it("loads an analysis explanation on demand", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            thread_id: "analysis-1",
            execution: {
              diagnosis: { root_cause: "Valuation gap" },
              verification: { passed: true },
            },
          }),
        ),
    ),
  );
  render(
    <AnalysisExplainView client={new ApiClient({ nativeApiKey: "viewer" })} />,
  );
  fireEvent.change(screen.getByLabelText("ID de análisis"), {
    target: { value: "analysis-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Explicar análisis" }));
  expect(await screen.findByText("Valuation gap")).toBeVisible();
});
