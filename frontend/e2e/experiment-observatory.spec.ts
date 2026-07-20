import { expect, test, type Page } from '@playwright/test';

const run = { run_id: 'run_observatory', created_at: null, updated_at: null, sources_count: 0, task_title: '观测任务', task_summary: '', task_source: 'manual', task_profile_warning: null, archived_at: null };
const projection = {
  schema_version: 1, selection_status: 'selected',
  session: { session_id: 'session_aaaaaaaaaaaaaaaa', task_ref: 'input_task.yaml', task_hash: 'a'.repeat(64), status: 'READY', execution_mode: 'approve_each_step', readiness_status: 'ready', readiness_blockers: [], environment_status: 'ready', baseline_status: 'completed', budget: {} },
  session_candidates: [],
  input_task: { run_id: run.run_id, request: '验证异常检测', source_ids: [], target_domain: null, user_idea: '验证一个可审计的异常检测假设', baseline: 'PatchCore', dataset: 'MVTec bottle', compute_budget: null, primary_metrics: ['image AUROC'], constraints: [] },
  summary: { idea_count: 2, idea_rooted_count: 1, attempt_by_status: { COMPLETED: 1 }, budget: {}, budget_consumed: null, champion_status: 'absent' },
  idea_tree: { session_id: 'session_aaaaaaaaaaaaaaaa', revision: 1, root_node_id: 'idea_000000', nodes: [
    { node_id: 'idea_000000', parent_id: null, is_root: true, depth: 0, mechanism: null, hypothesis: null, observable: null, research_axis: null, minimal_intervention: null, falsification: null, relationship_to_previous_ideas: null, grounding: [], expected_cost: 'unknown', status: 'DRAFT', attempt_refs: [], evidence_refs: [], cognitive_commit_refs: [], insights: [], children: ['idea_000001'], attempt_summary: {} },
    { node_id: 'idea_000001', parent_id: 'idea_000000', is_root: false, depth: 1, mechanism: '局部特征重加权', hypothesis: '可提高 AUROC', observable: 'image AUROC', research_axis: null, minimal_intervention: null, falsification: null, relationship_to_previous_ideas: null, grounding: [], expected_cost: 'low', status: 'SUPPORTED', attempt_refs: ['attempt_000001'], evidence_refs: [], cognitive_commit_refs: [], insights: [{ text: '已记录观察', kind: 'observation', evidence_refs: [], created_at: '2026-07-20T00:00:00Z' }], children: [], attempt_summary: { COMPLETED: 1 } },
  ] },
  attempts: [{ attempt_id: 'attempt_000001', attempt_purpose: 'exploration', runtime_status: 'COMPLETED', job_type: 'experiment_attempt', pipeline_job_id: null, required_device_count: 1, required_vram_mb: 1, retry_of: null, retry_count: 0, max_retries: 0, retry_exhausted: false, failure_code: null, command_plan_summary: 'python run.py', execution_outcome: { execution_status: 'COMPLETED' }, scientific_assessment: null, assessment_reconciliation: null, scientific_assessment_status: 'not_materialized', related_idea_ids: ['idea_000001'], pid: null, heartbeat_at: null, resource_lease_id: null }],
  candidates: [{ candidate_id: 'candidate_000001', idea_id: 'idea_000001', attempt_id: 'attempt_000001', b_test_passed: true, guardrails_passed: true }],
  candidate_inventory_status: 'available',
  cognitive_commits: [], champion_status: 'absent', champion: null,
  activity: [{ event_id: 1, event_type: 'experiment.idea_tree.mutated', created_at: '2026-07-20T00:00:00Z', title: 'Idea Tree 已更新', summary: '树版本：1', card_kind: 'idea_tree', related_idea_id: null, related_attempt_id: null, related_commit_id: null, related_outcome: null, detail: '', evidence_refs: [] }],
  activity_limit: 100, activity_truncated: false,
  activity_scan_truncated: false,
  developer_refs: { run_id: run.run_id, session_id: 'session_aaaaaaaaaaaaaaaa', event_ids: [1], artifact_paths: [], pipeline_job_ids: [], event_log_path: 'events/events.jsonl' },
};

