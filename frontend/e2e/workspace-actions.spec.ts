import { expect, test } from '@playwright/test';

const initialRun = {
  run_id: 'run_actions', created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z', sources_count: 0,
  task_title: '操作回归任务', task_summary: '', task_source: 'fixture', task_profile_warning: null, archived_at: null,
};

const createdRun = {
  ...initialRun,
  run_id: 'run_actions_created',
  task_title: '未命名研究任务',
  updated_at: '2026-07-22T00:01:00Z',
};

test('sends chat, uploads a source, creates a task, and renames the active task', async ({ page }) => {
  const runs = [initialRun];
  const requests: Array<{ method: string; path: string }> = [];
  await page.addInitScript(() => {
    localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' }));
  });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    requests.push({ method: request.method(), path });

    if (path === '/api/runs' && request.method() === 'GET') return route.fulfill({ json: runs });
    if (path === '/api/runs' && request.method() === 'POST') {
      runs.push(createdRun);
      return route.fulfill({ json: createdRun });
    }
    if ((path === `/api/runs/${initialRun.run_id}` || path === `/api/runs/${createdRun.run_id}`) && request.method() === 'PATCH') {
      const body = JSON.parse(request.postData() || '{}') as { task_title?: string };
      const target = runs.find(item => path === `/api/runs/${item.run_id}`) || initialRun;
      const updated = { ...target, task_title: body.task_title || target.task_title, updated_at: '2026-07-22T00:02:00Z' };
      const index = runs.findIndex(item => item.run_id === target.run_id);
      runs[index] = updated;
      return route.fulfill({ json: updated });
    }
    if (path.endsWith('/transcript') && request.method() === 'GET') return route.fulfill({ json: [] });
    if (path.endsWith('/sources') && request.method() === 'GET') return route.fulfill({ json: [] });
    if (path.endsWith('/jobs') && request.method() === 'GET') return route.fulfill({ json: [] });
    if (path.endsWith('/evidence/state') && request.method() === 'GET') return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path.endsWith('/intent-summary') && request.method() === 'GET') return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path.endsWith('/experiment-task/pending') && request.method() === 'GET') return route.fulfill({ status: 404, json: { detail: 'not found' } });
    if (path === `/api/chat/send` && request.method() === 'POST') {
      return route.fulfill({ json: { reply: '已收到测试消息。', reply_kind: 'message', source_action: null, experiment_task: null } });
    }
    if (path === `/api/runs/${initialRun.run_id}/sources/upload` && request.method() === 'POST') {
      return route.fulfill({ json: { source: { source_id: 'source_notes', kind: 'text', stored_path: 'sources/notes.txt' }, jobs: [], artifacts: ['sources/notes.txt'] } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/');
  await expect(page.getByPlaceholder('输入问题，或粘贴 URL…')).toBeVisible();

  await page.getByPlaceholder('输入问题，或粘贴 URL…').fill('验证请求链路');
  await page.getByRole('button', { name: '发送' }).click();
  await expect(page.getByText('已收到测试消息。')).toBeVisible();

  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: '上传文件' }).click();
  await page.getByRole('button', { name: '选择 PDF / txt / md' }).click();
  await (await chooser).setFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('evidence') });
  await expect(page.getByText(/已上传 notes\.txt/)).toBeVisible();

  await page.getByRole('button', { name: 'New session' }).click();
  await expect(page.getByText('未命名研究任务')).toBeVisible();

  await page.getByRole('button', { name: 'Session history' }).click();
  await page.getByRole('button', { name: 'Rename session' }).click();
  const renameInput = page.locator('.session-row.active input');
  await renameInput.fill('已重命名任务');
  await renameInput.press('Enter');
  await expect(page.getByText('任务已重命名', { exact: true })).toBeVisible();

  expect(requests).toContainEqual({ method: 'POST', path: '/api/chat/send' });
  expect(requests).toContainEqual({ method: 'POST', path: `/api/runs/${initialRun.run_id}/sources/upload` });
  expect(requests).toContainEqual({ method: 'POST', path: '/api/runs' });
  expect(requests).toContainEqual({ method: 'PATCH', path: `/api/runs/${createdRun.run_id}` });
});
