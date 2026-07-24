import { expect, test, type Page } from '@playwright/test';
import type { CandidateProposal, ExperimentProjection } from '../src/lib/types';

const run = { run_id: 'run_observatory', created_at: null, updated_at: null, sources_count: 0, task_title: '观测任务', task_summary: '', task_source: 'manual', task_profile_warning: null, archived_at: null };
const projection = {
  schema_version: 1, selection_status: 'selected',
  session: { session_id: 'session_aaaaaaaaaaaaaaaa', task_ref: 'input_task.yaml', task_hash: 'a'.repeat(64), status: 'READY', execution_mode: 'approve_each_step', readiness_status: 'ready', readiness_blockers: [], environment_status: 'ready', baseline_status: 'completed', evaluation_contract_ref: null, evaluation_contract_sha256: null, budget: {}, created_at: '2026-07-20T00:00:00Z', updated_at: '2026-07-20T00:00:00Z' },
  session_candidates: [],
  input_task: { run_id: run.run_id, request: '验证异常检测', source_ids: [], target_domain: null, user_idea: '验证一个可审计的异常检测假设', baseline: 'PatchCore', dataset: 'MVTec bottle', compute_budget: null, primary_metrics: ['image AUROC'], constraints: [] },
  summary: { status: 'READY', readiness_status: 'ready', environment_status: 'ready', baseline_status: 'completed', idea_count: 2, idea_rooted_count: 1, attempt_by_status: { COMPLETED: 1 }, budget: {}, budget_consumed: null, champion_status: 'absent' },
  idea_tree: { session_id: 'session_aaaaaaaaaaaaaaaa', revision: 1, root_node_id: 'idea_000000', nodes: [
    { node_id: 'idea_000000', parent_id: null, is_root: true, depth: 0, mechanism: null, hypothesis: null, observable: null, research_axis: null, minimal_intervention: null, falsification: null, relationship_to_previous_ideas: null, grounding: [], expected_cost: 'unknown', status: 'DRAFT', attempt_refs: [], evidence_refs: [], cognitive_commit_refs: [], insights: [], children: ['idea_000001'], attempt_summary: {} },
    { node_id: 'idea_000001', parent_id: 'idea_000000', is_root: false, depth: 1, mechanism: '局部特征重加权', hypothesis: '可提高 AUROC', observable: 'image AUROC', research_axis: null, minimal_intervention: null, falsification: null, relationship_to_previous_ideas: null, grounding: [], expected_cost: 'low', status: 'SUPPORTED', attempt_refs: ['attempt_000001'], evidence_refs: [], cognitive_commit_refs: [], insights: [{ text: '已记录观察', kind: 'observation', evidence_refs: [], created_at: '2026-07-20T00:00:00Z' }], children: [], attempt_summary: { COMPLETED: 1 } },
  ] },
  attempts: [{ attempt_id: 'attempt_000001', attempt_purpose: 'exploration', runtime_status: 'COMPLETED', job_type: 'experiment_attempt', pipeline_job_id: null, required_device_count: 1, required_vram_mb: 1, retry_of: null, retry_count: 0, max_retries: 0, retry_exhausted: false, failure_code: null, command_plan_summary: 'python run.py', execution_outcome: { execution_status: 'COMPLETED' }, scientific_assessment: null, assessment_reconciliation: null, scientific_assessment_status: 'not_materialized', related_idea_ids: ['idea_000001'], pid: null, heartbeat_at: null, resource_lease_id: null, created_at: '2026-07-20T00:00:00Z', updated_at: '2026-07-20T00:00:00Z' }],
  candidates: [{ candidate_id: 'candidate_000001', idea_id: 'idea_000001', attempt_id: 'attempt_000001', b_test_passed: true, guardrails_passed: true }],
  candidate_inventory_status: 'available',
  actions: { baseline_launch_available: false, candidate_confirmations: [], candidate_promotions: [{ candidate_id: 'candidate_000001' }] },
  cognitive_commits: [], champion_status: 'absent', champion: null,
  activity: [{ event_id: 1, event_type: 'experiment.idea_tree.mutated', created_at: '2026-07-20T00:00:00Z', title: 'Idea Tree 已更新', summary: '树版本：1', card_kind: 'idea_tree', related_idea_id: null, related_attempt_id: null, related_commit_id: null, related_outcome: null, detail: '', evidence_refs: [] }],
  activity_limit: 100, activity_truncated: false,
  activity_scan_truncated: false,
  developer_refs: { run_id: run.run_id, session_id: 'session_aaaaaaaaaaaaaaaa', event_ids: [1], artifact_paths: [], pipeline_job_ids: [], event_log_path: 'events/events.jsonl' },
} satisfies ExperimentProjection;

