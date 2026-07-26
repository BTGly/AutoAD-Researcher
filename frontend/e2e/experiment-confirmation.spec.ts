import { expect, test, type Page } from '@playwright/test';

const run = {
  run_id: 'run_e2e', created_at: null, updated_at: null, sources_count: 3,
  task_title: '真人验收', task_summary: '', task_source: 'ui', task_profile_warning: null, archived_at: null,
};

const binding = {
  schema_version: 1, source_id: 'repo_micro', source_kind: 'local_repo', execution_role: 'executable',
  repository_ref: 'repos/repo_micro', repository_fingerprint: 'b'.repeat(64),
  attestation_ref: 'repo_acquisition/repo_micro/repository_attestation.json', attestation_sha256: 'c'.repeat(64),
  adapter_manifest_ref: 'repos/repo_micro/autoad_executor_adapter.json', adapter_manifest_sha256: 'd'.repeat(64),
  adapter_id: 'generic_python', adapter_evidence: {},
};

const task = {
  schema_version: 1, task_id: 'task_000001', run_id: run.run_id, status: 'pending_confirmation',
  execution_mode: 'plan_only', input_task: {
    run_id: run.run_id, request: '验证执行仓库授权', source_ids: ['repo_official', 'repo_micro'], target_domain: null,
    user_idea: null, baseline: null, dataset: null, compute_budget: null,
    primary_metrics: ['image_auroc'], constraints: [],
  }, evidence_refs: [], execution_repository_binding: null, summary_sha256: 'a'.repeat(64),
  created_at: '2026-07-20T00:00:00Z', confirmed_at: null, primary_metric_candidates: [],
};

const candidates = [
  { source_id: 'repo_official', source_kind: 'github_repo', label: '官方 reference / 长名称', stored_path: 'repos/repo_official', adapter_id: 'generic_python', repository_fingerprint: 'a'.repeat(64), attestation_sha256: '1'.repeat(64), adapter_manifest_sha256: '2'.repeat(64), assigned_role: null },
  { source_id: 'repo_micro', source_kind: 'local_repo', label: '05_RareCLIP_微型仓库_中文', stored_path: 'repos/repo_micro', adapter_id: 'generic_python', repository_fingerprint: 'b'.repeat(64), attestation_sha256: '3'.repeat(64), adapter_manifest_sha256: '4'.repeat(64), assigned_role: null },
];

const awaitingReadiness = {
  task_id: task.task_id, ready: false, blockers: ['执行仓库候选已准备，请确认要用于本次实验的仓库'], pending_job_ids: [],
  failed_job_ids: [], unready_source_ids: [], admitted_execution_repository_source_ids: [],
  execution_repository_candidates: candidates, execution_repository_candidate_revision: 'e'.repeat(64),
  execution_repository_state: 'awaiting_repository_confirmation', execution_repository_admission_code: null,
  execution_repository_admission_blocker: null, summary_current: true,
};

interface PrepareOptions {
  authorizationStatus?: number;
  initialTask?: typeof task & { execution_repository_binding: typeof binding | null };
  initialReadiness?: typeof awaitingReadiness;
}

