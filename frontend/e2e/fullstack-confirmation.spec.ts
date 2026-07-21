import { expect, test } from '@playwright/test';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';

const runId = 'run_fullstack_e2e';
const runsRoot = process.env.AUTOAD_E2E_RUNS_ROOT;

test('persists an explicitly selected execution repository through the real API', async ({ page }) => {
  expect(runsRoot).toBeTruthy();
  await page.addInitScript(() => {
    localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'fullstack-e2e', baseUrl: 'http://fixture.invalid', model: 'fixture' }));
  });

  await page.goto('/');
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeVisible();
  await page.getByLabel('执行模式').selectOption('approve_each_step');
  await page.getByLabel('执行仓库').selectOption('repo_micro');
  await page.getByRole('button', { name: '确认任务' }).click();
  await expect(page.getByText('实验任务已确认（created）')).toBeVisible();

  const runDir = join(runsRoot!, runId);
  const binding = JSON.parse(await readFile(join(runDir, 'task_bridge', 'execution_repository_binding.json'), 'utf8'));
  const jobs = (await readFile(join(runDir, 'jobs', 'pipeline_jobs.jsonl'), 'utf8'))
    .trim().split('\n').map(line => JSON.parse(line));
  const sessionFiles = (await readdir(join(runDir, 'experiments', 'sessions')))
    .filter(name => name.endsWith('.json'));
  expect(sessionFiles).toHaveLength(1);
  const session = JSON.parse(await readFile(join(runDir, 'experiments', 'sessions', sessionFiles[0]), 'utf8'));

  expect(binding.source_id).toBe('repo_micro');
  expect(jobs).toHaveLength(1);
  expect(jobs[0]).toMatchObject({ job_type: 'experiment_environment_prepare', status: 'queued' });
  expect(session).toMatchObject({
    status: 'CREATED',
    environment_status: 'not_started',
    repository_ref: 'repos/repo_micro',
    authorization: { execution_mode: 'approve_each_step' },
  });
});