const candidateProposal: CandidateProposal = {
  proposal_id: 'proposal_0123456789abcdef', run_id: run.run_id, session_id: 'session_aaaaaaaaaaaaaaaa', idempotency_key: 'ui-proposal:session_aaaaaaaaaaaaaaaa', status: 'pending_review', idea_node_id: 'idea_000001', idea_tree_revision: 1,
  evaluation_contract_ref: 'experiments/evaluation_contracts/session_aaaaaaaaaaaaaaaa/evaluation_contract_000001.json', evaluation_contract_sha256: 'b'.repeat(64),
  idea: { mechanism: '局部特征重加权', hypothesis: '局部重加权可改善集中缺陷的 image AUROC', observable: 'image AUROC', research_axis: '局部残差', minimal_intervention: '只修改 model.py 的评分归约', falsification: 'B_dev 未超过冻结噪声阈值', expected_cost: 'low', relationship_to_previous_ideas: '基线后的第一个候选', grounding: ['baseline outcome'] },
  candidate: { intervention_contract: { idea_id: 'idea_000001', mechanism: '局部特征重加权', hypothesis: '局部重加权可改善集中缺陷的 image AUROC', target_modules: ['model.py'], allowed_paths: ['model.py'], forbidden_paths: ['evaluate.py', 'metric.py'], allowed_parameters: ['peak_window'], evaluation_invariants: ['fixed evaluator and split'], time_budget: 30 }, approved_proposal: { edits: [{ path: 'model.py', search: 'return mean(residuals)', replace: 'return max(residuals)' }], changed_symbols: ['score'], possible_contract_deviation: null, confidence: 0.8 }, comparison_seed: 1, idempotency_key: 'candidate:proposal_0123456789abcdef' },
  content_sha256: 'c'.repeat(64), created_at: '2026-07-20T00:00:00Z', updated_at: '2026-07-20T00:00:00Z', decided_by: null, attempt_id: null,
};

async function prepare(page: Page, getProjection: (sessionId?: string) => object = () => projection, withWebSocket = false, delayedRefreshMs = 0) {
  let projectionRequests = 0;
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
        projectionRequests += 1;
        const value = getProjection(new URL(route.request().url()).searchParams.get('session_id') || undefined);
        if (delayedRefreshMs > 0 && projectionRequests > 1) await new Promise(resolve => setTimeout(resolve, delayedRefreshMs));
        return route.fulfill({ json: value });
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

test('launches a Baseline from an environment-ready Session with an explicit contract', async ({ page }) => {
  const baselineProjection = structuredClone(projection);
  baselineProjection.session.status = 'READY_FOR_BASELINE';
  baselineProjection.session.baseline_status = 'not_started';
  baselineProjection.summary.status = 'READY_FOR_BASELINE';
  baselineProjection.summary.baseline_status = 'not_started';
  baselineProjection.actions.baseline_launch_available = true;
  let requestBody: Record<string, unknown> | null = null;
  await prepare(page, () => baselineProjection);
  await page.route(`**/api/runs/${run.run_id}/sessions/${baselineProjection.session.session_id}/baseline`, async route => {
    requestBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { started: {}, evaluation_contract_ref: 'contract.json', execution_inputs_ref: 'inputs.json' } });
  });
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByLabel('Split 标识').fill('fixture-split');
  await page.getByLabel('B_dev 文件引用').fill('inputs/dev.json');
  await page.getByLabel('B_test 文件引用').fill('inputs/test.json');
  await page.getByLabel('Checkpoint 选择').fill('best');
  await page.getByLabel('Seeds').fill('1');
  await expect(page.getByText('请依次填写：最大墙钟秒数、最大 GPU 秒数，单位均为秒。例如 CPU-only 任务填写 20000, 0；此时 GPU 数量和显存填 0 或留空。GPU 任务还需填写 GPU 数量和每个 GPU 所需显存 MB。', { exact: true })).toBeVisible();
  await page.getByLabel('最大墙钟秒数').fill('20000');
  await page.getByLabel('最大 GPU 秒数').fill('0');
  await page.getByLabel('指标方向 image AUROC').selectOption('maximize');
  await page.getByLabel('指标角色 image AUROC').selectOption('primary');
  await page.getByLabel('指标实现引用 image AUROC').fill('metric.py');
  await page.getByRole('button', { name: '冻结契约并启动 Baseline' }).click();
  await expect.poll(() => requestBody).not.toBeNull();
  expect(requestBody).toMatchObject({ contract: { primary_metric: 'image AUROC', max_wall_seconds: 20000, max_gpu_seconds: 0, required_device_count: 0, required_vram_mb: 0, b_dev_ref: 'inputs/dev.json', b_test_ref: 'inputs/test.json' } });
});

