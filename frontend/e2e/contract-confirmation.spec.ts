import { expect, test } from '@playwright/test';

const RUN_ID = 'run_confirmation_e2e';
const CONFIRMATION_ID = 'contract_confirmation_e2e';
const DRAFT_HASH = 'a'.repeat(64);

test('chat confirmation opens the modal and only modal approval creates a session', async ({ page }) => {
  let pending = false;
  let approved = false;
  let confirmationPosts = 0;
  const confirmationBodies: Array<Record<string, unknown>> = [];

  await page.addInitScript(() => {
    localStorage.setItem('autoad_config', JSON.stringify({
      apiKey: 'sk-browser-fixture',
      baseUrl: 'https://provider.invalid',
      model: 'fixture-model',
    }));
  });
  await page.routeWebSocket('**/ws/**', socket => socket.close());

  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });

    if (method === 'GET' && path === '/api/runs') {
      return json([{ run_id: RUN_ID, title: 'Fixture task', created_at: '2026-07-14T00:00:00Z' }]);
    }
    if (method === 'GET' && path === `/api/runs/${RUN_ID}/transcript`) return json([]);
    if (method === 'GET' && path === `/api/runs/${RUN_ID}/sources`) return json([]);
    if (method === 'GET' && path === `/api/runs/${RUN_ID}/jobs`) return json([]);
    if (method === 'GET' && path === `/api/runs/${RUN_ID}/evidence/state`) {
      return json({ usable_evidence: [], unusable_parsed_sources: [] });
    }
    if (method === 'GET' && path === `/api/runs/${RUN_ID}/draft`) {
      return json({
        ready: true,
        has_draft: true,
        title: '研究计划草案',
        fields: [
          { field: 'research_goal', label: '研究目标', value: '复现 Method-X', status: 'known' },
          { field: 'execution_mode', label: '执行模式', value: 'plan_only', status: 'known' },
        ],
        missing: [],
        sources: [],
        evidence: [],
        jobs: [],
        next_questions: [],
        confirmation: pending && !approved ? {
          confirmation_id: CONFIRMATION_ID,
          draft_hash: DRAFT_HASH,
          status: 'pending',
          requested_at: '2026-07-14T00:00:00Z',
          fields: [
            { field: 'research_goal', label: '研究目标', value: '复现 Method-X', status: 'known' },
            { field: 'execution_mode', label: '执行模式', value: 'plan_only', status: 'known' },
          ],
        } : null,
      });
    }
    if (method === 'GET' && path === `/api/runs/${RUN_ID}/experiment-session`) {
      return json({
        session: approved ? {
          session_id: 'experiment_session_e2e',
          prepare_job_id: 'job_prepare_e2e',
          status: 'queued',
        } : null,
        readiness: null,
        job: approved ? { job_id: 'job_prepare_e2e', status: 'queued', attempt_count: 0 } : null,
        requests: [],
      });
    }
    if (method === 'POST' && path === '/api/chat/send') {
      const body = request.postDataJSON();
      expect(body.user_input).toBe('确认');
      pending = true;
      return json({ reply: '请在弹窗中核对并确认合同。', reply_kind: 'intent_contract_confirmation' });
    }
    if (method === 'POST' && path === `/api/runs/${RUN_ID}/draft/confirmation`) {
      confirmationPosts += 1;
      const body = request.postDataJSON() as Record<string, unknown>;
      confirmationBodies.push(body);
      if (body.confirmation_id !== CONFIRMATION_ID || body.draft_sha256 !== DRAFT_HASH) {
        return json({ detail: { code: 'confirmation_stale', message: 'contract confirmation is stale' } }, 409);
      }
      expect(body.decision).toBe('approved');
      approved = true;
      pending = false;
      return json({
        confirmation_id: CONFIRMATION_ID,
        status: 'approved',
        message: '合同已确认',
      });
    }
    return json({ detail: `Unhandled fixture route: ${method} ${path}` }, 404);
  });

  await page.goto('/');
  await expect(page.getByPlaceholder('输入问题，或粘贴 URL…')).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);

  await page.getByPlaceholder('输入问题，或粘贴 URL…').fill('确认');
  await page.getByRole('button', { name: '发送' }).click();

  const modal = page.getByRole('dialog', { name: '确认研究任务合同' });
  await expect(modal).toBeVisible();
  await expect(modal.getByText('复现 Method-X')).toBeVisible();
  const modalBox = await modal.boundingBox();
  const viewport = page.viewportSize();
  expect(modalBox).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(modalBox!.x).toBeGreaterThanOrEqual(0);
  expect(modalBox!.y).toBeGreaterThanOrEqual(0);
  expect(modalBox!.x + modalBox!.width).toBeLessThanOrEqual(viewport!.width);
  expect(modalBox!.y + modalBox!.height).toBeLessThanOrEqual(viewport!.height);
  expect(confirmationPosts).toBe(0);
  expect(approved).toBe(false);

  const staleStatus = await page.evaluate(async ({ runId, confirmationId }) => {
    const response = await fetch(`/api/runs/${runId}/draft/confirmation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        confirmation_id: confirmationId,
        draft_sha256: '0'.repeat(64),
        decision: 'approved',
      }),
    });
    return response.status;
  }, { runId: RUN_ID, confirmationId: CONFIRMATION_ID });
  expect(staleStatus).toBe(409);
  await expect(modal).toBeVisible();
  expect(approved).toBe(false);

  await modal.getByRole('button', { name: '确认合同' }).click();

  await expect(modal).toHaveCount(0);
  expect(confirmationPosts).toBe(2);
  expect(confirmationBodies[1]).toEqual({
    confirmation_id: CONFIRMATION_ID,
    draft_sha256: DRAFT_HASH,
    decision: 'approved',
  });
  expect(approved).toBe(true);

  const session = await page.evaluate(async runId => {
    const response = await fetch(`/api/runs/${runId}/experiment-session`);
    return response.json();
  }, RUN_ID);
  expect(session.session.session_id).toBe('experiment_session_e2e');
  expect(session.job.job_id).toBe('job_prepare_e2e');
});
