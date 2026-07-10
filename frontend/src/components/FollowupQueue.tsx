import { ChevronDown, Pencil, Play, Trash2 } from 'lucide-react';
import { useState } from 'react';
import type { QueuedChatMessage } from '../lib/types';

interface Props {
  items: QueuedChatMessage[];
  paused: boolean;
  waitingForConfirmation: boolean;
  onEdit: (id: string) => void;
  onRemove: (id: string) => void;
  onResume: () => void;
}

export function FollowupQueue({
  items,
  paused,
  waitingForConfirmation,
  onEdit,
  onRemove,
  onResume,
}: Props) {
  const [collapsed, setCollapsed] = useState(false);
  if (items.length === 0) return null;

  const stateLabel = waitingForConfirmation
    ? '等待合同确认'
    : paused
      ? '自动发送已暂停'
      : '将在当前回复完成后发送';

  return (
    <section className="followup-queue" aria-label="已排队消息">
      <div className="followup-queue-heading">
        <button
          type="button"
          className="followup-queue-toggle"
          onClick={() => setCollapsed(value => !value)}
          aria-expanded={!collapsed}
        >
          <span>已排队 {items.length} 条</span>
          {collapsed && <span className="followup-queue-preview">{items[0].content}</span>}
          <ChevronDown className={collapsed ? 'collapsed' : ''} size={16} aria-hidden="true" />
        </button>
        <span className="followup-queue-state">{stateLabel}</span>
        {paused && !waitingForConfirmation && (
          <button type="button" className="followup-queue-resume" onClick={onResume} title="继续自动发送">
            <Play size={14} aria-hidden="true" />
            继续
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="followup-queue-list">
          {items.map(item => (
            <div className="followup-queue-row" key={item.id}>
              <span className="followup-queue-text">{item.content}</span>
              <button type="button" className="followup-queue-action" onClick={() => onEdit(item.id)} title="编辑排队消息" aria-label="编辑排队消息">
                <Pencil size={15} aria-hidden="true" />
              </button>
              <button type="button" className="followup-queue-action danger" onClick={() => onRemove(item.id)} title="撤回排队消息" aria-label="撤回排队消息">
                <Trash2 size={15} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
