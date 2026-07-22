import { devices, expect, test } from '@playwright/test';

test.use({ ...devices['iPhone 13'], browserName: 'chromium' });

const run = {
  run_id: 'run_touch', created_at: null, updated_at: null, sources_count: 0,
  task_title: '触摸回归', task_summary: '', task_source: 'fixture', task_profile_warning: null, archived_at: null,
};

const noSessionProjection = {
  schema_version: 1, selection_status: 'no_session', session: null, session_candidates: [],
  input_task: null, summary: null, idea_tree: null, attempts: [], candidates: [],
  candidate_inventory_status: 'available', champion_status: 'absent', champion: null,
  activity: [], activity_limit: 100, activity_truncated: false, activity_scan_truncated: false,
  developer_refs: null, actions: { candidate_confirmations: [], candidate_promotions: [] }, cognitive_commits: [],
};

test('navigates and opens touch popovers with real tap input', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' }));
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === `/api/runs/${run.run_id}/transcript` || path === `/api/runs/${run.run_id}/sources` || path === `/api/runs/${run.run_id}/jobs`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path === `/api/runs/${run.run_id}/experiment/projection`) return route.fulfill({ json: noSessionProjection });
    if (path === `/api/runs/${run.run_id}/experiment-task/pending`) return route.fulfill({ status: 404, json: { detail: 'not found' } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  await expect(page.getByPlaceholder('输入问题，或粘贴 URL…')).toBeVisible();

  await page.getByRole('button', { name: '实验工作台' }).tap();
  await expect(page.getByText('实验尚未启动。')).toBeVisible();
  await page.getByRole('button', { name: '研究对话' }).tap();
  await page.getByPlaceholder('输入问题，或粘贴 URL…').tap();
  await page.getByRole('button', { name: '上传文件' }).tap();
  await expect(page.locator('.plus-menu-popover')).toHaveAttribute('data-state', 'open');
  await page.locator('.plus-menu-scrim').tap({ position: { x: 10, y: 10 } });
  await expect(page.locator('.plus-menu-popover')).toHaveAttribute('data-state', 'closed');
  await page.getByRole('button', { name: 'Session history' }).tap();
  await expect(page.locator('.session-history-panel')).toHaveAttribute('data-state', 'open');
});
