import { useState } from 'react';
import type { ExperimentIdeaNode } from '../lib/types';

interface Props {
  nodes: ExperimentIdeaNode[];
  championIdeaId: string | null;
  selectedId: string | null;
  onSelect: (node: ExperimentIdeaNode) => void;
}

const STATUS_STYLE: Record<string, React.CSSProperties> = {
  DRAFT: { borderStyle: 'dashed' },
  RUNNING: { borderColor: 'var(--blue)' },
  SUPPORTED: { borderColor: 'var(--green)', color: 'var(--green)' },
  NOT_SUPPORTED: { opacity: 0.62 },
  INCONCLUSIVE: { borderColor: 'var(--orange)', borderStyle: 'dashed' },
  PRUNED: { color: 'var(--text-dim)', opacity: 0.68 },
};

export function IdeaTree({ nodes, championIdeaId, selectedId, onSelect }: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const byParent = new Map<string | null, ExperimentIdeaNode[]>();
  for (const node of nodes) {
    const current = byParent.get(node.parent_id) || [];
    current.push(node);
    byParent.set(node.parent_id, current);
  }
  const roots = nodes.filter(node => node.is_root);
  return (
    <div style={{ fontSize: '0.82em' }}>
      {roots.map(node => <Node key={node.node_id} node={node} byParent={byParent} championIdeaId={championIdeaId} selectedId={selectedId} expanded={expanded} setExpanded={setExpanded} onSelect={onSelect} />)}
    </div>
  );
}

function Node({ node, byParent, championIdeaId, selectedId, expanded, setExpanded, onSelect }: {
  node: ExperimentIdeaNode;
  byParent: Map<string | null, ExperimentIdeaNode[]>;
  championIdeaId: string | null;
  selectedId: string | null;
  expanded: Record<string, boolean>;
  setExpanded: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  onSelect: (node: ExperimentIdeaNode) => void;
}) {
  const children = byParent.get(node.node_id) || [];
  const defaultOpen = node.is_root || node.status !== 'PRUNED';
  const open = expanded[node.node_id] ?? defaultOpen;
  const label = node.mechanism || '未记录机制';
  return (
    <div style={{ marginLeft: node.depth ? 12 : 0, marginTop: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        {children.length > 0 ? <button aria-label={`${open ? '折叠' : '展开'} ${label}`} onClick={() => setExpanded(value => ({ ...value, [node.node_id]: !open }))} style={{ padding: 0, width: 16, border: 0, background: 'transparent', color: 'var(--text-muted)' }}>{open ? '⌄' : '›'}</button> : <span style={{ width: 16 }} />}
        <button onClick={() => onSelect(node)} style={{
          flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6, textAlign: 'left',
          padding: '5px 7px', borderRadius: 5, borderWidth: 1, borderStyle: 'solid', borderColor: selectedId === node.node_id ? 'var(--blue)' : 'var(--border)',
          background: selectedId === node.node_id ? 'var(--bg)' : 'transparent', color: 'var(--text)', cursor: 'pointer', ...STATUS_STYLE[node.status],
        }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
          {championIdeaId === node.node_id && <span title="当前 Champion">★</span>}
          {node.attempt_refs.length > 0 && <span style={{ marginLeft: 'auto', color: 'var(--text-dim)' }}>{node.attempt_refs.length}</span>}
        </button>
      </div>
      {open && children.map(child => <Node key={child.node_id} node={child} byParent={byParent} championIdeaId={championIdeaId} selectedId={selectedId} expanded={expanded} setExpanded={setExpanded} onSelect={onSelect} />)}
    </div>
  );
}