async function prepare(page: Page, options: PrepareOptions = {}) {
  let currentTask = options.initialTask ?? task;
  let currentReadiness = options.initialReadiness ?? awaitingReadiness;
  const authorizationBodies: Record<string, unknown>[] = [];
  const confirmationBodies: Record<string, unknown>[] = [];
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
    if (url.pathname === `/api/runs/${run.run_id}/experiment-task/pending`) return route.fulfill({ json: currentTask });
    if (url.pathname === `/api/runs/${run.run_id}/experiment-task/pending/readiness`) return route.fulfill({ json: currentReadiness });
    if (url.pathname === '/api/chat/send') return route.fulfill({ json: { reply: '已生成草案', reply_kind: 'answer', source_action: null, experiment_task: currentTask, experiment_task_readiness: currentReadiness } });
    if (url.pathname === `/api/runs/${run.run_id}/experiment-task/${task.task_id}/execution-repository`) {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      authorizationBodies.push(body);
      if (options.authorizationStatus) {
        return route.fulfill({ status: options.authorizationStatus, json: { detail: { code: 'confirmation_invalid', message: '仓库候选已变化，请查看当前候选' } } });
      }
      if (body.decision === 'confirm') {
        currentTask = { ...currentTask, execution_repository_binding: binding };
        currentReadiness = {
          ...currentReadiness,
          ready: true,
          blockers: [],
          admitted_execution_repository_source_ids: ['repo_micro'],
          execution_repository_candidates: candidates.map(candidate => ({
            ...candidate,
            assigned_role: candidate.source_id === 'repo_micro' ? 'executable' : 'candidate_source_only',
          })),
          execution_repository_state: 'ready',
        };
      } else {
        currentReadiness = {
          ...currentReadiness,
          execution_repository_candidates: candidates.map(candidate => ({
            ...candidate,
            assigned_role: candidate.source_id === body.source_id
              ? (body.decision === 'reference_only' ? 'reference_only' : 'candidate_source_only')
              : candidate.assigned_role,
          })),
        };
      }
      return route.fulfill({ json: currentReadiness });
    }
    if (url.pathname === `/api/runs/${run.run_id}/experiment-task/${task.task_id}/confirm`) {
      confirmationBodies.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ json: { task: { ...currentTask, status: 'confirmed' }, session_id: 'session_000001', session_status: 'ENVIRONMENT_PENDING', environment_job_id: 'job_000001', disposition: 'created' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  return { authorizationBodies, confirmationBodies };
}

test('requires repository authorization before final experiment confirmation', async ({ page }, testInfo) => {
  const requests = await prepare(page);
  const authorizationDialog = page.getByRole('dialog', { name: '确认执行仓库' });
  await expect(authorizationDialog).toBeVisible();
  await expect(authorizationDialog.locator('.repository-authorization-modal')).toHaveCSS('opacity', '1');
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeHidden();
  await page.screenshot({ path: testInfo.outputPath('repository-authorization-desktop.png') });
  expect(requests.authorizationBodies).toHaveLength(0);

  await page.getByRole('radio', { name: /05_RareCLIP_微型仓库_中文/ }).check();
  await page.getByRole('button', { name: '授权用于实验' }).click();
  const finalDialog = page.getByRole('dialog', { name: '确认实验任务' });
  await expect(finalDialog).toBeVisible();
  await expect(finalDialog.locator('.experiment-confirmation-modal')).toHaveCSS('opacity', '1');
  await expect(finalDialog.getByText('05_RareCLIP_微型仓库_中文')).toBeVisible();
  await expect(page.getByText('repo_micro', { exact: true })).toBeHidden();
  await page.screenshot({ path: testInfo.outputPath('experiment-confirmation-frozen.png') });

  await page.getByRole('button', { name: '确认并交给实验 Agent' }).click();
  await expect(page.getByText('实验任务已确认（created）')).toBeVisible();
  expect(requests.authorizationBodies).toEqual([{
    source_id: 'repo_micro', decision: 'confirm', candidate_revision: 'e'.repeat(64),
  }]);
  expect(requests.confirmationBodies).toEqual([{ execution_mode: 'agent_assisted_after_approval' }]);
});

test('reference-only decision never opens final confirmation', async ({ page }) => {
  const requests = await prepare(page, {
    initialReadiness: { ...awaitingReadiness, execution_repository_candidates: [candidates[1]] },
  });
  await expect(page.getByRole('dialog', { name: '确认执行仓库' })).toBeVisible();
  await page.getByRole('button', { name: '仅作参考' }).click();
  await expect(page.getByRole('dialog', { name: '确认执行仓库' })).toBeHidden();
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeHidden();
  expect(requests.authorizationBodies[0]).toMatchObject({ source_id: 'repo_micro', decision: 'reference_only' });
  expect(requests.confirmationBodies).toHaveLength(0);
});

test('stale repository revision remains fail closed and refreshable', async ({ page }) => {
  await prepare(page, {
    authorizationStatus: 409,
    initialReadiness: { ...awaitingReadiness, execution_repository_candidates: [candidates[1]] },
  });
  await page.getByRole('button', { name: '授权用于实验' }).click();
  await expect(page.getByText('仓库候选已变化，请查看当前候选')).toBeVisible();
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeHidden();
});

test('unresolved primary metric does not open final confirmation', async ({ page }) => {
  await prepare(page, {
    initialTask: { ...task, input_task: { ...task.input_task, primary_metrics: [] } },
    initialReadiness: {
      ...awaitingReadiness,
      blockers: ['主指标尚未确认', ...awaitingReadiness.blockers],
    },
  });
  await expect(page.getByRole('dialog', { name: '确认执行仓库' })).toBeVisible();
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeHidden();
});

test('repository authorization remains usable on a mobile viewport', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepare(page, {
    initialReadiness: { ...awaitingReadiness, execution_repository_candidates: [candidates[1]] },
  });
  const dialog = page.getByRole('dialog', { name: '确认执行仓库' });
  await expect(dialog).toBeVisible();
  const modal = dialog.locator('.repository-authorization-modal');
  await expect(modal).toHaveCSS('opacity', '1');
  const confirmButton = dialog.getByRole('button', { name: '授权用于实验' });
  await expect(confirmButton).toBeVisible();
  const box = await modal.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(844);
  await page.screenshot({ path: testInfo.outputPath('repository-authorization-mobile.png') });
});

test('repository authorization remains operable at a 200 percent effective viewport', async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 360 });
  await prepare(page, {
    initialReadiness: { ...awaitingReadiness, execution_repository_candidates: [candidates[1]] },
  });
  const dialog = page.getByRole('dialog', { name: '确认执行仓库' });
  const modal = dialog.locator('.repository-authorization-modal');
  await expect(modal).toHaveCSS('opacity', '1');
  const confirmButton = dialog.getByRole('button', { name: '授权用于实验' });
  await confirmButton.scrollIntoViewIfNeeded();
  await expect(confirmButton).toBeVisible();
  await confirmButton.click();
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeVisible();
});
