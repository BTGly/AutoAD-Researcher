import { Inbox } from 'lucide-react';

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return <div className="empty-state"><Inbox size={32} strokeWidth={1.5} aria-hidden="true" /><strong>{title}</strong>{detail && <span>{detail}</span>}</div>;
}
