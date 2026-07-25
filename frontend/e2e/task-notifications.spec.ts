import { expect, test } from '@playwright/test';

const run = {
  run_id: 'run_task_notifications', created_at: null, updated_at: null, sources_count: 1,
  task_title: '后台资料任务', task_summary: '', task_source: 'fixture', task_profile_warning: null, archived_at: null,
};

test('inserts a durable task result from WebSocket and restores it once from transcript', async ({ page }) => {
  let transcript: object[] = [];
  await page.addInitScript(() => {
    type Listener = ((event: { data: string }) => void) | null;
    const sockets: Array<{ onmessage: Listener; readyState: number }> = [];
    class FixtureWebSocket {
      static CLOSED = 3;
      readyState = 1;
      onopen: (() => void) | null = null;
      onmessage: Listener = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor() { sockets.push(this); }
      close() { this.readyState = FixtureWebSocket.CLOSED; this.onclose?.(); }
    }
    Object.defineProperty(window, 'WebSocket', { value: FixtureWebSocket });
    (window as typeof window & { emitTaskResult: (message: object) => number }).emitTaskResult = message => {
      let delivered = 0;
      for (const socket of sockets) {
        if (socket.onmessage) {
          socket.onmessage({ data: JSON.stringify(message) });
          delivered += 1;
        }
      }
      return delivered;
    };
    localStorage.setItem('autoad_config', JSON.stringify({ apiKey: 'e2e-key', baseUrl: 'http://example.invalid', model: 'fixture' }));
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === `/api/runs/${run.run_id}/transcript`) return route.fulfill({ json: transcript });
    if (path === `/api/runs/${run.run_id}/sources`) {
      return route.fulfill({ json: [{ source_id: 'src_mvtec', kind: 'dataset', user_label: 'MVTec AD', status: 'registered' }] });
    }
    if (path === `/api/runs/${run.run_id}/jobs`) {
      return route.fulfill({ json: [{ job_id: 'job_000001', job_type: 'dataset_manifest', status: 'completed', outputs: ['sources/src_mvtec/dataset_manifest.json'] }] });
    }
    if (path === `/api/runs/${run.run_id}/evidence/state`) return route.fulfill({ json: { usable_evidence: [], unusable_parsed_sources: [] } });
    if (path === `/api/runs/${run.run_id}/intent-summary`) return route.fulfill({ json: { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null } });
    if (path === `/api/runs/${run.run_id}/experiment-task/pending`) return route.fulfill({ status: 404, json: { detail: 'not found' } });
    return route.fulfill({ json: {} });
  });

  const taskResult = {
    type: 'conversation.message.created', event_id: 7, created_at: '2026-07-25T00:00:00Z',
    message_id: 'msg_task_1', message_kind: 'task_result', job_id: 'job_000001', source_id: 'src_mvtec',
    artifact_paths: ['sources/src_mvtec/dataset_manifest.json'], evidence_ids: [],
    content: '**数据集清单生成已完成**：MVTec AD。',
  };
  await page.goto('/');
  await expect(page.getByPlaceholder('输入问题，或粘贴 URL…')).toBeVisible();
  await expect.poll(() => page.evaluate(
    message => (window as typeof window & { emitTaskResult: (value: object) => number }).emitTaskResult(message),
    taskResult,
  )).toBeGreaterThan(0);
  await expect(page.locator('.message-task')).toHaveCount(1);
  await expect(page.getByText('数据集清单生成已完成')).toBeVisible();
  await expect(page.getByText('产物：sources/src_mvtec/dataset_manifest.json')).toBeVisible();

  transcript = [{
    role: 'assistant', content: taskResult.content, created_at: taskResult.created_at,
    message_id: taskResult.message_id, message_kind: taskResult.message_kind,
    job_id: taskResult.job_id, source_id: taskResult.source_id,
    artifact_paths: taskResult.artifact_paths, evidence_ids: [], error: null,
  }];
  await page.reload();
  await expect(page.locator('.message-task')).toHaveCount(1);
  await expect(page.getByText('数据集清单生成已完成')).toBeVisible();
});
