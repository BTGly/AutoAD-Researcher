import { expect, test, type Page } from '@playwright/test';

const run = { run_id: 'run_report_e2e', created_at: null, updated_at: null, sources_count: 0, task_title: '报告页面验收', task_summary: '', task_source: 'manual', task_profile_warning: null, archived_at: null };
const report = {
  report_id: 'report_000001', version: 1, generation_status: 'content_ready', review_status: 'unreviewed',
  format_status: { markdown: 'ready', html: 'ready', pdf: 'unavailable', bundle: 'ready' }, source_snapshot_content_sha256: 'a'.repeat(64), facts_content_sha256: 'b'.repeat(64),
};

async function prepare(page: Page) {
  await page.addInitScript(() => localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' })));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === `/api/runs/${run.run_id}/transcript`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/sources`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/jobs`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path === `/api/runs/${run.run_id}/reports`) return route.fulfill({ json: { reports: [report] } });
    if (path === `/api/runs/${run.run_id}/reports/latest-created` || path === `/api/runs/${run.run_id}/reports/latest-content-ready`) return route.fulfill({ json: report });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/state`) return route.fulfill({ json: { ...report, job_ids: [], jobs: [], retry_count: 0, last_error: null, available_artifacts: ['report.md', 'report.html', 'report_validation.json', 'report_bundle.zip'] } });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/digest`) return route.fulfill({ json: { report_id: report.report_id, facts_content_sha256: report.facts_content_sha256, research_objective: {}, engineering_status: 'READY', execution_status: 'COMPLETED', scientific_status: 'EVIDENCE_INSUFFICIENT', attempt_count: 1, failed_attempt_count: 0, non_comparable_attempt_count: 0, champion: {}, primary_metrics: [{ attempt_id: 'attempt_000001', metric: 'image_auroc', value: 0.91 }], stop_decision: {}, uncertainties: ['Scientific assessment is evidence-insufficient.'] } });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/content`) return route.fulfill({ json: { content: '# Frozen report' } });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/evidence`) return route.fulfill({ json: { entries: [] } });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/discussion`) return route.fulfill({ json: { messages: [], turns: [] } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
}

test('renders separated report states, core metrics, and the HTML delivery', async ({ page }) => {
  await prepare(page);
  await page.getByRole('button', { name: 'Report' }).click();

  await expect(page.getByText('工程：READY', { exact: true })).toBeVisible();
  await expect(page.getByText('执行：COMPLETED', { exact: true })).toBeVisible();
  await expect(page.getByText('科学：EVIDENCE_INSUFFICIENT', { exact: true })).toBeVisible();
  await expect(page.getByText('attempt_000001 · image_auroc: 0.91', { exact: true })).toBeVisible();
  await expect(page.getByTitle('在新窗口打开 HTML')).toHaveAttribute('href', `/api/runs/${run.run_id}/reports/${report.report_id}/download/report.html`);
});
