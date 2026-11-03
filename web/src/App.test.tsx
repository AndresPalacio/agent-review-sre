import { fireEvent, render, screen } from "@testing-library/react";
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
  expect(screen.getByRole("complementary", { name: "Componentes" })).toBeVisible();
  expect(screen.getByRole("region", { name: "Resultados" })).toBeVisible();
});

it("persists only the selected theme", () => {
  const write = vi.spyOn(Storage.prototype, "setItem");
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Cambiar a tema claro" }));
  expect(write).toHaveBeenCalledWith("fin-graph-theme", "light");
  expect(write).toHaveBeenCalledTimes(1);
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
  expect(document.body).not.toHaveTextContent("provider-secret-must-not-render");
});
