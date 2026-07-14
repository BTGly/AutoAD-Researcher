import { describe, expect, it } from 'vitest';

import type { Message, TaskRun } from './types';
import { applyAssistantProgress, applyTaskUpdated } from './wsUpdates';

const messages: Message[] = [
  { id: 'assistant-1', role: 'assistant', content: '', timestamp: 1 },
];

const tasks: TaskRun[] = [{
  run_id: 'run_one',
  created_at: '2026-07-14T00:00:00Z',
  updated_at: '2026-07-14T00:00:00Z',
  sources_count: 0,
  task_title: '未命名研究任务',
  task_summary: '用户创建的研究任务。',
  task_source: 'ui',
  task_profile_warning: null,
  archived_at: null,
}];

describe('WebSocket user-facing updates', () => {
  it('shows server progress in the pending assistant bubble', () => {
    const updated = applyAssistantProgress(messages, {
      type: 'assistant.progress',
      message_id: 'assistant-1',
      content: '正在理解你的任务……',
    });

    expect(updated[0].content).toBe('正在理解你的任务……');
  });

  it('updates the task title without changing run identity', () => {
    const updated = applyTaskUpdated(tasks, {
      type: 'task.updated',
      run_id: 'run_one',
      task_title: 'PatchCore MVTec AUROC优化',
      task_summary: '提升图像级 AUROC。',
      task_source: 'router_suggested',
      updated_at: '2026-07-14T01:00:00Z',
    });

    expect(updated[0].run_id).toBe('run_one');
    expect(updated[0].task_title).toBe('PatchCore MVTec AUROC优化');
    expect(updated[0].task_source).toBe('router_suggested');
  });
});
