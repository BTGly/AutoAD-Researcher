import { expect, test, type Page } from '@playwright/test';

const run = {
  run_id: 'run_report', created_at: null, updated_at: null, sources_count: 0,
  task_title: '报告任务', task_summary: '', task_source: 'manual', task_profile_warning: null, archived_at: null,
};

async function prepare(page: Page, content: string) {
  await page.addInitScript(() => localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' })));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === `/api/runs/${run.run_id}/transcript`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/sources`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/jobs`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path === `/api/runs/${run.run_id}/report`) return route.fulfill({ json: { content } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '研究报告' }).click();
}

test('renders only the persisted Markdown report and does not invent export states', async ({ page }) => {
  await prepare(page, '# 实验结论\n\n结果可比较。');
  await expect(page.getByRole('heading', { name: '实验结论' })).toBeVisible();
  await expect(page.getByText('当前 run 的已持久化 Markdown 报告')).toBeVisible();
  await expect(page.getByText('HTML、PDF、Bundle 及其依赖状态未包含在当前报告接口中，因此本页不推断或伪造这些状态。')).toBeVisible();
});

test('states that the report endpoint returned no content', async ({ page }) => {
  await prepare(page, '');
  await expect(page.getByText('当前没有可显示的研究报告')).toBeVisible();
  await expect(page.getByText('报告接口尚未返回 Markdown 正文。')).toBeVisible();
});
