import { expect, test, type Page } from '@playwright/test';

const run = {
  run_id: 'run_e2e', created_at: null, updated_at: null, sources_count: 3,
  task_title: '真人验收', task_summary: '', task_source: 'ui', task_profile_warning: null, archived_at: null,
};

const task = {
  schema_version: 1, task_id: 'task_000001', run_id: run.run_id, status: 'pending_confirmation',
  execution_mode: 'plan_only', input_task: {
    run_id: run.run_id, request: '验证执行仓库选择', source_ids: [], target_domain: null,
    user_idea: null, baseline: null, dataset: null, compute_budget: null,
    primary_metrics: ['image_auroc'], constraints: [],
  }, evidence_refs: [], summary_sha256: 'a'.repeat(64), created_at: '2026-07-20T00:00:00Z', confirmed_at: null,
  primary_metric_candidates: [],
};

const readiness = {
  task_id: task.task_id, ready: true, blockers: [], pending_job_ids: [],
  failed_job_ids: [], unready_source_ids: [],
  admitted_execution_repository_source_ids: ['repo_official', 'repo_micro'], summary_current: true,
};

async function prepare(page: Page, options: { confirmStatus?: number; task?: typeof task; readiness?: typeof readiness; screenshotName?: string } = {}) {
  const taskPayload = options.task ?? task;
  const readinessPayload = options.readiness ?? readiness;
  await page.addInitScript(() => localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' })));
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/runs') return route.fulfill({ json: [run] });
    if (url.pathname === `/api/runs/${run.run_id}/transcript`) return route.fulfill({ json: [] });
    if (url.pathname === `/api/runs/${run.run_id}/sources`) return route.fulfill({ json: [
      { source_id: 'repo_official', kind: 'github_repo', user_label: '官方 reference / 长名称', status: 'ready', intake_status: 'ok' },
      { source_id: 'repo_micro', kind: 'local_repo', user_label: '05_RareCLIP_微型仓库_中文', status: 'ready', intake_status: 'ok' },
      { source_id: 'repo_pending', kind: 'local_repo', user_label: '未完成仓库', status: 'pending', intake_status: 'pending' },
    ] });
    if (url.pathname === `/api/runs/${run.run_id}/jobs`) return route.fulfill({ json: [] });
    if (url.pathname === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (url.pathname === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (url.pathname === `/api/runs/${run.run_id}/intent-summary/primary-metrics`) return route.fulfill({ json: { ...taskPayload, input_task: { ...taskPayload.input_task, primary_metrics: ['image_auroc'] }, primary_metric_candidates: taskPayload.primary_metric_candidates ?? [] } });
    if (url.pathname === `/api/runs/${run.run_id}/experiment-task/pending/readiness`) return route.fulfill({ json: readinessPayload });
    if (url.pathname === '/api/chat/send') return route.fulfill({ json: { reply: '已生成草案', reply_kind: 'answer', source_action: null, experiment_task: taskPayload, experiment_task_readiness: readinessPayload } });
    if (url.pathname === `/api/runs/${run.run_id}/experiment-task/${task.task_id}/confirm`) {
      if (options.confirmStatus) return route.fulfill({ status: options.confirmStatus, json: { detail: { code: 'summary_changed', message: '研究摘要已更新' } } });
      return route.fulfill({ json: { task: { ...taskPayload, status: 'confirmed' }, session_id: 'session_000001', session_status: 'ENVIRONMENT_PENDING', environment_job_id: 'job_000001', disposition: 'created' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  await page.getByPlaceholder('输入问题，或粘贴 URL…').fill('请生成实验草案');
  const send = page.getByRole('button', { name: '发送' });
  await expect(send).toBeEnabled();
  await send.click();
  if (readinessPayload.ready) {
    await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeVisible();
    await expect(page).toHaveScreenshot(options.screenshotName ?? 'experiment-confirmation.png', { fullPage: true, animations: 'disabled' });
  } else {
    await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeHidden();
  }
}

async function selectMicroRepository(page: Page) {
  await page.getByLabel('执行仓库').selectOption('repo_micro');
  await expect(page.getByText('已选择：05_RareCLIP_微型仓库_中文')).toBeVisible();
}

test('binds the explicitly selected repository source ID', async ({ page }) => {
  let requestBody: Record<string, unknown> | null = null;
  await prepare(page);
  await selectMicroRepository(page);
  await page.route(`**/api/runs/${run.run_id}/experiment-task/${task.task_id}/confirm`, async route => {
    requestBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { task: { ...task, status: 'confirmed' }, session_id: 'session_000001', session_status: 'ENVIRONMENT_PENDING', environment_job_id: 'job_000001', disposition: 'created' } });
  });
  await page.getByRole('button', { name: '确认并交给实验 Agent' }).click();
  await expect(page.getByText('实验任务已确认（created）')).toBeVisible();
  expect(requestBody).toEqual({ execution_mode: 'agent_assisted_after_approval', execution_repository_source_id: 'repo_micro' });
});

test('refining the draft leaves confirmation endpoint untouched', async ({ page }) => {
  let confirmations = 0;
  await prepare(page);
  await page.route(`**/api/runs/${run.run_id}/experiment-task/${task.task_id}/confirm`, async route => {
    confirmations += 1;
    await route.fulfill({ json: {} });
  });
  await selectMicroRepository(page);
  await page.getByRole('button', { name: '还需要细化实验草案' }).click();
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeHidden();
  expect(confirmations).toBe(0);
});

test('renders a stable backend conflict without confirming', async ({ page }) => {
  await prepare(page, { confirmStatus: 409 });
  await selectMicroRepository(page);
  await page.getByRole('button', { name: '确认并交给实验 Agent' }).click();
  await expect(page.getByText('研究摘要已更新')).toBeVisible();
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeVisible();
});

test('does not open confirmation while the primary metric is unresolved', async ({ page }) => {
  await prepare(page, {
    task: {
      ...task,
      input_task: { ...task.input_task, primary_metrics: [] },
      primary_metric_candidates: ['image_auroc', 'pixel_auroc'],
    },
    readiness: { ...readiness, ready: false, blockers: ['主指标尚未确认'] },
  });
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeHidden();
});
