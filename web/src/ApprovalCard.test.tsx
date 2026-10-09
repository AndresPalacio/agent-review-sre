import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApprovalCard } from "./ApprovalCard";
import { ApiError } from "./api";
const requests = [
  {
    action_requests: [
      {
        name: "scale",
        args: { replicas: 2 },
        description: "Preparación verificada",
      },
      { name: "restart", args: { service: "api" } },
    ],
    review_configs: [
      {
        action_name: "scale",
        allowed_decisions: ["approve", "edit", "reject"],
      },
      { action_name: "restart", allowed_decisions: ["reject"] },
    ],
  },
];
describe("approval review", () => {
  it("sends every decision with checkpoint IDs and edited arguments", async () => {
    const submit = vi.fn();
    render(
      <ApprovalCard
        requests={requests}
        ids={["checkpoint"]}
        canApprove
        onSubmit={submit}
        onStale={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("Decisión scale"), {
      target: { value: "edit" },
    });
    fireEvent.change(screen.getByLabelText("Argumentos editados scale"), {
      target: { value: '{"replicas":3}' },
    });
    fireEvent.click(screen.getByText("Confirmar decisiones"));
    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith({
        approval_ids: ["checkpoint"],
        decisions: [
          {
            type: "edit",
            edited_action: { name: "scale", args: { replicas: 3 } },
          },
          { type: "reject" },
        ],
      }),
    );
  });
  it("reloads stale requests without retrying execution", async () => {
    const stale = vi.fn();
    const submit = vi.fn().mockRejectedValue(new ApiError(409, "stale"));
    render(
      <ApprovalCard
        requests={requests}
        ids={["old"]}
        canApprove
        onSubmit={submit}
        onStale={stale}
      />,
    );
    fireEvent.click(screen.getByText("Confirmar decisiones"));
    await waitFor(() => expect(stale).toHaveBeenCalledTimes(1));
    expect(submit).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent("actualizó");
  });
  it("does not allow viewer approval", () => {
    render(
      <ApprovalCard
        requests={requests}
        ids={["x"]}
        canApprove={false}
        onSubmit={vi.fn()}
        onStale={() => {}}
      />,
    );
    expect(screen.queryByText("Confirmar decisiones")).not.toBeInTheDocument();
  });
});
it("keeps stale reload failures visible without an unhandled rejection", async () => {
  render(
    <ApprovalCard
      requests={[
        {
          action_requests: [{ name: "scale", args: {} }],
          review_configs: [
            { action_name: "scale", allowed_decisions: ["approve"] },
          ],
        },
      ]}
      ids={["old"]}
      canApprove
      onSubmit={async () => {
        throw new ApiError(409, "stale");
      }}
      onStale={async () => {
        throw new Error("sin conexión");
      }}
    />,
  );
  fireEvent.click(screen.getByText("Confirmar decisiones"));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("No se pudo recargar"),
  );
});
