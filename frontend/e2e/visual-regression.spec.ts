import { expect, test, type Page, type TestInfo } from '@playwright/test';

const run = {
  run_id: 'run_visual', created_at: null, updated_at: null, sources_count: 0,
  task_title: '视觉回归任务', task_summary: '', task_source: 'fixture', task_profile_warning: null, archived_at: null,
};

const noSessionProjection = {
  schema_version: 1, selection_status: 'no_session', session: null, session_candidates: [],
  input_task: null, summary: null, idea_tree: null, attempts: [], candidates: [],
  candidate_inventory_status: 'available', champion_status: 'absent', champion: null,
  activity: [], activity_limit: 100, activity_truncated: false, activity_scan_truncated: false,
  developer_refs: null,
};

const selectedProjection = {
  ...noSessionProjection,
  selection_status: 'selected',
  session: {
    session_id: 'session_visual', task_ref: 'input_task.yaml', task_hash: 'a'.repeat(64), status: 'READY',
    execution_mode: 'approve_each_step', readiness_status: 'ready', readiness_blockers: [],
    environment_status: 'ready', baseline_status: 'completed', evaluation_contract_ref: null,
    evaluation_contract_sha256: null, budget: { gpu_hours: 10 }, created_at: '2026-07-20T00:00:00Z', updated_at: '2026-07-20T00:00:00Z',
  },
  input_task: {
    run_id: run.run_id, request: '验证异常检测', source_ids: [], target_domain: '异常检测',
    user_idea: '验证一个可审计的异常检测假设', baseline: 'PatchCore', dataset: 'MVTec bottle',
    compute_budget: null, primary_metrics: ['image AUROC'], constraints: ['单卡运行'],
  },
  summary: { status: 'READY', readiness_status: 'ready', environment_status: 'ready', baseline_status: 'completed', idea_count: 2, idea_rooted_count: 1, attempt_by_status: { COMPLETED: 1 }, budget: { gpu_hours: 10 }, budget_consumed: { gpu_hours: 2 }, champion_status: 'absent' },
  idea_tree: { session_id: 'session_visual', revision: 1, root_node_id: 'idea_root', nodes: [
    { node_id: 'idea_root', parent_id: null, is_root: true, depth: 0, mechanism: null, hypothesis: null, observable: null, research_axis: null, minimal_intervention: null, falsification: null, relationship_to_previous_ideas: null, grounding: [], expected_cost: 'unknown', status: 'DRAFT', attempt_refs: [], evidence_refs: [], cognitive_commit_refs: [], insights: [], children: ['idea_visual'], attempt_summary: {} },
    { node_id: 'idea_visual', parent_id: 'idea_root', is_root: false, depth: 1, mechanism: '局部特征重加权', hypothesis: '可提高 AUROC', observable: 'image AUROC', research_axis: null, minimal_intervention: null, falsification: null, relationship_to_previous_ideas: null, grounding: [], expected_cost: 'low', status: 'SUPPORTED', attempt_refs: ['attempt_visual'], evidence_refs: [], cognitive_commit_refs: [], insights: [{ text: '已记录观察', kind: 'observation', evidence_refs: [], created_at: '2026-07-20T00:00:00Z' }], children: [], attempt_summary: { COMPLETED: 1 } },
  ] },
  attempts: [], candidates: [], actions: { candidate_confirmations: [], candidate_promotions: [] }, cognitive_commits: [], champion_status: 'absent', champion: null,
  activity: [{ event_id: 1, event_type: 'experiment.idea_tree.mutated', created_at: '2026-07-20T00:00:00Z', title: 'Idea Tree 已更新', summary: '树版本：1', card_kind: 'idea_tree', related_idea_id: null, related_attempt_id: null, related_commit_id: null, related_outcome: null, detail: '', evidence_refs: [] }],
  activity_limit: 100, activity_truncated: false, activity_scan_truncated: false, developer_refs: { run_id: run.run_id, session_id: 'session_visual', event_ids: [1], artifact_paths: [], pipeline_job_ids: [], event_log_path: 'events/events.jsonl' },
};

const report = {
  report_id: 'report_visual', version: 1, generation_status: 'content_ready', review_status: 'accepted',
  format_status: { markdown: 'ready', html: 'ready', pdf: 'unavailable', bundle: 'ready' }, source_snapshot_content_sha256: 'a'.repeat(64), facts_content_sha256: 'b'.repeat(64),
};
const pendingReport = { ...report, report_id: 'report_visual_pending', version: 2, generation_status: 'queued', review_status: 'unreviewed', format_status: { markdown: 'pending', html: 'pending', pdf: 'pending', bundle: 'pending' } };

