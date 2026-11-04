import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App, AppErrorBoundary } from "./App";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

it("renders the responsive financial workbench landmarks", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) =>
      String(url).endsWith("/flows/")
        ? new Response("[]")
        : new Response(JSON.stringify({ agents: [] })),
    ),
  );
  render(<App />);

  expect(screen.getByRole("banner")).toHaveTextContent("Fin Graph");
  expect(screen.getByRole("navigation", { name: "Flujos" })).toBeVisible();
  expect(screen.getByRole("main")).toBeVisible();
  expect(
    screen.getByRole("complementary", { name: "Componentes" }),
  ).toBeVisible();
  expect(screen.getByRole("region", { name: "Resultados" })).toBeVisible();
});

it("exposes financial product surfaces without operational incident language", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.endsWith("/flows/")) return new Response("[]");
      if (url.endsWith("/v2/algorithms")) return Response.json([]);
      return Response.json({ agents: [] });
    }),
  );
  render(<App />);

  expect(screen.getByRole("heading", { name: /estrategia/i })).toBeVisible();
  expect(document.body).toHaveTextContent(/analistas/i);

  fireEvent.click(screen.getByRole("button", { name: "Backtests" }));
  expect(await screen.findByRole("heading", { name: "Backtests" })).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Paper trading" }));
  expect(await screen.findByRole("heading", { name: "Mesa de inversión" })).toBeVisible();
  expect(document.body).toHaveTextContent(/cartera simulada/i);
  expect(document.body).toHaveTextContent(/órdenes/i);
  expect(document.body).not.toHaveTextContent(/AI Hedge Fund/i);
  expect(document.body).not.toHaveTextContent(/causa raíz|incidente|remediación/i);
});

it("persists only the selected theme", () => {
  const write = vi.spyOn(Storage.prototype, "setItem");
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Cambiar a tema claro" }));
  expect(write).toHaveBeenCalledWith("fin-graph-theme", "light");
  expect(write).toHaveBeenCalledTimes(1);
});

it("navigates to backtests, paper trading and settings from the product bar", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.endsWith("/flows/")) return new Response("[]");
      if (url.endsWith("/v2/algorithms")) return Response.json([]);
      if (url.endsWith("/v1/portfolio"))
        return Response.json({ paper: true, positions: [] });
      if (url.endsWith("/v1/orders")) return Response.json([]);
      if (url.endsWith("/v1/orders/pending")) return Response.json([]);
      if (url.endsWith("/language-models/"))
        return Response.json({ models: [] });
      return Response.json({});
    }),
  );
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Backtests" }));
  expect(
    await screen.findByRole("heading", { name: "Backtests" }),
  ).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Paper trading" }));
  expect(
    await screen.findByRole("heading", { name: "Mesa de inversión" }),
  ).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Configuración" }));
  await waitFor(() =>
    expect(screen.getByRole("dialog", { name: "Configuración" })).toBeVisible(),
  );
});

it("contains rendering failures in a recoverable boundary", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const Broken = () => {
    throw new Error("provider-secret-must-not-render");
  };
  render(
    <AppErrorBoundary>
      <Broken />
    </AppErrorBoundary>,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "No pudimos mostrar el espacio de trabajo",
  );
  expect(document.body).not.toHaveTextContent(
    "provider-secret-must-not-render",
  );
});
