import { expect, test, type Page, type TestInfo } from '@playwright/test';

const run = {
  run_id: 'run_visual', created_at: null, updated_at: null, sources_count: 1,
  task_title: '视觉基准任务', task_summary: '', task_source: 'fixture', task_profile_warning: null, archived_at: null,
};

const noSessionProjection = { schema_version: 1, selection_status: 'no_session', session: null, session_candidates: [], input_task: null, summary: null, idea_tree: null, attempts: [], candidates: [], candidate_inventory_status: 'available', champion_status: 'absent', champion: null, activity: [], activity_limit: 100, activity_truncated: false, activity_scan_truncated: false, developer_refs: null };

const selectedProjection = {
  schema_version: 1, selection_status: 'selected',
  session: { session_id: 'session_visual_000001', task_ref: 'input_task.yaml', task_hash: 'a'.repeat(64), status: 'READY', execution_mode: 'approve_each_step', readiness_status: 'ready', readiness_blockers: [], environment_status: 'ready', baseline_status: 'completed', budget: {} },
  session_candidates: [],
  input_task: { run_id: run.run_id, request: '验证视觉工作台', source_ids: [], target_domain: null, user_idea: '验证一个可审计的异常检测假设', baseline: 'PatchCore', dataset: 'MVTec bottle', compute_budget: null, primary_metrics: ['image AUROC'], constraints: [] },
  summary: { idea_count: 1, idea_rooted_count: 1, attempt_by_status: {}, budget: {}, budget_consumed: null, champion_status: 'absent' },
  idea_tree: { session_id: 'session_visual_000001', revision: 1, root_node_id: 'idea_visual_000001', nodes: [{ node_id: 'idea_visual_000001', parent_id: null, is_root: true, depth: 0, mechanism: '局部特征重加权', hypothesis: '可提高 AUROC', observable: 'image AUROC', research_axis: null, minimal_intervention: null, falsification: null, relationship_to_previous_ideas: null, grounding: [], expected_cost: 'low', status: 'SUPPORTED', attempt_refs: [], evidence_refs: [], cognitive_commit_refs: [], insights: [], children: [], attempt_summary: {} }] },
  attempts: [], candidates: [], candidate_inventory_status: 'available', champion_status: 'absent', champion: null,
  activity: [{ event_id: 1, event_type: 'experiment.idea_tree.mutated', created_at: '2026-07-20T00:00:00Z', title: 'Idea Tree 已更新', summary: '树版本：1', card_kind: 'idea_tree', related_idea_id: null, related_attempt_id: null, related_commit_id: null, related_outcome: null, detail: '', evidence_refs: [] }],
  activity_limit: 100, activity_truncated: false, activity_scan_truncated: false, developer_refs: null,
};

const pendingTask = {
  schema_version: 1, task_id: 'task_visual_000001', run_id: run.run_id, status: 'pending_confirmation', execution_mode: 'plan_only',
  input_task: { run_id: run.run_id, request: '验证执行仓库选择', source_ids: [], target_domain: null, user_idea: null, baseline: null, dataset: null, compute_budget: null, primary_metrics: ['image_auroc'], constraints: [] },
  evidence_refs: [], summary_sha256: 'a'.repeat(64), created_at: '2026-07-20T00:00:00Z', confirmed_at: null,
};

async function prepare(
  page: Page,
  theme: 'light' | 'dark',
  getProjection: () => object = () => noSessionProjection,
  chatTask: object | null = null,
) {
  await page.addInitScript(value => {
    localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' }));
    localStorage.setItem('autoad_theme_preference', value);
  }, theme);
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === `/api/runs/${run.run_id}/transcript`) return route.fulfill({ json: [{ role: 'user', content: '验证视觉工作台', created_at: '2026-07-20T00:00:00Z' }, { role: 'assistant', content: '已准备研究上下文。', created_at: '2026-07-20T00:00:01Z' }] });
    if (path === `/api/runs/${run.run_id}/sources`) return route.fulfill({ json: [{ source_id: 'paper_001', kind: 'paper_pdf', user_label: '参考论文', status: 'parsed', intake_status: 'ok' }] });
    if (path === `/api/runs/${run.run_id}/jobs`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '验证界面', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path === `/api/runs/${run.run_id}/experiment/projection`) {
      try {
        return route.fulfill({ json: getProjection() });
      } catch {
        return route.fulfill({ status: 500, json: { detail: 'fixture refresh failure' } });
      }
    }
    if (path === '/api/chat/send') return route.fulfill({ json: { reply: '已生成实验草案。', reply_kind: 'answer', source_action: null, experiment_task: chatTask } });
    if (path === `/api/runs/${run.run_id}/report`) return route.fulfill({ json: { content: '# 研究报告\n\n这是持久化的报告正文。' } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
}

async function assertNoHorizontalOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

for (const theme of ['light', 'dark'] as const) {
  test(`captures ${theme} workspace baselines`, async ({ page }, testInfo: TestInfo) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await prepare(page, theme);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`${theme}-chat-1440.png`), fullPage: true });

    await page.getByRole('button', { name: '实验工作台' }).click();
    await expect(page.getByText('实验尚未启动。')).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`${theme}-experiment-no-session-1440.png`), fullPage: true });

    await page.getByRole('button', { name: '研究报告' }).click();
    await expect(page.locator('.report-toolbar').getByRole('heading', { name: '研究报告' })).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`${theme}-report-1440.png`), fullPage: true });
  });
}

for (const viewport of [{ width: 1280, height: 800 }, { width: 1024, height: 768 }]) {
  test(`keeps the light workspace within ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await prepare(page, 'light');
    await assertNoHorizontalOverflow(page);
    await page.getByRole('button', { name: '实验工作台' }).click();
    await assertNoHorizontalOverflow(page);
    await page.getByRole('button', { name: '研究报告' }).click();
    await assertNoHorizontalOverflow(page);
  });
}

test('captures selection, approval, and failed-refresh visual states', async ({ page }, testInfo: TestInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepare(page, 'light', () => ({ ...noSessionProjection, selection_status: 'ambiguous', session_candidates: [
    { session_id: 'session_visual_000001', task_hash: 'a'.repeat(64), status: 'READY', created_at: '2026-07-20T00:00:00Z' },
    { session_id: 'session_visual_000002', task_hash: 'b'.repeat(64), status: 'READY', created_at: '2026-07-20T00:00:00Z' },
  ] }));
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('发现多个实验 Session，请明确选择')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('light-experiment-ambiguous-1440.png'), fullPage: true });

  await prepare(page, 'light', undefined, pendingTask);
  await page.getByPlaceholder('输入问题，或粘贴 URL…').fill('请生成实验草案');
  await page.getByRole('button', { name: '发送' }).click();
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('light-pending-task-confirmation-1440.png'), fullPage: true });

  let refreshFails = false;
  await prepare(page, 'light', () => {
    if (refreshFails) throw new Error('fixture refresh failure');
    return selectedProjection;
  });
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('验证一个可审计的异常检测假设')).toBeVisible();
  refreshFails = true;
  await page.getByRole('button', { name: '刷新' }).click();
  await expect(page.getByRole('alert')).toHaveText('工作台刷新失败，仍保留上一份有效快照。');
  await expect(page.getByText('验证一个可审计的异常检测假设')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('light-experiment-refresh-failed-1440.png'), fullPage: true });
});