test('generates a reviewed Candidate Proposal before creating a Candidate Attempt', async ({ page }) => {
  const proposalProjection = structuredClone(projection);
  proposalProjection.actions = { baseline_launch_available: false, candidate_proposal_generation_available: true, candidate_proposal_approvals: [], candidate_confirmations: [], candidate_promotions: [] };
  proposalProjection.attempts = [];
  proposalProjection.candidates = [];
  proposalProjection.summary.idea_count = 1;
  let generated = false;
  let generationBody: Record<string, unknown> | null = null;
  let approvalBody: Record<string, unknown> | null = null;
  await prepare(page, () => {
    const value = structuredClone(proposalProjection);
    if (generated) {
      value.actions.candidate_proposal_generation_available = false;
      value.actions.candidate_proposal_approvals = [{ proposal: candidateProposal }];
      value.summary.idea_count = 2;
    }
    if (approvalBody) {
      value.actions.candidate_proposal_approvals = [];
      value.attempts = [{ ...projection.attempts[0], attempt_id: 'attempt_candidate_000001', job_type: 'experiment_attempt', related_idea_ids: ['idea_000001'] }];
      value.summary.idea_count = 2;
    }
    return value;
  });
  await page.route(`**/api/runs/${run.run_id}/sessions/${candidateProposal.session_id}/candidate-proposals`, async route => {
    generationBody = route.request().postDataJSON() as Record<string, unknown>;
    generated = true;
    await route.fulfill({ json: { status: 'created', proposal: candidateProposal } });
  });
  await page.route(`**/api/runs/${run.run_id}/sessions/${candidateProposal.session_id}/candidate-proposals/${candidateProposal.proposal_id}/approve`, async route => {
    approvalBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { status: 'started', proposal: { ...candidateProposal, status: 'started', attempt_id: 'attempt_candidate_000001' }, candidate: { status: 'queued', attempt: { attempt_id: 'attempt_candidate_000001' } } } });
  });
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByRole('button', { name: '生成候选方案' }).click();
  await expect(page.getByText('局部重加权可改善集中缺陷的 image AUROC', { exact: true })).toBeVisible();
  await expect(page.getByText('attempt_candidate_000001', { exact: true })).not.toBeVisible();
  expect(generationBody?.idempotency_key).toMatch(new RegExp(`^ui-proposal:${candidateProposal.session_id}:`));
  await page.getByRole('button', { name: '批准候选' }).click();
  await expect.poll(() => approvalBody).not.toBeNull();
  expect(approvalBody).toEqual({ approved_by: 'user' });
  await expect(page.getByRole('button', { name: /attempt_candidate_000001/ })).toBeVisible();
});

test('moves focus to the first invalid Baseline field while retaining the error', async ({ page }) => {
  const baselineProjection = structuredClone(projection);
  baselineProjection.session.status = 'READY_FOR_BASELINE';
  baselineProjection.session.baseline_status = 'not_started';
  baselineProjection.summary.status = 'READY_FOR_BASELINE';
  baselineProjection.summary.baseline_status = 'not_started';
  baselineProjection.actions.baseline_launch_available = true;
  await prepare(page, () => baselineProjection);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByRole('button', { name: '冻结契约并启动 Baseline' }).click();
  await expect(page.getByRole('alert')).toHaveText('数据集、split、checkpoint 选择和冻结文件引用均不能为空。');
  await expect(page.getByLabel('Split 标识')).toBeFocused();
  await expect(page.getByRole('form', { name: 'Baseline 启动表单' })).toHaveAttribute('aria-describedby', 'baseline-launch-error');
});

