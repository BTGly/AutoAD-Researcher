import type { ExperimentActivity } from '../lib/types';

interface Props {
  activity: ExperimentActivity[];
  truncated: boolean;
  scanTruncated: boolean;
  limit: number;
  selectedId: number | null;
  onSelect: (item: ExperimentActivity) => void;
}

export function ActivityFeed({ activity, truncated, scanTruncated, limit, selectedId, onSelect }: Props) {
  if (activity.length === 0) return <div style={{ color: 'var(--text-muted)', padding: 12 }}>{scanTruncated ? '为控制读取开销，较早动态未完成扫描。' : '暂无实验动态'}</div>;
  return <div style={{ display: 'grid', gap: 8 }}>
    {activity.map(item => <button key={item.event_id} onClick={() => onSelect(item)} style={{ textAlign: 'left', padding: 9, borderRadius: 6, cursor: 'pointer', border: `1px solid ${selectedId === item.event_id ? 'var(--blue)' : 'var(--border)'}`, background: selectedId === item.event_id ? 'var(--bg)' : 'var(--bg-panel)', color: 'var(--text)' }}>
      <div style={{ fontWeight: 600, fontSize: '0.9em' }}>{item.title}</div>
      <div style={{ marginTop: 3, color: 'var(--text-muted)', fontSize: '0.82em' }}>{item.summary}</div>
      <div style={{ marginTop: 5, color: 'var(--text-dim)', fontSize: '0.72em' }}>{item.created_at}</div>
    </button>)}
    {truncated && <div style={{ color: 'var(--text-dim)', fontSize: '0.8em', padding: 8 }}>仅显示最近 {limit} 条动态</div>}
    {scanTruncated && <div style={{ color: 'var(--text-dim)', fontSize: '0.8em', padding: 8 }}>为控制读取开销，较早动态未完成扫描</div>}
  </div>;
}
