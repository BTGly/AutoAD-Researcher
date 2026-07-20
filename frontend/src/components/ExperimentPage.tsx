import { useEffect, useState } from 'react';
import { ActivityFeed } from './ActivityFeed';
import { DetailDrawer, type ExperimentDetailSelection } from './DetailDrawer';
import { IdeaTree } from './IdeaTree';
import { getExperimentProjection } from '../lib/api';
import type { ExperimentActivity, ExperimentAttempt, ExperimentIdeaNode, ExperimentProjection } from '../lib/types';

interface Props {
  runId: string;
  onOpenExperimentSettings: () => void;
  onDiscuss: (text: string) => void;
}

const ATTEMPT_STATUS_LABEL: Record<string, string> = {
  QUEUED: '等待运行', STARTING: '正在启动', RUNNING: '运行中', TERMINATING: '正在终止', COMPLETED: '已完成', FAILED: '运行失败', TIMED_OUT: '运行超时', CANCELLED: '已取消', LOST: '运行状态丢失',
};
const IDEA_STATUS_LABEL: Record<string, string> = {
  DRAFT: '草稿', REVIEWED: '已审阅', READY: '等待实验', RUNNING: '实验中', SUPPORTED: '获得证据支持', NOT_SUPPORTED: '未获得支持', INCONCLUSIVE: '证据不足', PRUNED: '已停止探索', MERGED: '已合并',
};

function label(value: string, labels: Record<string, string>) {
  return labels[value] || `未知状态（原始值：${value}）`;
}

export function ExperimentPage({ runId, onOpenExperimentSettings, onDiscuss }: Props) {
  const [projection, setProjection] = useState<ExperimentProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [selection, setSelection] = useState<ExperimentDetailSelection>(null);
  const [showDeveloper, setShowDeveloper] = useState(false);

  useEffect(() => {
    if (!runId) {
      setProjection(null);
      setError(null);
      return;
    }
    let active = true;
    setLoading(true);
    getExperimentProjection(runId, sessionId)
      .then(value => { if (active) { setProjection(value); setError(null); } })
      .catch(() => { if (active) setError('工作台刷新失败，仍保留上一份有效快照。'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [runId, sessionId]);

  const chooseSession = (next: string) => {
    setSelection(null);
    setSessionId(next || undefined);
  };
  const chooseIdea = (value: ExperimentIdeaNode) => setSelection({ kind: 'idea', value });
  const chooseAttempt = (value: ExperimentAttempt) => setSelection({ kind: 'attempt', value });
  const chooseActivity = (value: ExperimentActivity) => setSelection({ kind: 'activity', value });

  return <main style={{ flex: 1, minWidth: 0, overflow: 'auto', padding: 20 }}>
    <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
      <div><h1 style={{ margin: 0, fontSize: '1.25em' }}>实验工作台</h1><div style={{ color: 'var(--text-muted)', fontSize: '0.82em', marginTop: 4 }}>持久化实验状态的只读快照</div></div>
      <button onClick={onOpenExperimentSettings}>实验配置</button>
    </header>
    {!runId && <EmptyState title="请先创建一个研究任务。" />}
    {runId && loading && !projection && <EmptyState title="正在读取实验状态…" />}
    {runId && error && <div role="alert" style={{ color: 'var(--orange)', marginBottom: 12 }}>{error}</div>}
    {runId && projection?.selection_status === 'no_session' && <EmptyState title="实验尚未启动。请先在“研究助手”中确认实验任务。" />}
    {runId && projection?.selection_status === 'ambiguous' && <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 18 }}><b>发现多个实验 Session，请明确选择</b><select aria-label="实验 Session" value={sessionId || ''} onChange={event => chooseSession(event.target.value)} style={{ display: 'block', marginTop: 12, width: '100%' }}><option value="">请选择</option>{projection.session_candidates.map(item => <option key={item.session_id} value={item.session_id}>{item.session_id} · {item.status}</option>)}</select></section>}
    {runId && projection?.selection_status === 'selected' && projection.session && projection.summary && <>
      <SessionOverview projection={projection} />
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(190px, 1fr) minmax(230px, 1.1fr) minmax(240px, 1.2fr)', gap: 12, marginTop: 12, alignItems: 'start' }}>
        <Panel title="Idea Tree"><IdeaTree nodes={projection.idea_tree?.nodes || []} championIdeaId={projection.champion?.idea_id || null} selectedId={selection?.kind === 'idea' ? selection.value.node_id : null} onSelect={chooseIdea} /></Panel>
        <Panel title="研究动态"><ActivityFeed activity={projection.activity} truncated={projection.activity_truncated} limit={projection.activity_limit} selectedId={selection?.kind === 'activity' ? selection.value.event_id : null} onSelect={chooseActivity} /></Panel>
        <Panel title="详情面板"><DetailDrawer selection={selection} onDiscuss={onDiscuss} /><AttemptList attempts={projection.attempts} selectedId={selection?.kind === 'attempt' ? selection.value.attempt_id : null} onSelect={chooseAttempt} /><DeveloperRefs projection={projection} show={showDeveloper} onToggle={() => setShowDeveloper(value => !value)} /></Panel>
      </div>
    </>}
  </main>;
}

function SessionOverview({ projection }: { projection: ExperimentProjection }) {
  const task = projection.input_task;
  const goal = task?.user_idea || task?.request || '未能读取已确认研究目标';
  const champion = projection.champion_status === 'absent' ? '暂未产生' : projection.champion_status === 'available' ? '已登记' : projection.champion_status === 'assessment_missing' ? 'Champion 已登记，但科学评价详情缺失' : 'Champion 已登记，但科学评价详情无效';
  return <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, background: 'var(--bg-panel)' }}>
    <div style={{ fontWeight: 600 }}>{goal}</div>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', marginTop: 10, fontSize: '0.8em', color: 'var(--text-muted)' }}>
      <span>Session：{projection.session?.status}</span><span>环境：{projection.session?.environment_status}</span><span>Baseline：{projection.session?.baseline_status}</span><span>Idea：{projection.summary?.idea_count}</span><span>Champion：{champion}</span>
      {task?.baseline && <span>Baseline：{task.baseline}</span>}{task?.dataset && <span>Dataset：{task.dataset}</span>}
      {Object.entries(projection.summary?.attempt_by_status || {}).map(([status, count]) => <span key={status}>{label(status, ATTEMPT_STATUS_LABEL)}：{count}</span>)}
    </div>
    {projection.session?.readiness_blockers.length ? <div style={{ marginTop: 8, color: 'var(--orange)', fontSize: '0.8em' }}>阻塞项：{projection.session.readiness_blockers.join('；')}</div> : null}
  </section>;
}

