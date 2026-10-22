import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const backend = 'http://127.0.0.1:8000';
const admin = { 'X-API-Key': 'admin-key' };

async function login(page: Page, key = 'admin-key') {
  await page.goto('/');
  await page.getByLabel('API key').fill(key);
  await page.getByRole('button', { name: 'Iniciar sesión', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cerrar sesión' })).toBeVisible();
}

async function fixture(request: APIRequestContext, thread?: string) {
  const response = await request.get(`${backend}/__fixture/state`, {
    params: thread ? { thread_id: thread } : {},
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function prepare(page: Page, replicas: number) {
  await page.getByRole('button', { name: 'Remediaciones', exact: true }).click();
  await page.getByLabel('Acción').selectOption('fixture_scale');
  await page.getByLabel('namespace', { exact: true }).fill('production');
  await page.getByLabel('replicas', { exact: true }).fill(String(replicas));
  const queued = page.waitForResponse(r => r.url().endsWith('/v1/remediations') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Preparar acción', exact: true }).click();
  const response = await queued;
  expect(response.status()).toBe(202);
  await expect(page.getByRole('heading', { name: 'Aprobación pendiente' })).toBeVisible();
  return response.json();
}

test('investigación real, preparación explícita y aprobación única con auditoría', async ({ page, request }) => {
  let streamAttempts = 0;
  const cursors: string[] = [];
  await page.route('**/v1/threads/*/stream', async route => {
    streamAttempts += 1;
    cursors.push(route.request().headers()['last-event-id'] ?? '');
    if (streamAttempts === 1) await route.abort('connectionreset');
    else await route.continue();
  });
  await login(page);
  await page.getByLabel('Texto de la alerta').fill('Deployment checkout con pods OOMKilled');
  const created = page.waitForResponse(r => r.url().endsWith('/v1/investigations') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Investigar', exact: true }).click();
  const investigation = await (await created).json();
  await expect.poll(async () => {
    const response = await request.get(`${backend}/v1/investigations/${investigation.thread_id}`, { headers: admin });
    return (await response.json()).run.status;
  }).toBe('completed');
  await expect(page.getByRole('heading', { name: 'k8s_list_pods', exact: true }).first()).toBeVisible();
  const timeline = page.getByRole('heading', { name: 'Timeline', exact: true }).locator('..').locator('..');
  await expect(timeline.getByRole('listitem').first()).toBeVisible();
  await expect(timeline).toContainText('completed');
  await expect.poll(() => streamAttempts).toBeGreaterThanOrEqual(2);
  const storedEvents = await (await request.get(`${backend}/v1/threads/${investigation.thread_id}/history`, { headers: admin })).json();
  await expect(timeline.getByRole('listitem')).toHaveCount(storedEvents.length);
  expect(cursors[1]).toBeTruthy();
  const detail = await (await request.get(`${backend}/v1/investigations/${investigation.thread_id}`, { headers: admin })).json();
  expect(detail.evidence[0].tool).toBe('k8s_list_pods');
  expect(detail.evidence[0].data).toBeTruthy();
  const before = await fixture(request);
  await page.screenshot({ path: 'test-results/visual-investigation-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/visual-investigation-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1280, height: 720 });
  const pending = await prepare(page, 2);
  expect((await fixture(request)).writes).toHaveLength(before.writes.length);
  await page.screenshot({ path: 'test-results/visual-pending-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/visual-pending-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByLabel('Decisión fixture_scale').selectOption('approve');
  const decided = page.waitForResponse(r => r.url().endsWith(`/remediations/${pending.thread_id}/approve`));
  await page.getByRole('button', { name: 'Confirmar decisiones' }).click();
  expect((await (await decided).json()).outcome).toBe('verified');
  await expect(page.getByRole('heading', { name: 'Aprobación pendiente' })).toHaveCount(0);
  const state = await fixture(request, pending.thread_id);
  expect(state.writes).toHaveLength(before.writes.length + 1);
  expect(state.approvals).toHaveLength(1);
  expect(state.approvals[0]).toMatchObject({ kind: 'remediation', decision: 'approve', action_name: 'fixture_scale' });
  expect(state.approvals[0].actor).toMatch(/^[a-f0-9]{64}$/);
  expect(state.writes.at(-1).actor).toBe(state.approvals[0].actor);
  expect(state.approvals[0].args_digest).toMatch(/^[a-f0-9]{64}$/);
  const repeated = await request.post(`${backend}/v1/remediations/${pending.thread_id}/approve`, {
    headers: admin,
    data: { decisions: [{ type: 'approve' }], approval_ids: pending.approval_ids },
  });
  expect(repeated.status()).toBe(409);
  expect((await fixture(request)).writes).toHaveLength(state.writes.length);
  await page.screenshot({ path: 'test-results/visual-approval-desktop.png', fullPage: true });
});

test('rechazar conserva la auditoría y evita ejecutar', async ({ page, request }) => {
  await login(page);
  const before = await fixture(request);
  const pending = await prepare(page, 3);
  await page.getByLabel('Decisión fixture_scale').selectOption('reject');
  const decided = page.waitForResponse(r => r.url().endsWith(`/remediations/${pending.thread_id}/approve`));
  await page.getByRole('button', { name: 'Confirmar decisiones' }).click();
  expect((await (await decided).json()).outcome).toBe('rejected');
  const state = await fixture(request, pending.thread_id);
  expect(state.writes).toHaveLength(before.writes.length);
  expect(state.approvals[0]).toMatchObject({ decision: 'reject', action_name: 'fixture_scale' });
});

test('viewer consulta sin poder preparar ni aprobar; API key solo en memoria', async ({ page, request }) => {
  const queued = await request.post(`${backend}/v1/remediations`, {
    headers: admin, data: { tool: 'fixture_scale', args: { replicas: 4 } },
  });
  expect(queued.status()).toBe(202);
  const pending = await queued.json();
  await login(page, 'viewer-key');
  await expect(page.getByText('viewer', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  expect(page.url()).not.toContain('viewer-key');
  const response = await request.post(`${backend}/v1/remediations`, {
    headers: { 'X-API-Key': 'viewer-key' }, data: { tool: 'fixture_scale', args: { replicas: 2 } },
  });
  expect(response.status()).toBe(403);
  await expect(page.getByRole('button', { name: 'Investigar', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Remediaciones', exact: true }).click();
  await page.getByLabel('ID de remediación', { exact: true }).fill(pending.thread_id);
  await page.getByRole('button', { name: 'Abrir remediación', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Aprobación pendiente' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Preparar acción', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Confirmar decisiones' })).toHaveCount(0);
  const forbidden = await request.post(`${backend}/v1/remediations/${pending.thread_id}/approve`, {
    headers: { 'X-API-Key': 'viewer-key' },
    data: { decisions: [{ type: 'approve' }], approval_ids: pending.approval_ids },
  });
  expect(forbidden.status()).toBe(403);
  await page.reload();
  await expect(page.getByLabel('API key')).toBeVisible();
});

test('chat por fetch SSE recupera el historial público del hilo', async ({ page }) => {
  await login(page);
  await page.getByRole('button', { name: 'Chat', exact: true }).click();
  await page.getByLabel('Mensaje', { exact: true }).fill('Consulta offline del incidente');
  await page.getByRole('button', { name: 'Enviar mensaje', exact: true }).click();
  const history = page.getByRole('region', { name: 'Historial de chat' });
  await expect(history).toContainText('Consulta offline recibida');
  await expect(page.getByRole('button', { name: 'Enviar mensaje', exact: true })).toBeEnabled();
  const id = await page.getByLabel('ID del hilo', { exact: true }).inputValue();
  expect(id).toBeTruthy();
  await page.getByRole('button', { name: 'Nuevo chat', exact: true }).click();
  await page.getByLabel('ID del hilo', { exact: true }).fill(id);
  await page.getByRole('button', { name: 'Abrir hilo de chat', exact: true }).click();
  await expect(history).toContainText('Consulta offline del incidente');
  await expect(history).toContainText('Consulta offline recibida');
  await page.screenshot({ path: 'test-results/visual-chat-desktop.png', fullPage: true });
});

test('formularios de automatización persisten creación, edición, habilitación y eliminación', async ({ page, request }) => {
  await login(page);
  await page.getByRole('button', { name: 'Automatizaciones', exact: true }).click();
  await page.getByLabel('ID de programación', { exact: true }).fill('browser-health');
  await page.getByLabel('Intervalo en minutos', { exact: true }).fill('8');
  await page.getByRole('button', { name: 'Crear programación', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'browser-health', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Habilitar browser-health', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Deshabilitar browser-health', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Editar browser-health', exact: true }).click();
  await page.getByLabel('Intervalo en minutos', { exact: true }).fill('12');
  await page.getByRole('button', { name: 'Guardar programación', exact: true }).click();
  await expect(page.getByText('Health check · cada 12 minutos', { exact: true })).toBeVisible();
  const row = await (await request.get(`${backend}/v1/schedules/browser-health`, { headers: admin })).json();
  expect(row).toMatchObject({ enabled: true, definition: { interval_minutes: 12 } });
  await page.screenshot({ path: 'test-results/visual-schedules-desktop.png', fullPage: true });
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Eliminar browser-health', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'browser-health', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Crear nueva', exact: true }).click();
  await page.getByLabel('ID de programación', { exact: true }).fill('browser-digest');
  await page.getByRole('combobox', { name: 'Tipo de programación' }).selectOption('digest');
  await page.getByLabel('Hora', { exact: true }).fill('7');
  await page.getByLabel('Minuto', { exact: true }).fill('30');
  await page.getByLabel('Zona horaria', { exact: true }).fill('America/Bogota');
  await page.getByRole('button', { name: 'Crear programación', exact: true }).click();
  await expect(page.getByText('Digest · 07:30 · America/Bogota', { exact: true })).toBeVisible();
  const digest = await (await request.get(`${backend}/v1/schedules/browser-digest`, { headers: admin })).json();
  expect(digest.definition).toMatchObject({ kind: 'digest', hour: 7, minute: 30, timezone: 'America/Bogota' });
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Eliminar browser-digest', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'browser-digest', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Integraciones', exact: true }).click();
  await expect(page.getByText('No hay integraciones registradas.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Verificar integraciones', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
});
