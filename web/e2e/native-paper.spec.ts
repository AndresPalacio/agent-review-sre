import { expect, test } from "@playwright/test";

test("paper desk keeps native auth separate and approves a queued order", async ({
  page,
  request,
}) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });

  const queued = await request.post("http://127.0.0.1:8000/v1/orders", {
    headers: { "X-API-Key": "operator-key" },
    data: {
      ticker: "DEEP",
      action: "buy",
      quantity: 10,
      reasoning: "browser fixture",
    },
  });
  expect(queued.status()).toBe(202);
  const pending = await queued.json();

  await page.goto("/");
  await page.getByRole("button", { name: "Paper trading" }).click();
  await page.getByRole("button", { name: "Configuración" }).click();
  await page.getByRole("tab", { name: "Acceso nativo" }).click();
  await page.getByLabel("Clave nativa").fill("approver-key");
  await page.getByRole("button", { name: "Conectar sesión nativa" }).click();
  await expect(page.getByText("Rol conectado:")).toContainText("approver");
  expect(await page.getByLabel("Clave nativa").inputValue()).toBe("");
  expect(
    await page.evaluate(() => [localStorage.length, sessionStorage.length]),
  ).toEqual([0, 0]);
  await page.getByRole("button", { name: "Cerrar configuración" }).click();

  await expect(page.getByText("Conectado · approver")).toBeVisible();
  await expect(
    page.getByRole("button", { name: `Aprobar ${pending.thread_id}` }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/native-paper-before-approval.png",
    fullPage: true,
  });
  expect(await page.locator("body").textContent()).not.toContain(
    "approver-key",
  );
  await page
    .getByRole("button", { name: `Aprobar ${pending.thread_id}` })
    .click();
  await expect(page.getByText("No hay órdenes pendientes.")).toBeVisible();
  await expect(page.getByText("DEEP", { exact: true })).toBeVisible();

  const state = await request.get("http://127.0.0.1:8000/__fixture/state", {
    params: { thread_id: pending.thread_id },
  });
  const value = await state.json();
  expect(value.approvals[0]).toMatchObject({
    decision: "approve",
    action_name: "place_paper_order",
  });
  expect(browserErrors).toEqual([]);
});
