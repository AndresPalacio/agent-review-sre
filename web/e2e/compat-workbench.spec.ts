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

test("frozen reference frontend creates, opens and runs against Fin Graph unchanged", async ({
  page,
  request,
}) => {
  const errors = captureErrors(page);
  await page.goto("http://127.0.0.1:4174");
  await expect(page.getByTitle("Create new flow")).toBeVisible();

  const createdResponse = page.waitForResponse(
    (response) =>
      response.url() === "http://localhost:8000/flows/" &&
      response.request().method() === "POST",
  );
  await page.getByTitle("Create new flow").click();
  await page
    .getByLabel("Name", { exact: true })
    .fill("Frozen reference strategy");
  await page
    .getByLabel("Description", { exact: true })
    .fill("No source changes");
  await page.getByRole("button", { name: "Create Flow" }).click();
  const created = await (await createdResponse).json();

  const updated = await request.put(
    `http://127.0.0.1:8000/flows/${created.id}`,
    {
      data: {
        name: created.name,
        description: created.description,
        nodes: [
          {
            id: "stock_input",
            type: "stock-analyzer-node",
            position: { x: 50, y: 100 },
            data: { name: "Stock Input" },
          },
          {
            id: "valuation_analyst",
            type: "agent-node",
            position: { x: 350, y: 100 },
            data: { name: "Valuation Analyst", agent_key: "valuation_analyst" },
          },
          {
            id: "portfolio_manager",
            type: "portfolio-manager-node",
            position: { x: 650, y: 100 },
            data: { name: "Portfolio Manager" },
          },
        ],
        edges: [
          {
            id: "start-value",
            source: "stock_input",
            target: "valuation_analyst",
          },
          {
            id: "value-manager",
            source: "valuation_analyst",
            target: "portfolio_manager",
          },
        ],
        viewport: { x: 0, y: 0, zoom: 1 },
      },
    },
  );
  expect(updated.ok(), await updated.text()).toBeTruthy();

  await page.reload();
  await page
    .getByText("Frozen reference strategy", { exact: true })
    .first()
    .click();
  await expect(page.getByPlaceholder("Enter tickers")).toBeVisible();
  await page.getByPlaceholder("Enter tickers").fill("AAPL");
  await page.getByTitle(/Run/).click();
  await expect(
    page.getByText("Completed valuation(AAPL)", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("rf__node-portfolio_manager")
      .getByRole("button", { name: "View Investment Report" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
