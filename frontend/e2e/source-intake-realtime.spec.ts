import { expect, test, type WebSocketRoute } from '@playwright/test';

const runId = 'run_intake_e2e';

test('durable intake update wins over an older in-flight source refresh', async ({ page }) => {
  let sourceRequestCount = 0;
  let releaseFirstSourceRequest: (() => void) | null = null;
  const firstSourceRequest = new Promise<void>(resolve => {
    releaseFirstSourceRequest = resolve;
  });
  let socket: WebSocketRoute | null = null;

  await page.addInitScript(() => {
    localStorage.setItem('autoad_config', JSON.stringify({
      apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture',
    }));
  });
  await page.routeWebSocket(new RegExp(`/api/runs/${runId}/ws$`), ws => {
    socket = ws;
  });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/runs') {
      return route.fulfill({ json: [{
        run_id: runId, created_at: null, updated_at: null, sources_count: 1,
        task_title: '资料竞态验收', task_summary: '', task_source: 'ui',
        task_profile_warning: null, archived_at: null,
      }] });
    }
    if (url.pathname === `/api/runs/${runId}/transcript`) return route.fulfill({ json: [] });
    if (url.pathname === `/api/runs/${runId}/sources`) {
      sourceRequestCount += 1;
      if (sourceRequestCount === 1) await firstSourceRequest;
      const parsed = sourceRequestCount > 1;
      return route.fulfill({ json: [{
        source_id: 'source_pdf', kind: 'paper_pdf', user_label: 'SimpleNet.pdf',
        status: parsed ? 'parsed' : 'parsing', intake_status: parsed ? 'ok' : 'pending',
      }] });
    }
    if (url.pathname === `/api/runs/${runId}/jobs`) return route.fulfill({ json: [] });
    if (url.pathname === `/api/runs/${runId}/evidence/state`) {
      return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    }
    if (url.pathname === `/api/runs/${runId}/intent-summary`) {
      return route.fulfill({ json: {
        goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null,
      } });
    }
    if (url.pathname === `/api/runs/${runId}/experiment-task/pending`) return route.fulfill({ status: 404, json: { detail: 'not found' } });
    if (url.pathname === `/api/runs/${runId}/experiment-task/pending/readiness`) return route.fulfill({ status: 404, json: { detail: 'not found' } });
    return route.fulfill({ json: {} });
  });

  await page.goto('/');
  await expect.poll(() => socket !== null).toBe(true);
  socket!.send(JSON.stringify({
    type: 'source.intake_updated', event_id: 1, source_id: 'source_pdf',
    status: 'parsed', intake_status: 'ok', kind: 'paper_pdf',
    stored_path: 'sources/source_pdf/SimpleNet.pdf',
  }));

  await expect.poll(() => sourceRequestCount).toBe(2);
  const inspector = page.getByLabel('研究上下文 Inspector');
  await expect(inspector.getByText('SimpleNet.pdf')).toBeVisible();
  await expect(inspector.getByText('已解析')).toBeVisible();

  releaseFirstSourceRequest!();
  await expect(page.getByPlaceholder('输入问题，或粘贴 URL…')).toBeEnabled();
  await expect(inspector.getByText('已解析')).toBeVisible();
  await expect(inspector.getByText('解析中')).toBeHidden();
});
