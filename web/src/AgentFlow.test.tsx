import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { AgentFlow, type AgentFlowData } from "./AgentFlow";

it("shows a pending verification and empty diagnosis for an unfinished thread", () => {
  render(
    <AgentFlow
      data={
        {
          thread_id: "case-1",
          topology: { nodes: [], edges: [] },
          execution: {
            steps: [],
            verification: { passed: null, score: null },
            diagnosis: { root_cause: "" },
          },
        } as unknown as AgentFlowData
      }
    />,
  );
  expect(screen.getByRole("heading", { name: "Qué ocurrió en esta ejecución" })).toBeVisible();
  expect(screen.getByText("Pendiente de verificación.")).toBeVisible();
  expect(screen.getByText("Aún sin causa establecida.")).toBeVisible();
  expect(screen.queryByText("Requiere más evidencia")).not.toBeInTheDocument();
});
