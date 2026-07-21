import { useState } from 'react';
import { BarChart3, FlaskConical, MessageSquare, PanelLeftClose, PanelLeftOpen, type LucideIcon } from 'lucide-react';
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
  const [pinned, setPinned] = useState(false);
  const [hoverSuppressed, setHoverSuppressed] = useState(false);
  const isExpanded = expanded || pinned;

  return (
    <nav
      className={`project-sidebar${isExpanded ? ' expanded' : ''}`}
      aria-label="主要导航"
      onMouseEnter={() => { if (!hoverSuppressed) setExpanded(true); }}
      onMouseLeave={() => { setHoverSuppressed(false); if (!pinned) setExpanded(false); }}
      onFocusCapture={event => {
        if ((event.target as HTMLElement).closest('.project-sidebar-toggle')) return;
        setExpanded(true);
      }}
      onBlurCapture={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null) && !pinned) setExpanded(false);
      }}
    >
      <button
        type="button"
        className="project-sidebar-toggle"
        aria-expanded={isExpanded}
        aria-label={isExpanded ? '收起导航' : '展开导航'}
        title={isExpanded ? '收起导航' : '展开导航'}
        onClick={() => {
          const nextExpanded = !isExpanded;
          setPinned(nextExpanded);
          setExpanded(nextExpanded);
          setHoverSuppressed(!nextExpanded);
        }}
      >
        {isExpanded ? <PanelLeftClose size={18} strokeWidth={1.8} aria-hidden="true" /> : <PanelLeftOpen size={18} strokeWidth={1.8} aria-hidden="true" />}
        <span className="project-sidebar-label">{isExpanded ? '收起导航' : '展开导航'}</span>
      </button>
      {ITEMS.map(item => (
        <button
          key={item.id}
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
