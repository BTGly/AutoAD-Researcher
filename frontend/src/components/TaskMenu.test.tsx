import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../lib/api';
import type { TaskRun } from '../lib/types';
import { TaskMenu } from './TaskMenu';

afterEach(cleanup);

const task: TaskRun = {
  run_id: 'run_keep_this_id',
  created_at: '2026-07-14T00:00:00Z',
  updated_at: '2026-07-14T00:00:00Z',
  sources_count: 0,
  task_title: '未命名研究任务',
  task_summary: '',
  task_source: 'default',
  task_profile_warning: null,
  archived_at: null,
};

function renderMenu(onRename = vi.fn<(title: string) => Promise<TaskRun>>()) {
  return render(
    <TaskMenu
      activeTask={task}
      tasks={[task]}
      onSelect={vi.fn()}
      onCreate={vi.fn()}
      onRename={onRename}
      onDelete={vi.fn()}
    />,
  );
}

describe('TaskMenu current title editor', () => {
  it('keeps the current-title edit button visible for one task', () => {
    renderMenu();
    expect(screen.getByRole('button', { name: '编辑当前任务名称' })).toBeTruthy();
    expect(screen.getByText('未命名研究任务')).toBeTruthy();
  });

  it('submits once when Enter is followed by blur and updates immediately', async () => {
    let resolveRename: ((value: TaskRun) => void) | undefined;
    const onRename = vi.fn(() => new Promise<TaskRun>(resolve => { resolveRename = resolve; }));
    renderMenu(onRename);

    fireEvent.click(screen.getByRole('button', { name: '编辑当前任务名称' }));
    const input = screen.getByRole('textbox', { name: '当前任务名称' });
    fireEvent.change(input, { target: { value: '异常检测改进' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.blur(input);

    expect(onRename).toHaveBeenCalledTimes(1);
    resolveRename?.({ ...task, task_title: '异常检测改进' });
    await waitFor(() => expect(screen.getByText('异常检测改进')).toBeTruthy());
  });

  it('submits on blur and sends no request for an empty or unchanged title', async () => {
    const onRename = vi.fn(async (title: string) => ({ ...task, task_title: title }));
    const view = renderMenu(onRename);

    fireEvent.click(screen.getByRole('button', { name: '编辑当前任务名称' }));
    let input = screen.getByRole('textbox', { name: '当前任务名称' });
    fireEvent.change(input, { target: { value: '失焦保存标题' } });
    fireEvent.blur(input);
    await waitFor(() => expect(onRename).toHaveBeenCalledWith('失焦保存标题'));

    view.rerender(
      <TaskMenu
        activeTask={task}
        tasks={[task]}
        onSelect={vi.fn()}
        onCreate={vi.fn()}
        onRename={onRename}
        onDelete={vi.fn()}
      />,
    );
    onRename.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '编辑当前任务名称' }));
    input = screen.getByRole('textbox', { name: '当前任务名称' });
    fireEvent.blur(input);
    expect(onRename).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '编辑当前任务名称' }));
    input = screen.getByRole('textbox', { name: '当前任务名称' });
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.blur(input);
    expect(onRename).not.toHaveBeenCalled();
  });

  it('cancels with Escape', () => {
    const onRename = vi.fn(async (title: string) => ({ ...task, task_title: title }));
    renderMenu(onRename);
    fireEvent.click(screen.getByRole('button', { name: '编辑当前任务名称' }));
    const input = screen.getByRole('textbox', { name: '当前任务名称' });
    fireEvent.change(input, { target: { value: '不保存' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onRename).not.toHaveBeenCalled();
    expect(screen.getByText('未命名研究任务')).toBeTruthy();
  });

  it('keeps the input and displays the backend detail after failure', async () => {
    const onRename = vi.fn(async () => {
      throw new ApiError('标题已经被占用', 409, '标题已经被占用');
    });
    renderMenu(onRename);
    fireEvent.click(screen.getByRole('button', { name: '编辑当前任务名称' }));
    const input = screen.getByRole('textbox', { name: '当前任务名称' });
    fireEvent.change(input, { target: { value: '保留这个输入' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('标题已经被占用'));
    expect((screen.getByRole('textbox', { name: '当前任务名称' }) as HTMLInputElement).value).toBe('保留这个输入');
  });

  it('shows a persisted backend title after remount without changing run_id', () => {
    const persisted = { ...task, task_title: '刷新后标题' };
    const first = renderMenu();
    first.unmount();
    render(
      <TaskMenu
        activeTask={persisted}
        tasks={[persisted]}
        onSelect={vi.fn()}
        onCreate={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByText('刷新后标题')).toBeTruthy();
    expect(persisted.run_id).toBe(task.run_id);
  });
});
