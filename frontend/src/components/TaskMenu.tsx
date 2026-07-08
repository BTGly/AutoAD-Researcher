import { useEffect, useMemo, useRef, useState } from 'react';
import { History, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import type { TaskRun } from '../lib/types';

interface Props {
  activeTask: TaskRun | null;
  tasks: TaskRun[];
  onSelect: (runId: string) => void;
  onCreate: () => void;
  onRename: (title: string) => void;
  onDelete: (runId: string) => void;
}

export function TaskMenu({
  activeTask,
  tasks,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState(false);
  const [renameTitle, setRenameTitle] = useState('');
  const ref = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setRenameTitle(activeTask?.task_title || '');
    setEditing(false);
  }, [activeTask?.run_id, activeTask?.task_title]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
        setEditing(false);
      }
    };
    window.addEventListener('mousedown', handleClick);
    return () => window.removeEventListener('mousedown', handleClick);
  }, []);

  const filteredTasks = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const ordered = [...tasks].sort((a, b) => {
      if (a.run_id === activeTask?.run_id) return -1;
      if (b.run_id === activeTask?.run_id) return 1;
      return String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || ''));
    });
    if (!needle) return ordered;
    return ordered.filter(task => (
      task.task_title.toLowerCase().includes(needle)
      || task.run_id.toLowerCase().includes(needle)
      || task.task_summary.toLowerCase().includes(needle)
    ));
  }, [activeTask?.run_id, query, tasks]);

  const activeTitle = activeTask?.task_title || 'Untitled session';

  const submitRename = () => {
    const nextTitle = renameTitle.trim();
    if (nextTitle && nextTitle !== activeTask?.task_title) {
      onRename(nextTitle);
    }
    setEditing(false);
  };

  return (
    <div className="session-controls" ref={ref}>
      <div className="session-current" title={activeTitle}>
        <span className="session-current-label">当前任务：</span>
        <span className="session-current-title">{activeTitle}</span>
      </div>

      <button
        className="session-icon-button"
        onClick={() => {
          setOpen(value => !value);
          setEditing(false);
        }}
        title="Session history"
        aria-label="Session history"
      >
        <History size={16} strokeWidth={1.8} />
      </button>
      <button
        className="session-icon-button"
        onClick={onCreate}
        title="New session"
        aria-label="New session"
      >
        <Plus size={17} strokeWidth={1.8} />
      </button>

      {open && (
        <div className="session-history-panel">
          <div className="session-search">
            <Search size={15} strokeWidth={1.8} />
            <input
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="Search sessions..."
            />
          </div>

          <div className="session-list">
            {filteredTasks.length === 0 && <div className="session-empty">No sessions</div>}
            {filteredTasks.map(task => {
              const isActive = task.run_id === activeTask?.run_id;
              return (
                <div
                  key={task.run_id}
                  className={`session-row${isActive ? ' active' : ''}`}
                  onClick={() => {
                    if (!editing) onSelect(task.run_id);
                  }}
                >
                  <div className="session-row-main">
                    {isActive && editing ? (
                      <input
                        ref={inputRef}
                        value={renameTitle}
                        onChange={event => setRenameTitle(event.target.value)}
                        onKeyDown={event => {
                          if (event.key === 'Enter') submitRename();
                          if (event.key === 'Escape') {
                            setRenameTitle(activeTask?.task_title || '');
                            setEditing(false);
                          }
                        }}
                        onBlur={submitRename}
                        maxLength={30}
                        onClick={event => event.stopPropagation()}
                      />
                    ) : (
                      <>
                        <div className="session-row-title">{task.task_title}</div>
                        <div className="session-row-meta">{formatDate(task.updated_at || task.created_at)}</div>
                      </>
                    )}
                  </div>

                  <div className="session-row-actions">
                    {isActive && (
                      <button
                        className="session-row-button"
                        title="Rename session"
                        aria-label="Rename session"
                        onClick={event => {
                          event.stopPropagation();
                          setEditing(true);
                        }}
                      >
                        <Pencil size={14} strokeWidth={1.8} />
                      </button>
                    )}
                    <button
                      className="session-row-button danger"
                      title="Delete session"
                      aria-label="Delete session"
                      onClick={event => {
                        event.stopPropagation();
                        onDelete(task.run_id);
                      }}
                    >
                      <Trash2 size={14} strokeWidth={1.8} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function formatDate(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}
