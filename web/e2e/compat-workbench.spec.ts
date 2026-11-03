import { expect, test, type ConsoleMessage, type Page } from "@playwright/test";

function captureErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message: ConsoleMessage) => {
    if (message.type() === "error") errors.push(message.text());
  });
  return errors;
}

test("new workbench creates, edits, duplicates, runs and backtests a compatible flow", async ({
  page,
}) => {
  const errors = captureErrors(page);
  await page.goto("/");
  await expect(page.getByText("Fin Graph", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Crear flujo" }).click();
  await page
    .getByLabel("Nombre", { exact: true })
    .fill("Browser value strategy");
  await page.getByLabel("Descripción").fill("Compatibility browser journey");
  await page.getByRole("button", { name: "Crear", exact: true }).click();
  await expect(
    page.getByRole("tab", { name: /Browser value strategy/ }),
  ).toBeVisible();

  await page
    .getByRole("button", { name: "Añadir Valuation Analyst" })
    .dblclick();
  const saved = page.waitForResponse(
    (response) =>
      /\/flows\/\d+$/.test(response.url()) &&
      response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Guardar flujo" }).click();
  const savedResponse = await saved;
  expect(savedResponse.ok(), await savedResponse.text()).toBeTruthy();

  await page
    .getByRole("button", { name: "Acciones de Browser value strategy" })
    .click();
  await page
    .getByRole("menuitem", { name: "Duplicar Browser value strategy" })
    .click();
  await expect(
    page.getByRole("button", { name: "Abrir Browser value strategy copy" }),
  ).toBeVisible();

  await page.getByLabel("Símbolos", { exact: true }).fill("AAPL");
  await page.getByRole("button", { name: "Ejecutar análisis" }).click();
  await expect(page.getByText("Completado", { exact: true })).toBeVisible();
  await expect(page.getByText("AAPL", { exact: true }).last()).toBeVisible();
  await page.getByRole("button", { name: /Informe/ }).click();
  await expect(
    page.getByRole("dialog", { name: "Informe de inversión" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Cerrar informe" }).click();

  await page.getByRole("button", { name: "Backtests" }).click();
  await page.getByLabel("Símbolos del backtest").fill("AAPL");
  await page.getByLabel("Inicio del backtest").fill("2026-01-01");
  await page.getByLabel("Fin del backtest").fill("2026-01-31");
  await page.getByRole("button", { name: "Ejecutar backtest" }).click();
  await expect(page.getByText("18.00%", { exact: true })).toBeVisible();
  await expect(page.getByText("$118,000.00", { exact: true })).toBeVisible();

  await page.getByLabel("Símbolos del backtest").fill("SLOW");
  await page.getByRole("button", { name: "Ejecutar backtest" }).click();
  await page.getByRole("button", { name: "Cancelar backtest" }).click();
  await expect(page.getByText("cancelled", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Configuración" }).click();
  await page.getByRole("tab", { name: "Ollama" }).click();
  await page.getByRole("button", { name: "Iniciar Ollama" }).click();
  await expect(page.getByText("Ollama fixture started")).toBeVisible();
  expect(errors).toEqual([]);
});
