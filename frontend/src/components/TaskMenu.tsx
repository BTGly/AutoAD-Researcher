import { useEffect, useRef, useState } from 'react';
import type { TaskRun } from '../lib/types';

interface Props {
  activeTask: TaskRun | null;
  tasks: TaskRun[];
  onSelect: (runId: string) => void;
  onCreate: (title?: string) => void;
  onRename: (title: string) => void;
  onArchive: () => void;
  onRestore: (runId: string) => void;
  onDelete: (runId: string) => void;
}

export function TaskMenu({
  activeTask,
  tasks,
  onSelect,
  onCreate,
  onRename,
  onArchive,
  onRestore,
  onDelete,
}: Props) {
  const [open, setOpen] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [renameTitle, setRenameTitle] = useState('');
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setRenameTitle(activeTask?.task_title || '');
  }, [activeTask?.run_id, activeTask?.task_title]);

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener('mousedown', handleClick);
    return () => window.removeEventListener('mousedown', handleClick);
  }, []);

  const activeTitle = activeTask?.task_title || '未选择任务';
  const archivedTasks = tasks.filter(task => task.archived_at);
  const liveTasks = tasks.filter(task => !task.archived_at);

  return (
    <div className="task-menu" ref={ref}>
      <button
        className="task-menu-trigger"
        onClick={() => setOpen(value => !value)}
        title="切换或管理当前任务"
      >
        <span className="task-menu-label">当前任务：</span>
        <span className="task-menu-title">{activeTitle}</span>
        <span className="task-menu-chevron">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="task-menu-panel">
          <div className="task-menu-section">
            <div className="task-menu-heading">任务列表</div>
            <div className="task-menu-list">
              {liveTasks.length === 0 && <div className="task-menu-empty">暂无任务</div>}
              {liveTasks.map(task => (
                <button
                  key={task.run_id}
                  className={`task-menu-item${task.run_id === activeTask?.run_id ? ' active' : ''}`}
                  onClick={() => {
                    onSelect(task.run_id);
                    setOpen(false);
                  }}
                >
                  <span>{task.task_title}</span>
                  <span className="task-menu-meta">{formatDate(task.updated_at || task.created_at)}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="task-menu-section">
            <div className="task-menu-heading">新建任务</div>
            <div className="task-menu-row">
              <input
                value={newTitle}
                onChange={event => setNewTitle(event.target.value)}
                placeholder="任务标题"
                maxLength={30}
              />
              <button
                className="primary"
                onClick={() => {
                  onCreate(newTitle.trim() || undefined);
                  setNewTitle('');
                  setOpen(false);
                }}
              >
                新建
              </button>
            </div>
          </div>

          {activeTask && (
            <div className="task-menu-section">
              <div className="task-menu-heading">当前任务</div>
              <div className="task-menu-row">
                <input
                  value={renameTitle}
                  onChange={event => setRenameTitle(event.target.value)}
                  maxLength={30}
                />
                <button
                  onClick={() => {
                    const nextTitle = renameTitle.trim();
                    if (nextTitle) onRename(nextTitle);
                  }}
                >
                  重命名
                </button>
              </div>
              <div className="task-menu-actions">
                <button onClick={onArchive}>归档</button>
              </div>
            </div>
          )}

          {archivedTasks.length > 0 && (
            <div className="task-menu-section">
              <div className="task-menu-heading">已归档</div>
              <div className="task-menu-list">
                {archivedTasks.map(task => (
                  <div key={task.run_id} className="task-menu-archived">
                    <span>{task.task_title}</span>
                    <div className="task-menu-actions">
                      <button onClick={() => onRestore(task.run_id)}>恢复</button>
                      <button className="danger" onClick={() => onDelete(task.run_id)}>删除</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatDate(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { month: '2-digit', day: '2-digit' });
}
