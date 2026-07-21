import { devices, expect, test, type Page, type TestInfo } from '@playwright/test';

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

const pendingTask = {
  schema_version: 1, task_id: 'task_motion_000001', run_id: run.run_id, status: 'pending_confirmation', execution_mode: 'plan_only',
  input_task: { run_id: run.run_id, request: '验证异步确认焦点', source_ids: [], target_domain: null, user_idea: null, baseline: null, dataset: null, compute_budget: null, primary_metrics: ['image_auroc'], constraints: [] },
  evidence_refs: [], summary_sha256: 'a'.repeat(64), created_at: '2026-07-21T00:00:00Z', confirmed_at: null,
};

async function prepare(page: Page, options: { reducedMotion?: 'reduce' | 'no-preference'; chatTask?: object | null } = {}) {
  if (options.reducedMotion) await page.emulateMedia({ reducedMotion: options.reducedMotion });
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
    if (path === `/api/runs/${run.run_id}/report`) return route.fulfill({ json: { content: '' } });
    if (path === '/api/chat/send') return route.fulfill({ json: { reply: '已生成实验草案。', reply_kind: 'answer', source_action: null, experiment_task: options.chatTask || null } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  await expect(page.getByPlaceholder('输入问题，或粘贴 URL…')).toBeVisible();
}

test('keeps the Sidebar footprint fixed and supports keyboard and touch-safe expansion', async ({ page }, testInfo: TestInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepare(page);
  const sidebar = page.locator('.project-sidebar');
  const chat = page.locator('.chat-workspace');
  const leftBefore = await chat.evaluate(element => element.getBoundingClientRect().left);
  const toggle = page.getByRole('button', { name: '展开导航' });
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: '收起导航' })).toHaveAttribute('aria-expanded', 'true');
  const leftAfter = await chat.evaluate(element => element.getBoundingClientRect().left);
  expect(leftAfter).toBe(leftBefore);
  await expect(sidebar).toHaveClass(/expanded/);
  await page.screenshot({ path: testInfo.outputPath('sidebar-expanded.png'), fullPage: true });
  await page.getByRole('button', { name: '收起导航' }).click();
  await expect(page.getByRole('button', { name: '展开导航' })).toHaveAttribute('aria-expanded', 'false');
  await expect(sidebar).not.toHaveClass(/expanded/);

  await page.mouse.move(1000, 500);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByRole('button', { name: '展开导航' })).toBeVisible();
  await page.getByRole('button', { name: '展开导航' }).click();
  await expect(page.getByRole('button', { name: '实验工作台' })).toBeVisible();
  await page.getByRole('button', { name: '实验工作台' }).click();
  await expect(page.getByText('实验尚未启动。')).toBeVisible();
});

test('keeps non-vestibular feedback under Reduced Motion', async ({ page }) => {
  await prepare(page, { reducedMotion: 'reduce' });
  await page.getByRole('button', { name: '展开导航' }).click();
  const motion = await page.locator('.project-sidebar-label').first().evaluate(element => {
    const style = getComputedStyle(element);
    return { transform: style.transform, duration: style.transitionDuration };
  });
  expect(motion.transform).toBe('none');
  expect(Number.parseFloat(motion.duration)).toBeGreaterThan(0);
  const panelTransform = await page.locator('.project-sidebar').evaluate(element => getComputedStyle(element, '::before').transform);
  expect(panelTransform).toBe('none');

  await page.getByRole('button', { name: '实验工作台' }).click();
  await page.getByRole('button', { name: '实验配置' }).click();
  const searchSwitch = page.getByRole('switch', { name: '启用文献检索' });
  await expect(searchSwitch).toHaveAttribute('aria-checked', 'false');
  const thumbMotion = await page.locator('.settings-toggle-thumb').first().evaluate(element => getComputedStyle(element).transitionDuration);
  expect(Number.parseFloat(thumbMotion)).toBeLessThan(0.02);
});

test('can rapidly reverse anchored Popovers without a stuck scrim', async ({ page }) => {
  await prepare(page);
  const upload = page.getByRole('button', { name: '上传文件' });
  const uploadPopover = page.locator('.plus-menu-popover');
  await upload.click();
  await expect(uploadPopover).toHaveAttribute('data-state', 'open');
  await upload.click();
  await expect(uploadPopover).toHaveAttribute('data-state', 'closed');
  await upload.click();
  await expect(uploadPopover).toHaveAttribute('data-state', 'open');
  const uploadOrigin = await uploadPopover.evaluate(element => {
    const style = getComputedStyle(element);
    return { x: Number.parseFloat(style.transformOrigin.split(' ')[0]), y: Number.parseFloat(style.transformOrigin.split(' ')[1]), width: element.offsetWidth, height: element.offsetHeight };
  });
  expect(uploadOrigin.x).toBeCloseTo(uploadOrigin.width);
  expect(uploadOrigin.y).toBeCloseTo(uploadOrigin.height, 0);
  await upload.click();
  await expect(uploadPopover).toHaveAttribute('data-state', 'closed');

  const history = page.getByRole('button', { name: 'Session history' });
  const historyPanel = page.locator('.session-history-panel');
  await history.click();
  await expect(historyPanel).toHaveAttribute('data-state', 'open');
  await history.click();
  await expect(historyPanel).toHaveAttribute('data-state', 'closed');
  await history.click();
  await expect(historyPanel).toHaveAttribute('data-state', 'open');
  const historyOrigin = await historyPanel.evaluate(element => getComputedStyle(element).transformOrigin);
  expect(historyOrigin.startsWith('0px 0px')).toBe(true);
});

test('keeps repeated Toast feedback interruptible and restores Modal focus', async ({ page }) => {
  await prepare(page);
  const developerDetails = page.getByRole('button', { name: /开发者详情/ });
  await developerDetails.click();
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

  await page.getByRole('button', { name: '实验工作台' }).click();
  const experimentSettings = page.getByRole('button', { name: '实验配置' });
  await experimentSettings.click();
  await expect(page.getByRole('dialog', { name: '实验配置' })).toBeVisible();
  await page.getByRole('button', { name: '返回工作台' }).click();
  await expect(experimentSettings).toBeFocused();
});

test('restores focus for an asynchronously opened experiment confirmation', async ({ page }) => {
  await prepare(page, { chatTask: pendingTask });
  const composer = page.getByPlaceholder('输入问题，或粘贴 URL…');
  const send = page.getByRole('button', { name: '发送' });
  await composer.fill('请生成实验草案');
  await send.click();
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toBeVisible();
  await expect(page.getByLabel('执行模式')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '确认实验任务' })).toHaveCount(0);
  await expect(composer).toBeFocused();
});

test.describe('real touch input', () => {
  test.use({
    hasTouch: true,
    isMobile: true,
    viewport: { width: 390, height: 844 },
    userAgent: devices['iPhone 13'].userAgent,
  });

  test('expands, collapses, and navigates with real touch input', async ({ page }) => {
    await prepare(page);
    const expand = page.getByRole('button', { name: '展开导航' });
    await expand.tap();
    const collapse = page.getByRole('button', { name: '收起导航' });
    await expect(collapse).toHaveAttribute('aria-expanded', 'true');
    await collapse.tap();
    await expect(page.getByRole('button', { name: '展开导航' })).toHaveAttribute('aria-expanded', 'false');
    await expand.tap();
    await page.getByRole('button', { name: '实验工作台' }).tap();
    await expect(page.getByText('实验尚未启动。')).toBeVisible();
  });
});
