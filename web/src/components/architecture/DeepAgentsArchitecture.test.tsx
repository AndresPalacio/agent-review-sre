import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../../App";

function stubAppRequests() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.endsWith("/flows/")) return Response.json([]);
      return Response.json({ agents: [] });
    }),
  );
}

async function renderArchitecture() {
  stubAppRequests();
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "Arquitectura" }));
  await screen.findByRole("heading", { name: "Arquitectura de agentes" });
  return user;
}

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("DeepAgentsArchitecture", () => {
  it("abre en Objetivo y explica el límite implementado", async () => {
    await renderArchitecture();

    expect(screen.getByRole("button", { name: "Objetivo" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("status", { name: "Veredicto de arquitectura" }),
    ).toHaveTextContent(
      "Todo bucle autónomo usa Deep Agents; LangGraph conserva el control determinista",
    );
  });

  it("cambia el veredicto y la topología entre los tres modos", async () => {
    const user = await renderArchitecture();
    const verdict = screen.getByRole("status", {
      name: "Veredicto de arquitectura",
    });

    expect(verdict).toHaveTextContent("Todo bucle autónomo usa Deep Agents");

    await user.click(screen.getByRole("button", { name: "Actual" }));
    expect(verdict).toHaveTextContent(
      "La topología anterior mezclaba dos fábricas de agentes",
    );
    expect(
      screen.getByRole("button", { name: "Fábrica LangChain heredada" }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Literal" }));
    expect(verdict).toHaveTextContent(
      "Sustituir LangGraph sería un error de categoría",
    );
    expect(
      screen.getByRole("button", { name: "Control determinista" }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Objetivo" }));
    expect(verdict).toHaveTextContent("Todo bucle autónomo usa Deep Agents");
  });

  it("abre el inspector de nodos con teclado y muestra la ruta exacta", async () => {
    const user = await renderArchitecture();
    const syncDelegation = screen.getByRole("button", {
      name: "Delegación síncrona nativa",
    });

    syncDelegation.focus();
    await user.keyboard("{Enter}");

    const inspector = screen.getByRole("region", { name: "Detalle del nodo" });
    expect(inspector).toHaveTextContent("Propósito");
    expect(inspector).toHaveTextContent("Implementación");
    expect(inspector).toHaveTextContent("Por qué existe");
    expect(
      within(inspector).getByText("src/fin_graph/agents/common/subagents.py"),
    ).toBeVisible();
  });

  it("presenta make_agent como ruta restringida sin ocultar las rutas directas", async () => {
    const user = await renderArchitecture();

    await user.click(
      screen.getByRole("button", { name: "Fábrica restringida" }),
    );

    const inspector = screen.getByRole("region", { name: "Detalle del nodo" });
    expect(inspector).toHaveTextContent(
      "make_agent restringe la ruta común de los agentes pequeños",
    );
    expect(inspector).toHaveTextContent(
      "persona, chat, optimizer y solver-child llaman create_deep_agent directamente",
    );
  });

  it("calcula 1 + hijos y avisa cuando faltan workers", async () => {
    await renderArchitecture();

    fireEvent.change(screen.getByRole("slider", { name: "Hijos asíncronos" }), {
      target: { value: "5" },
    });
    fireEvent.change(
      screen.getByRole("slider", { name: "Workers disponibles" }),
      {
        target: { value: "4" },
      },
    );

    expect(screen.getByText("6 slots requeridos")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Faltan 2 workers: se necesitan 6 y hay 4",
    );
  });

  it("enlaza la documentación oficial de Deep Agents y LangGraph", async () => {
    await renderArchitecture();

    const links = [
      [
        "Subagentes de Deep Agents",
        "https://docs.langchain.com/oss/python/deepagents/subagents",
      ],
      [
        "Subagentes asíncronos de Deep Agents",
        "https://docs.langchain.com/oss/python/deepagents/async-subagents",
      ],
      [
        "Documentación de LangGraph",
        "https://docs.langchain.com/oss/python/langgraph/overview",
      ],
    ] as const;

    for (const [name, href] of links) {
      const link = screen.getByRole("link", { name });
      expect(link).toHaveAttribute("href", href);
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noreferrer noopener");
    }
  });
});