async function prepareChat(page: Page, projection = noSessionProjection) {
  await page.addInitScript(() => localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' })));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === `/api/runs/${run.run_id}/transcript`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/sources`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/jobs`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path === `/api/runs/${run.run_id}/experiment/projection`) return route.fulfill({ json: projection });
    if (path === `/api/runs/${run.run_id}/experiment-task/pending`) return route.fulfill({ status: 404, json: { detail: 'not found' } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  await expect(page.getByPlaceholder('输入问题，或粘贴 URL…')).toBeVisible();
}

async function prepareReport(page: Page) {
  const proposals = [{ proposal_id: 'proposal_visual', proposal_type: 'REQUEST_HUMAN', rationale: '请人工确认下一步', status: 'HANDED_OFF', validation_errors: [], handoff: { kind: 'human_queue' } }];
  await page.addInitScript(() => localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' })));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === `/api/runs/${run.run_id}/transcript`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/sources`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/jobs`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path === `/api/runs/${run.run_id}/reports`) return route.fulfill({ json: { reports: [report, pendingReport] } });
    if (path === `/api/runs/${run.run_id}/reports/latest-created` || path === `/api/runs/${run.run_id}/reports/latest-content-ready`) return route.fulfill({ json: report });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/state`) return route.fulfill({ json: { ...report, job_ids: [], jobs: [], retry_count: 0, last_error: null, available_artifacts: ['report.md', 'report.html', 'report_bundle.zip'] } });
    if (path === `/api/runs/${run.run_id}/reports/${pendingReport.report_id}/state`) return route.fulfill({ json: { ...pendingReport, job_ids: [], jobs: [], retry_count: 0, last_error: null, available_artifacts: [] } });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/digest`) return route.fulfill({ json: { report_id: report.report_id, facts_content_sha256: report.facts_content_sha256, research_objective: {}, engineering_status: 'READY', execution_status: 'COMPLETED', scientific_status: 'EVIDENCE_INSUFFICIENT', attempt_count: 1, failed_attempt_count: 0, non_comparable_attempt_count: 0, champion: {}, primary_metrics: [{ attempt_id: 'attempt_visual', metric: 'image_auroc', value: 0.91 }], stop_decision: {}, uncertainties: ['Scientific assessment is evidence-insufficient.'] } });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/content`) return route.fulfill({ json: { content: '# Frozen report\n\n## Results\n\nEvidence remains bounded and auditable.' } });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/evidence`) return route.fulfill({ json: { entries: [] } });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/discussion`) return route.fulfill({ json: { messages: [], turns: [] } });
    if (path === `/api/runs/${run.run_id}/reports/${report.report_id}/proposals`) return route.fulfill({ json: { proposals } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
}

async function assertNoHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({ documentWidth: document.documentElement.scrollWidth, viewportWidth: window.innerWidth }));
  expect(dimensions.documentWidth).toBeLessThanOrEqual(dimensions.viewportWidth + 1);
}

test('captures Chat light, dark, and touch layouts without overflow', async ({ page }, testInfo: TestInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ colorScheme: 'light' });
  await prepareChat(page);
  await page.screenshot({ path: testInfo.outputPath('chat-light.png'), fullPage: true });
  await assertNoHorizontalOverflow(page);
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({ path: testInfo.outputPath('chat-dark.png'), fullPage: true });
  await assertNoHorizontalOverflow(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.screenshot({ path: testInfo.outputPath('chat-touch.png'), fullPage: true });
  await assertNoHorizontalOverflow(page);
});

test('captures Experiment no-session and selected observatory layouts', async ({ page }, testInfo: TestInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepareChat(page);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('实验尚未启动。')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('experiment-no-session.png'), fullPage: true });
  await assertNoHorizontalOverflow(page);

  await page.route(`**/api/runs/${run.run_id}/experiment/projection`, route => route.fulfill({ json: selectedProjection }));
  await page.getByRole('button', { name: '刷新' }).click();
  await expect(page.getByText('验证一个可审计的异常检测假设')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('experiment-selected.png'), fullPage: true });
  await assertNoHorizontalOverflow(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath('experiment-touch.png'), fullPage: true });
  await assertNoHorizontalOverflow(page);
});

test('captures reviewed, handed-off, and pending Report workspace states', async ({ page }, testInfo: TestInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepareReport(page);
  await page.getByRole('button', { name: '研究报告' }).click();
  await expect(page.getByText('审阅：accepted', { exact: true })).toBeVisible();
  await expect(page.getByText('REQUEST_HUMAN · HANDED_OFF', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('report-reviewed-handoff.png'), fullPage: true });
  await assertNoHorizontalOverflow(page);
  await page.locator('select').selectOption(pendingReport.report_id);
  await expect(page.getByText('此版本尚无可读 Markdown。')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('report-pending.png'), fullPage: true });
  await assertNoHorizontalOverflow(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath('report-touch.png'), fullPage: true });
  await assertNoHorizontalOverflow(page);
});
