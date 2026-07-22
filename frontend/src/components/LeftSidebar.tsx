import { BarChart3, FlaskConical, MessageSquare, type LucideIcon } from 'lucide-react';
import { useState } from 'react';
import type { PageId } from '../lib/types';

interface Props {
  page: PageId;
  onPage: (p: PageId) => void;
}

const ITEMS: { id: PageId; icon: LucideIcon; label: string }[] = [
  { id: 'chat', icon: MessageSquare, label: '研究对话' },
  { id: 'experiment', icon: FlaskConical, label: '实验工作台' },
  { id: 'report', icon: BarChart3, label: '研究报告' },
];

export function LeftSidebar({ page, onPage }: Props) {
  const [expanded, setExpanded] = useState(false);

  return (
    <nav
      className={`project-sidebar${expanded ? ' expanded' : ''}`}
      aria-label="主要导航"
      onPointerEnter={event => {
        if (event.pointerType === 'mouse') setExpanded(true);
      }}
      onPointerLeave={event => {
        if (event.pointerType === 'mouse') setExpanded(false);
      }}
      onFocusCapture={() => setExpanded(true)}
      onBlurCapture={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setExpanded(false);
      }}
    >
      {ITEMS.map(item => (
        <button
          key={item.id}
          type="button"
          onClick={() => onPage(item.id)}
          className={`project-sidebar-item${page === item.id ? ' active' : ''}`}
          aria-current={page === item.id ? 'page' : undefined}
          title={item.label}
        >
          <item.icon size={18} strokeWidth={1.8} aria-hidden="true" />
          <span className="project-sidebar-label">{item.label}</span>
        </button>
      ))}
    </nav>
  );
}
