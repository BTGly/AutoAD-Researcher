import { useEffect, useState } from 'react';
import { CircleCheck, CircleX, Info } from 'lucide-react';
import type { ToastItem } from '../lib/types';

interface Props {
  toasts: ToastItem[];
  onRemove: (id: string) => void;
}

export function ToastContainer({ toasts, onRemove }: Props) {
  return (
    <div className="toast-container">
      {toasts.map(t => (
        <Toast key={t.id} item={t} onRemove={onRemove} />
      ))}
    </div>
  );
}

function Toast({ item, onRemove }: { item: ToastItem; onRemove: (id: string) => void }) {
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setLeaving(true), 3320);
    return () => clearTimeout(timer);
  }, [item.id]);

  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => onRemove(item.id), 180);
    return () => window.clearTimeout(timer);
  }, [item.id, leaving, onRemove]);

  const Icon = item.kind === 'success' ? CircleCheck : item.kind === 'error' ? CircleX : Info;
  return (
    <div className={`toast ${item.kind}${leaving ? ' is-leaving' : ''}`} role={item.kind === 'error' ? 'alert' : 'status'} aria-atomic="true">
      <Icon size={16} aria-hidden="true" />
      <span>{item.message}</span>
    </div>
  );
}
