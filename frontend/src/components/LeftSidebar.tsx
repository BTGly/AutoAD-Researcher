import { useState } from 'react';
import type { PageId } from '../lib/types';

interface Props {
  page: PageId;
  onPage: (p: PageId) => void;
}

const ITEMS: { id: PageId; icon: string; label: string }[] = [
  { id: 'chat', icon: '💬', label: 'Chat' },
  { id: 'experiment', icon: '🔬', label: '实验工作台' },
  { id: 'report', icon: '📊', label: 'Report' },
];

export function LeftSidebar({ page, onPage }: Props) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      style={{
        width: expanded ? 160 : 48,
        flexShrink: 0,
        borderRight: '1px solid var(--border)',
        background: 'var(--bg-panel)',
        display: 'flex',
        flexDirection: 'column',
        transition: 'width 0.15s ease',
        overflow: 'hidden',
      }}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
    >
      {ITEMS.map(item => (
        <button
          key={item.id}
          onClick={() => onPage(item.id)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            width: '100%',
            padding: '12px 14px',
            border: 'none',
            background: page === item.id ? 'var(--bg)' : 'transparent',
            color: page === item.id ? 'var(--blue)' : 'var(--text-muted)',
            fontSize: '0.85em',
            cursor: 'pointer',
            whiteSpace: 'nowrap',
            borderLeft: page === item.id ? '2px solid var(--blue)' : '2px solid transparent',
          }}
          title={item.label}
        >
          <span style={{ fontSize: '1.2em', flexShrink: 0 }}>{item.icon}</span>
          <span style={{
            opacity: expanded ? 1 : 0,
            transition: 'opacity 0.1s ease',
          }}>
            {item.label}
          </span>
        </button>
      ))}
    </div>
  );
}