test('keeps the Baseline form usable without horizontal overflow on mobile', async ({ page }) => {
  const baselineProjection = structuredClone(projection);
  baselineProjection.session.status = 'READY_FOR_BASELINE';
  baselineProjection.session.baseline_status = 'not_started';
  baselineProjection.summary.status = 'READY_FOR_BASELINE';
  baselineProjection.summary.baseline_status = 'not_started';
  baselineProjection.actions.baseline_launch_available = true;
  await prepare(page, () => baselineProjection);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '实验工作台' }).click();
  const dimensions = await page.evaluate(() => ({ clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
  const launchButton = page.getByRole('button', { name: '冻结契约并启动 Baseline' });
  await launchButton.scrollIntoViewIfNeeded();
  await expect(launchButton).toBeInViewport();
});

test('keeps Baseline validation actionable at a 200 percent layout scale', async ({ page }) => {
  const baselineProjection = structuredClone(projection);
  baselineProjection.session.status = 'READY_FOR_BASELINE';
  baselineProjection.session.baseline_status = 'not_started';
  baselineProjection.summary.status = 'READY_FOR_BASELINE';
  baselineProjection.summary.baseline_status = 'not_started';
  baselineProjection.actions.baseline_launch_available = true;
  await prepare(page, () => baselineProjection);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });

  const dimensions = await page.evaluate(() => ({ clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
  const form = page.getByRole('form', { name: 'Baseline 启动表单' });
  await form.scrollIntoViewIfNeeded();
  await expect(form).toBeVisible();
  await page.getByRole('button', { name: '冻结契约并启动 Baseline' }).click();
  await expect(page.getByRole('alert')).toHaveText('数据集、split、checkpoint 选择和冻结文件引用均不能为空。');
  await expect(page.getByLabel('Split 标识')).toBeFocused();
  await expect(form).toHaveAttribute('aria-describedby', 'baseline-launch-error');
});

test('releases the Baseline form when the post-start projection refresh fails', async ({ page }) => {
  const baselineProjection = structuredClone(projection);
  baselineProjection.session.status = 'READY_FOR_BASELINE';
  baselineProjection.session.baseline_status = 'not_started';
  baselineProjection.summary.status = 'READY_FOR_BASELINE';
  baselineProjection.summary.baseline_status = 'not_started';
  baselineProjection.actions.baseline_launch_available = true;
  let baselineStarted = false;
  await prepare(page, () => {
    if (baselineStarted) throw new Error('fixture refresh failure');
    return baselineProjection;
  });
  await page.route(`**/api/runs/${run.run_id}/sessions/${baselineProjection.session.session_id}/baseline`, async route => {
    baselineStarted = true;
    await route.fulfill({ json: { started: {}, evaluation_contract_ref: 'contract.json', execution_inputs_ref: 'inputs.json' } });
  });
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByLabel('Split 标识').fill('fixture-split');
  await page.getByLabel('B_dev 文件引用').fill('inputs/dev.json');
  await page.getByLabel('B_test 文件引用').fill('inputs/test.json');
  await page.getByLabel('Checkpoint 选择').fill('best');
  await page.getByLabel('Seeds').fill('1');
  await page.getByLabel('最大墙钟秒数').fill('30');
  await page.getByLabel('最大 GPU 秒数').fill('0');
  await page.getByLabel('指标方向 image AUROC').selectOption('maximize');
  await page.getByLabel('指标角色 image AUROC').selectOption('primary');
  await page.getByLabel('指标实现引用 image AUROC').fill('metric.py');
  await page.getByRole('button', { name: '冻结契约并启动 Baseline' }).click();
  await expect(page.getByText('Baseline 已启动，但工作台刷新失败。当前契约已保留，请刷新后继续。', { exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(1);
  await expect(page.getByRole('button', { name: '冻结契约并启动 Baseline' })).toBeEnabled();
  await expect(page.getByLabel('Split 标识')).toHaveValue('fixture-split');
});

test('keeps an additional confirmed metric as a recorded observation by default', async ({ page }) => {
  const baselineProjection = structuredClone(projection);
  baselineProjection.session.status = 'READY_FOR_BASELINE';
  baselineProjection.session.baseline_status = 'not_started';
  baselineProjection.summary.status = 'READY_FOR_BASELINE';
  baselineProjection.summary.baseline_status = 'not_started';
  baselineProjection.actions.baseline_launch_available = true;
  baselineProjection.input_task.primary_metrics = ['image AUROC', 'latency'];
  let requestBody: Record<string, unknown> | null = null;
  await prepare(page, () => baselineProjection);
  await page.route(`**/api/runs/${run.run_id}/sessions/${baselineProjection.session.session_id}/baseline`, async route => {
    requestBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { started: {}, evaluation_contract_ref: 'contract.json', execution_inputs_ref: 'inputs.json' } });
  });
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByLabel('Split 标识').fill('fixture-split');
  await page.getByLabel('B_dev 文件引用').fill('inputs/dev.json');
  await page.getByLabel('B_test 文件引用').fill('inputs/test.json');
  await page.getByLabel('Checkpoint 选择').fill('best');
  await page.getByLabel('Seeds').fill('1');
  await page.getByLabel('最大墙钟秒数').fill('30');
  await page.getByLabel('最大 GPU 秒数').fill('0');
  await page.getByLabel('指标方向 image AUROC').selectOption('maximize');
  await page.getByLabel('指标角色 image AUROC').selectOption('primary');
  await page.getByLabel('指标实现引用 image AUROC').fill('metric.py');
  await page.getByLabel('指标方向 latency').selectOption('minimize');
  await page.getByLabel('指标实现引用 latency').fill('metric.py');
  await page.getByRole('button', { name: '冻结契约并启动 Baseline' }).click();
  await expect.poll(() => requestBody).not.toBeNull();
  expect(requestBody).toMatchObject({ contract: { primary_metric: 'image AUROC', guardrails: [], metrics: [{ name: 'image AUROC' }, { name: 'latency' }] } });
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

test('keeps the last observatory snapshot visible during an in-flight refresh', async ({ page }) => {
  await prepare(page, () => projection, false, 350);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('验证一个可审计的异常检测假设')).toBeVisible();
  await page.getByRole('button', { name: '刷新' }).click();
  await expect(page.locator('.observatory-sync-state')).toBeVisible();
  await expect(page.locator('.observatory')).toHaveAttribute('aria-busy', 'true');
  await expect(page.getByText('验证一个可审计的异常检测假设')).toBeVisible();
  await expect(page.locator('.observatory-sync-state')).not.toBeVisible();
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

test('shows the durable scientific assessment reference in an Attempt detail', async ({ page }) => {
  const assessedProjection = structuredClone(projection) as { attempts: Array<Record<string, unknown>> };
  assessedProjection.attempts[0].scientific_assessment_status = 'available';
  assessedProjection.attempts[0].scientific_assessment = {
    scientific_effect: 'IMPROVEMENT',
    primary_delta: 0.1,
    evaluation_status: 'COMPARABLE',
    guardrail_deltas: {},
    patch_applied: true,
    smoke_passed: true,
    outcome_card_ref: 'attempts/attempt_000001/outcome_card.json',
    inputs_ref: 'attempts/attempt_000001/scientific_evaluation_inputs.json',
  };
  assessedProjection.attempts[0].assessment_reconciliation = {
    effective_evaluation_status: 'COMPARABLE',
    execution_protocol_authority: 'outcome_card',
    scientific_comparison_authority: 'scientific_assessment',
    scientific_assessment_ref: 'attempts/attempt_000001/scientific_assessment.json',
  };
  await prepare(page, () => assessedProjection);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByRole('button', { name: /attempt_000001/ }).click();
  await expect(page.getByText('候选探索', { exact: true })).toBeVisible();
  await expect(page.getByText('experiment_attempt', { exact: true })).toHaveCount(0);
  await expect(page.getByText('attempts/attempt_000001/scientific_assessment.json', { exact: true })).toBeVisible();
});

test('uses the projection status vocabulary and preserves complete Session facts', async ({ page }) => {
  const detailedProjection = structuredClone(projection);
  detailedProjection.session.budget = { gpu_hours: 10 };
  detailedProjection.summary.budget = { gpu_hours: 10 };
  detailedProjection.summary.budget_consumed = { gpu_hours: 2 };
  detailedProjection.input_task.constraints = ['单卡运行'];
  detailedProjection.idea_tree.nodes[1].status = 'READY';
  detailedProjection.attempts[0].runtime_status = 'LOST';
  await prepare(page, () => detailedProjection);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('Session：就绪')).toBeVisible();
  await expect(page.getByText('主指标：image AUROC')).toBeVisible();
  await expect(page.getByText('约束：单卡运行')).toBeVisible();
  await expect(page.getByText('预算：gpu_hours: 10')).toBeVisible();
  await expect(page.getByText('异常 Attempt：attempt_000001（运行状态丢失）')).toBeVisible();
  await page.getByRole('button', { name: '局部特征重加权' }).click();
  await expect(page.getByText('等待实验', { exact: true })).toBeVisible();
});

test('renders the explicit B_dev completion state instead of an unknown baseline status', async ({ page }) => {
  const afterBDev = structuredClone(projection);
  afterBDev.session.status = 'READY_FOR_BASELINE';
  afterBDev.session.baseline_status = 'b_dev_completed';
  afterBDev.summary.status = 'READY_FOR_BASELINE';
  afterBDev.summary.baseline_status = 'b_dev_completed';
  afterBDev.actions.baseline_launch_available = false;
  await prepare(page, () => afterBDev);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('Session：B_dev 已完成')).toBeVisible();
  await expect(page.getByText('基线状态：B_dev 已完成')).toBeVisible();
  await expect(page.getByText('未知状态（原始值：b_dev_completed）')).toHaveCount(0);
});

test('filters the experiment list to the selected Idea relations', async ({ page }) => {
  const relationProjection = structuredClone(projection);
  relationProjection.attempts.push({ ...relationProjection.attempts[0], attempt_id: 'attempt_unrelated', related_idea_ids: ['idea_000000'] });
  await prepare(page, () => relationProjection);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByRole('button', { name: '局部特征重加权' }).click();
  await expect(page.getByRole('button', { name: /attempt_000001/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /attempt_unrelated/ })).toHaveCount(0);
});

test('reports a bounded activity scan even when no activity card was produced', async ({ page }) => {
  const boundedProjection = structuredClone(projection);
  boundedProjection.activity = [];
  boundedProjection.activity_scan_truncated = true;
  await prepare(page, () => boundedProjection);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('为控制读取开销，较早动态未完成扫描。')).toBeVisible();
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

test('does not duplicate a Session load when a pending refresh tick changes scope', async ({ page }) => {
  let requests = 0;
  await prepare(page, (sessionId?: string) => {
    requests += 1;
    if (!sessionId) {
      return {
        schema_version: 1,
        selection_status: 'ambiguous',
        session: null,
        session_candidates: [
          { session_id: 'session_aaaaaaaaaaaaaaaa', task_hash: 'a'.repeat(64), status: 'READY', created_at: '2026-07-20T00:00:00Z' },
          { session_id: 'session_bbbbbbbbbbbbbbbb', task_hash: 'b'.repeat(64), status: 'READY', created_at: '2026-07-20T00:00:00Z' },
        ],
        input_task: null, summary: null, idea_tree: null, attempts: [], candidates: [], candidate_inventory_status: 'available', actions: { candidate_confirmations: [], candidate_promotions: [] }, cognitive_commits: [], champion_status: 'absent', champion: null, activity: [], activity_limit: 100, activity_truncated: false, activity_scan_truncated: false, developer_refs: null,
      };
    }
    const value = structuredClone(projection);
    value.session.session_id = sessionId;
    return value;
  }, true);
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('发现多个实验 Session，请明确选择')).toBeVisible();
  await page.evaluate(() => (window as any).emitExperimentEvent());
  const requestsBeforeSelection = requests;
  await page.getByLabel('实验 Session').selectOption('session_bbbbbbbbbbbbbbbb');
  await expect(page.getByText('验证一个可审计的异常检测假设')).toBeVisible();
  await page.waitForTimeout(450);
  expect(requests).toBe(requestsBeforeSelection + 1);
});
