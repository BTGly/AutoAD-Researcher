import { expect, test, type Page } from '@playwright/test';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';

const runId = 'run_fullstack_e2e';
const reportRunId = 'run_report_fullstack_e2e';
const runsRoot = process.env.AUTOAD_E2E_RUNS_ROOT;

test.describe.configure({ mode: 'serial' });

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

test('persists report review and human handoff without creating experiment jobs', async ({ page }) => {
  expect(runsRoot).toBeTruthy();
  await page.addInitScript(() => {
    localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'fullstack-e2e', baseUrl: 'http://fixture.invalid', model: 'fixture' }));
  });
  const jobsPath = join(runsRoot!, reportRunId, 'jobs', 'pipeline_jobs.jsonl');
  const jobsBefore = await readFile(jobsPath, 'utf8');

  await openReportRun(page);
  await expect(page.getByTitle('下载 report_bundle.zip')).toBeVisible();
  await page.getByRole('button', { name: '接受' }).click();
  await expect(page.getByText('审阅：已接受', { exact: true })).toBeVisible();

  await openReportRun(page);
  await expect(page.getByText('审阅：已接受', { exact: true })).toBeVisible();
  await page.getByLabel('人工跟进 Proposal').fill('请人工复核下一步');
  await page.getByRole('button', { name: '创建人工 Proposal' }).click();
  await page.getByRole('button', { name: '确认转交' }).click();
  await expect(page.getByText('请求人工判断 · 已转交人工', { exact: true })).toBeVisible();

  await openReportRun(page);
  await expect(page.getByText('请求人工判断 · 已转交人工', { exact: true })).toBeVisible();
  await expect(page.getByText('已转交：人工队列', { exact: true })).toBeVisible();
  expect(await readFile(jobsPath, 'utf8')).toBe(jobsBefore);
});

async function openReportRun(page: Page) {
  await page.goto('/');
  const history = page.getByRole('button', { name: 'Session history' });
  await history.click();
  await page.locator('.session-row-title').filter({ hasText: '真实报告全栈验收' }).click();
  await expect(page.getByText('当前任务：真实报告全栈验收', { exact: true })).toBeVisible();
  await history.click();
  await page.getByTitle('研究报告', { exact: true }).click();
  await expect(page.getByText('生成：内容可读', { exact: true })).toBeVisible();
}