async function prepare(page: Page, getProjection: () => object = () => projection, withWebSocket = false) {
  if (withWebSocket) {
    await page.addInitScript(() => {
      const sockets: Array<{ onmessage: ((event: { data: string }) => void) | null; readyState: number }> = [];
      class FixtureWebSocket {
        static CLOSED = 3;
        readyState = 1;
        onopen = null;
        onmessage: ((event: { data: string }) => void) | null = null;
        onclose = null;
        onerror = null;
        constructor() { sockets.push(this); }
        close() { this.readyState = FixtureWebSocket.CLOSED; }
      }
      Object.defineProperty(window, 'WebSocket', { value: FixtureWebSocket });
      (window as typeof window & { emitExperimentEvent: () => void }).emitExperimentEvent = () => {
        for (const socket of sockets) socket.onmessage?.({ data: JSON.stringify({ type: 'experiment.attempt.finalized' }) });
      };
    });
  }
  await page.addInitScript(() => localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' })));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === `/api/runs/${run.run_id}/transcript`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/sources`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/jobs`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path === `/api/runs/${run.run_id}/experiment/projection`) {
      try {
        return route.fulfill({ json: getProjection() });
      } catch {
        return route.fulfill({ status: 500, json: { detail: 'fixture refresh failure' } });
      }
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
}

test('renders a durable observatory snapshot and only prefills discussion', async ({ page }) => {
  let chatCalls = 0;
  await prepare(page);
  await page.route('**/api/chat/send', async route => { chatCalls += 1; await route.fulfill({ json: {} }); });
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('验证一个可审计的异常检测假设')).toBeVisible();
  await page.getByRole('button', { name: '局部特征重加权' }).click();
  await expect(page.getByText('已记录观察', { exact: true }).last()).toBeVisible();
  await page.getByRole('button', { name: '在研究助手中讨论' }).click();
  await expect(page.getByPlaceholder('输入问题，或粘贴 URL…')).toHaveValue(/Idea idea_000001/);
  expect(chatCalls).toBe(0);
});

test('sends an explicit human Champion approval instead of auto-promoting', async ({ page }) => {
  let requestBody: Record<string, unknown> | null = null;
  await prepare(page);
  await page.route(`**/api/runs/${run.run_id}/promotions`, async route => {
    requestBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { approval_ref: 'experiments/champions/approvals/approval_000001.json', champion_event: {} } });
  });
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByLabel('批准人').fill('fixture-user');
  await page.getByRole('button', { name: '批准并推广 Champion' }).click();
  expect(requestBody).toEqual({ candidate_id: 'candidate_000001', approved_by: 'fixture-user' });
});

test('derives a selected detail from the refreshed projection', async ({ page }) => {
  let serveRefreshed = false;
  await prepare(page, () => {
    const value = structuredClone(projection);
    if (serveRefreshed) {
      value.idea_tree.nodes[1].mechanism = '刷新后的局部特征重加权';
      value.idea_tree.nodes[1].hypothesis = '刷新后的可检验假设';
    }
    return value;
  });
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByRole('button', { name: '局部特征重加权' }).click();
  await expect(page.getByText('可提高 AUROC', { exact: true })).toBeVisible();
  serveRefreshed = true;
  await page.getByRole('button', { name: '刷新' }).click();
  await expect(page.getByRole('heading', { name: '刷新后的局部特征重加权' })).toBeVisible();
  await expect(page.getByText('刷新后的可检验假设', { exact: true })).toBeVisible();
});

test('does not present an invalid assessment as not materialized', async ({ page }) => {
  const invalidProjection = structuredClone(projection);
  invalidProjection.attempts[0].scientific_assessment_status = 'invalid';
  await prepare(page, () => invalidProjection);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByRole('button', { name: /attempt_000001/ }).click();
  await expect(page.getByText('工件无效', { exact: true })).toBeVisible();
  await expect(page.getByText('科学评价工件存在但未通过校验，不能作为研究结论。', { exact: true })).toBeVisible();
  await expect(page.getByText('执行事实已记录，科学评价尚未物化。')).not.toBeVisible();
});

test('coalesces WebSocket events and refreshes the selected detail', async ({ page }) => {
  let requests = 0;
  await prepare(page, () => {
    requests += 1;
    const value = structuredClone(projection);
    if (requests > 1) value.idea_tree.nodes[1].hypothesis = '来自实时刷新的假设';
    return value;
  }, true);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByRole('button', { name: '局部特征重加权' }).click();
  await page.evaluate(() => {
    (window as any).emitExperimentEvent();
    (window as any).emitExperimentEvent();
    (window as any).emitExperimentEvent();
  });
  await expect(page.getByText('来自实时刷新的假设', { exact: true })).toBeVisible();
  expect(requests).toBe(2);
});

test('keeps the last projection when a WebSocket refresh fails', async ({ page }) => {
  let serveFailure = false;
  await prepare(page, () => {
    if (serveFailure) throw new Error('fixture refresh failure');
    return projection;
  }, true);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('验证一个可审计的异常检测假设')).toBeVisible();
  serveFailure = true;
  await page.evaluate(() => (window as any).emitExperimentEvent());
  await expect(page.getByRole('alert')).toHaveText('工作台刷新失败，仍保留上一份有效快照。');
  await expect(page.getByText('验证一个可审计的异常检测假设')).toBeVisible();
});