function AttemptList({ attempts, selectedId, onSelect }: { attempts: ExperimentAttempt[]; selectedId: string | null; onSelect: (item: ExperimentAttempt) => void }) {
  if (!attempts.length) return null;
  return <div style={{ borderTop: '1px solid var(--border)', marginTop: 14, paddingTop: 10 }}><b style={{ fontSize: '0.85em' }}>关联实验</b>{attempts.map(item => <button key={item.attempt_id} onClick={() => onSelect(item)} style={{ display: 'block', width: '100%', marginTop: 6, textAlign: 'left', padding: 6, background: selectedId === item.attempt_id ? 'var(--bg)' : 'transparent', border: `1px solid ${selectedId === item.attempt_id ? 'var(--blue)' : 'var(--border)'}`, borderRadius: 5, color: 'var(--text)', cursor: 'pointer' }}>{item.attempt_id} · {label(item.runtime_status, ATTEMPT_STATUS_LABEL)}</button>)}</div>;
}

function DeveloperRefs({ projection, show, onToggle }: { projection: ExperimentProjection; show: boolean; onToggle: () => void }) {
  return <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 8 }}><button onClick={onToggle} style={{ background: 'transparent', border: 0, color: 'var(--text-dim)', padding: 0 }}>{show ? '▼' : '▶'} 开发者详情</button>{show && projection.developer_refs && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: '0.72em', color: 'var(--text-dim)' }}>{JSON.stringify(projection.developer_refs, null, 2)}</pre>}</div>;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) { return <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, minHeight: 260 }}><h2 style={{ margin: '0 0 10px', fontSize: '0.95em' }}>{title}</h2>{children}</section>; }
function EmptyState({ title }: { title: string }) { return <section style={{ minHeight: 280, display: 'grid', placeItems: 'center', textAlign: 'center', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-muted)', background: 'var(--bg-panel)', padding: 24 }}><div><div style={{ fontSize: '2em', marginBottom: 12 }}>🔬</div><div>{title}</div></div></section>; }

export { IDEA_STATUS_LABEL };
