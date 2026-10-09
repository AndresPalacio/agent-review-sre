import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InvestigationContent, Overview } from "./InvestigationContent";

describe("investigation evidence and conclusions", () => {
  it("connects report citations to observed evidence and exposes verification doubts", () => {
    render(
      <InvestigationContent
        detail={{
          report: {
            diagnosis: { root_cause: "Memory limit reached", confidence: 0.8 },
            claims: [
              {
                statement: "Pod exceeded memory",
                citations: ["E1"],
                status: "weak",
                reason: "Need metric confirmation",
              },
            ],
            labels: { ev1: "E1" },
            verification: { passed: false, score: 0.4, missing_sections: [] },
          },
          evidence: [
            {
              id: "ev1",
              tool: "k8s_list_pods",
              integration: "kubernetes",
              summary: "OOMKilled",
              data: { reason: "OOMKilled" },
            },
          ],
        }}
      />,
    );
    expect(screen.getByRole("link", { name: "E1" })).toHaveAttribute(
      "href",
      "#evidence-ev1",
    );
    expect(screen.getByText("Need metric confirmation")).toBeVisible();
    expect(screen.getByText("Requiere revisión")).toBeVisible();
    expect(screen.getByText("k8s_list_pods")).toBeVisible();
    expect(screen.getByText("Datos observados")).toBeVisible();
  });

  it("preserves zero confidence and pending hypotheses without inventing a conclusion", () => {
    render(
      <Overview
        detail={{
          plan: {
            hypotheses: [
              {
                id: "H1",
                statement: "Check saturation",
                confirm_if: "Queue rises",
                rule_out_if: "Queue stable",
              },
            ],
          },
          diagnosis: {
            root_cause: "Unknown",
            confidence: 0,
            status: { next: "Inspect metrics" },
          },
        }}
      />,
    );
    expect(screen.getByText("Confianza: 0%")).toBeVisible();
    expect(screen.getByText("Check saturation")).toBeVisible();
    expect(screen.getByText("Sin resolver")).toBeVisible();
    expect(screen.getByText("Inspect metrics")).toBeVisible();
  });
});
