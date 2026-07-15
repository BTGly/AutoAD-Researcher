import { Undo2 } from 'lucide-react';
import type { QueuedChatMessage } from '../lib/types';

interface Props {
  items: QueuedChatMessage[];
  paused: boolean;
  onRestore: (id: string) => void;
}

export function FollowupQueue({ items, paused, onRestore }: Props) {
  if (items.length === 0) return null;

  return (
    <section className="followup-queue" aria-label="已排队消息">
      <div className="followup-queue-heading">
        <span className="followup-queue-summary">排队消息 · {items.length}</span>
        <span className="followup-queue-state">
          {paused ? '自动发送已暂停' : '将在当前回复完成后发送'}
        </span>
      </div>

      <div className="followup-queue-list">
        {items.map(item => (
          <div className="followup-queue-row" key={item.id}>
            <span className="followup-queue-text">{item.content}</span>
            <button
              type="button"
              className="followup-queue-action"
              onClick={() => onRestore(item.id)}
              title="撤回到输入框"
              aria-label="撤回到输入框"
            >
              <Undo2 size={15} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
