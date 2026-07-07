import { useEffect } from 'react';
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
  useEffect(() => {
    const timer = setTimeout(() => onRemove(item.id), 3500);
    return () => clearTimeout(timer);
  }, [item.id, onRemove]);

  const icon = item.kind === 'success' ? '✅' : item.kind === 'error' ? '❌' : 'ℹ️';
  return (
    <div className={`toast ${item.kind}`}>
      <span>{icon}</span>
      <span>{item.message}</span>
    </div>
  );
}
