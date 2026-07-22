import { expect, test, type Page } from '@playwright/test';

const run = {
  run_id: 'run_motion', created_at: null, updated_at: null, sources_count: 0,
  task_title: '交互动效回归', task_summary: '', task_source: 'fixture', task_profile_warning: null, archived_at: null,
};

const noSessionProjection = {
  schema_version: 1, selection_status: 'no_session', session: null, session_candidates: [],
  input_task: null, summary: null, idea_tree: null, attempts: [], candidates: [],
  candidate_inventory_status: 'available', champion_status: 'absent', champion: null,
  activity: [], activity_limit: 100, activity_truncated: false, activity_scan_truncated: false,
  developer_refs: null,
};

async function prepare(page: Page, reducedMotion?: 'reduce' | 'no-preference') {
  if (reducedMotion) await page.emulateMedia({ reducedMotion });
  await page.addInitScript(() => {
    localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' }));
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === `/api/runs/${run.run_id}/transcript`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/sources`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/jobs`) return route.fulfill({ json: [] });
    if (path === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path === `/api/runs/${run.run_id}/experiment/projection`) return route.fulfill({ json: noSessionProjection });
    if (path === `/api/runs/${run.run_id}/experiment-task/pending`) return route.fulfill({ status: 404, json: { detail: 'not found' } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  await expect(page.getByPlaceholder('输入问题，或粘贴 URL…')).toBeVisible();
}

test('keeps the sidebar footprint fixed and expands only from hover or focus', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepare(page);
  const chat = page.locator('.chat-workspace');
  const leftBefore = await chat.evaluate(element => element.getBoundingClientRect().left);
  const sidebar = page.locator('.project-sidebar');
  await expect(page.getByRole('button', { name: '展开导航' })).toHaveCount(0);
  await expect(sidebar).not.toHaveClass(/expanded/);
  await page.locator('.project-sidebar-item').first().hover();
  await expect(sidebar).toHaveClass(/expanded/);
  const leftAfter = await chat.evaluate(element => element.getBoundingClientRect().left);
  expect(leftAfter).toBe(leftBefore);
  await chat.hover();
  await expect(sidebar).not.toHaveClass(/expanded/);

  await page.getByRole('button', { name: '研究对话' }).focus();
  await expect(sidebar).toHaveClass(/expanded/);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('实验尚未启动。')).toBeVisible();
});

test('keeps non-decorative feedback available under Reduced Motion', async ({ page }) => {
  await prepare(page, 'reduce');
  await page.getByRole('button', { name: '研究对话' }).focus();
  const motion = await page.locator('.project-sidebar-label').first().evaluate(element => {
    const style = getComputedStyle(element);
    return { transform: style.transform, duration: style.transitionDuration };
  });
  expect(motion.transform).toBe('none');
  expect(Number.parseFloat(motion.duration)).toBeGreaterThan(0);
});

test('reverses anchored Popovers without leaving a stuck overlay', async ({ page }) => {
  await prepare(page);
  const upload = page.getByRole('button', { name: '上传文件' });
  const uploadPopover = page.locator('.plus-menu-popover');
  await upload.click();
  await expect(uploadPopover).toHaveAttribute('data-state', 'open');
  await page.locator('.plus-menu-scrim').click({ position: { x: 10, y: 10 } });
  await expect(uploadPopover).toHaveAttribute('data-state', 'closed');
  await upload.click();
  await expect(uploadPopover).toHaveAttribute('data-state', 'open');
  const uploadOrigin = await uploadPopover.evaluate(element => {
    const style = getComputedStyle(element);
    return { x: Number.parseFloat(style.transformOrigin.split(' ')[0]), y: Number.parseFloat(style.transformOrigin.split(' ')[1]), width: element.offsetWidth, height: element.offsetHeight };
  });
  expect(uploadOrigin.x).toBeCloseTo(uploadOrigin.width);
  expect(uploadOrigin.y).toBeCloseTo(uploadOrigin.height, 0);
  await page.locator('.plus-menu-scrim').click({ position: { x: 10, y: 10 } });

  const history = page.getByRole('button', { name: 'Session history' });
  const historyPanel = page.locator('.session-history-panel');
  await history.click();
  await expect(historyPanel).toHaveAttribute('data-state', 'open');
  await history.click();
  await expect(historyPanel).toHaveAttribute('data-state', 'closed');
  await history.click();
  await expect(historyPanel).toHaveAttribute('data-state', 'open');
  await expect.poll(() => historyPanel.evaluate(element => getComputedStyle(element).transformOrigin)).toMatch(/^0px 0px/);
});

test('keeps repeated Toast feedback interruptible and restores Modal focus', async ({ page }) => {
  await prepare(page);
  await page.getByRole('button', { name: /开发者详情/ }).click();
  const demo = page.getByRole('button', { name: /演示/ });
  for (const label of ['成功', '失败', '信息']) {
    await demo.click();
    await page.getByRole('button', { name: new RegExp(label) }).click();
  }
  await expect(page.locator('.toast')).toHaveCount(3);
  await expect(page.locator('.toast').first()).toHaveCSS('transition-property', /transform/);

  const config = page.getByRole('button', { name: '配置' });
  await config.click();
  await expect(page.getByRole('dialog', { name: '配置 API Key' })).toBeVisible();
  await expect(page.getByPlaceholder('sk-…')).toBeFocused();
  await page.getByRole('button', { name: '取消' }).click();
  await expect(config).toBeFocused();
});
