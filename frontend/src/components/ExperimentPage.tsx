import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityFeed } from './ActivityFeed';
import { DetailDrawer, type ExperimentDetailSelection } from './DetailDrawer';
import { IdeaTree } from './IdeaTree';
import { ApiError, confirmCandidate, getExperimentProjection, promoteCandidate } from '../lib/api';
import { attemptStatusLabel, baselineStatusLabel, environmentStatusLabel, sessionStatusLabel } from '../lib/experimentLabels';
import type { ExperimentActivity, ExperimentAttempt, ExperimentIdeaNode, ExperimentProjection } from '../lib/types';

interface Props {
  runId: string;
  experimentRefreshTick: number;
  onDiscuss: (text: string) => void;
}


type ExperimentDetailSelectionKey =
  | { kind: 'idea'; id: string }
  | { kind: 'attempt'; id: string }
  | { kind: 'activity'; id: number }
  | null;

export function ExperimentPage({ runId, experimentRefreshTick, onDiscuss }: Props) {
  const [projection, setProjection] = useState<ExperimentProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [selection, setSelection] = useState<ExperimentDetailSelectionKey>(null);
  const [showDeveloper, setShowDeveloper] = useState(false);
  const requestId = useRef(0);
  const currentRequest = useRef<AbortController | null>(null);
  const refreshScope = useRef({ runId, sessionId });
  const refreshScopeVersion = useRef(0);
  const lastHandledRefreshTick = useRef(experimentRefreshTick);
  refreshScope.current = { runId, sessionId };

  const loadProjection = useCallback(async (targetRunId: string, targetSessionId: string | undefined) => {
    currentRequest.current?.abort();
    const controller = new AbortController();
    currentRequest.current = controller;
    const id = ++requestId.current;
    setLoading(true);
    try {
      const value = await getExperimentProjection(targetRunId, targetSessionId, controller.signal);
      if (id === requestId.current) {
        setProjection(value);
        setError(null);
      }
    } catch (reason) {
      if (id === requestId.current && !(reason instanceof DOMException && reason.name === 'AbortError')) {
        setError('工作台刷新失败，仍保留上一份有效快照。');
      }
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshScopeVersion.current += 1;
    setSessionId(undefined);
    setSelection(null);
    setProjection(null);
    setError(null);
  }, [runId]);

  useEffect(() => {
    refreshScopeVersion.current += 1;
    if (!runId) {
      currentRequest.current?.abort();
      setProjection(null);
      setError(null);
      return;
    }
    void loadProjection(runId, sessionId);
    return () => currentRequest.current?.abort();
  }, [loadProjection, runId, sessionId]);

  useEffect(() => {
    if (experimentRefreshTick === lastHandledRefreshTick.current) return;
    lastHandledRefreshTick.current = experimentRefreshTick;
    const scheduledScopeVersion = refreshScopeVersion.current;
    const timer = window.setTimeout(() => {
      if (refreshScopeVersion.current !== scheduledScopeVersion) return;
      const scope = refreshScope.current;
      if (scope.runId) void loadProjection(scope.runId, scope.sessionId);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [experimentRefreshTick, loadProjection]);

  const chooseSession = (next: string) => {
    setSelection(null);
    setProjection(null);
    setSessionId(next || undefined);
  };
  const chooseIdea = (value: ExperimentIdeaNode) => setSelection({ kind: 'idea', id: value.node_id });
  const chooseAttempt = (value: ExperimentAttempt) => setSelection({ kind: 'attempt', id: value.attempt_id });
  const chooseActivity = (value: ExperimentActivity) => setSelection({ kind: 'activity', id: value.event_id });
  const detailSelection = selectDetail(projection, selection);

  return <main style={{ flex: 1, minWidth: 0, overflow: 'auto', padding: 20 }}>
    <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
      <div><h1 style={{ margin: 0, fontSize: '1.25em' }}>实验工作台</h1><div style={{ color: 'var(--text-muted)', fontSize: '0.82em', marginTop: 4 }}>持久化实验状态的只读观测 + 受限显式审批动作</div></div>
      <div style={{ display: 'flex', gap: 8 }}><button onClick={() => void loadProjection(runId, sessionId)} disabled={!runId || loading}>刷新</button></div>
    </header>
    {!runId && <EmptyState title="请先创建一个研究任务。" />}
    {runId && loading && !projection && <EmptyState title="正在读取实验状态…" />}
    {runId && error && <div role="alert" style={{ color: 'var(--orange)', marginBottom: 12 }}>{error}</div>}
    {runId && projection?.selection_status === 'no_session' && <EmptyState title="实验尚未启动。请先在“研究助手”中确认实验任务。" />}
    {runId && projection?.selection_status === 'ambiguous' && <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 18 }}><b>发现多个实验 Session，请明确选择</b><select aria-label="实验 Session" value={sessionId || ''} onChange={event => chooseSession(event.target.value)} style={{ display: 'block', marginTop: 12, width: '100%' }}><option value="">请选择</option>{projection.session_candidates.map(item => <option key={item.session_id} value={item.session_id}>{item.session_id} · {sessionStatusLabel(item.status)}</option>)}</select></section>}
    {runId && projection?.selection_status === 'selected' && projection.session && projection.summary && <>
      <SessionOverview projection={projection} />
      <ExperimentActions runId={runId} projection={projection} onChanged={() => void loadProjection(runId, sessionId)} />
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(190px, 1fr) minmax(230px, 1.1fr) minmax(240px, 1.2fr)', gap: 12, marginTop: 12, alignItems: 'start' }}>
        <Panel title="Idea Tree"><IdeaTree nodes={projection.idea_tree?.nodes || []} championIdeaId={projection.champion?.idea_id || null} selectedId={selection?.kind === 'idea' ? selection.id : null} onSelect={chooseIdea} /></Panel>
        <Panel title="研究动态"><ActivityFeed activity={projection.activity} truncated={projection.activity_truncated} scanTruncated={projection.activity_scan_truncated} limit={projection.activity_limit} selectedId={selection?.kind === 'activity' ? selection.id : null} onSelect={chooseActivity} /></Panel>
        <Panel title="详情面板"><DetailDrawer selection={detailSelection} onDiscuss={onDiscuss} /><AttemptList attempts={projection.attempts} selectedIdeaId={selection?.kind === 'idea' ? selection.id : null} selectedId={selection?.kind === 'attempt' ? selection.id : null} onSelect={chooseAttempt} /><DeveloperRefs projection={projection} show={showDeveloper} onToggle={() => setShowDeveloper(value => !value)} /></Panel>
      </div>
    </>}
  </main>;
}

function selectDetail(
  projection: ExperimentProjection | null,
  selection: ExperimentDetailSelectionKey,
): ExperimentDetailSelection {
  if (!projection || !selection) return null;
  if (selection.kind === 'idea') {
    const value = projection.idea_tree?.nodes.find(item => item.node_id === selection.id);
    return value ? { kind: 'idea', value } : null;
  }
  if (selection.kind === 'attempt') {
    const value = projection.attempts.find(item => item.attempt_id === selection.id);
    return value ? { kind: 'attempt', value } : null;
  }
  const value = projection.activity.find(item => item.event_id === selection.id);
  return value ? { kind: 'activity', value } : null;
}

function ExperimentActions({ runId, projection, onChanged }: { runId: string; projection: ExperimentProjection; onChanged: () => void }) {
  const [noise, setNoise] = useState('');
  const [approvedBy, setApprovedBy] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (projection.session?.execution_mode !== 'approve_each_step') return null;
  const confirmations = projection.actions.candidate_confirmations;
  const promotions = projection.actions.candidate_promotions;
  const reportError = (reason: unknown) => setError(reason instanceof ApiError ? reason.message : '操作未完成，请保留当前证据后重试。');
  return <section aria-label="实验确认动作" style={{ border: '1px solid var(--blue)', borderRadius: 8, padding: 14, marginTop: 12, background: 'var(--bg-panel)' }}>
    <b>需要确认的实验动作</b><div style={{ color: 'var(--text-muted)', fontSize: '0.8em', marginTop: 5 }}>命令、仓库、输入和合并目标均由已冻结工件派生；本页不会接受任意命令。</div>
    {error && <div role="alert" style={{ color: 'var(--orange)', marginTop: 8 }}>{error}</div>}
    {confirmations.map(confirmation => <div key={confirmation.candidate_attempt_id} style={{ marginTop: 12 }}><div>候选 {confirmation.candidate_attempt_id} 已记录 B_dev 比较结果。提交阈值后，服务端会重新验证是否可进行 B_test。</div><label style={{ display: 'block', marginTop: 7 }}>噪声阈值 <input aria-label={`噪声阈值 ${confirmation.candidate_attempt_id}`} value={noise} onChange={event => setNoise(event.target.value)} inputMode="decimal" /></label><button disabled={busy !== null || !Number.isFinite(Number(noise)) || Number(noise) < 0} onClick={async () => { setBusy(`confirm:${confirmation.candidate_attempt_id}`); setError(null); try { await confirmCandidate(runId, projection.session!.session_id, confirmation.candidate_attempt_id, Number(noise)); onChanged(); } catch (reason) { reportError(reason); } finally { setBusy(null); } }}>确认 B_test 评估</button></div>)}
    {promotions.map(promotable => <div key={promotable.candidate_id} style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10 }}><div>候选 {promotable.candidate_id} 已具备服务端投影的推广事实。推广会合并到 run-owned 主 checkout，并记录 Champion journal。</div><label style={{ display: 'block', marginTop: 7 }}>批准人 <input aria-label={`批准人 ${promotable.candidate_id}`} value={approvedBy} onChange={event => setApprovedBy(event.target.value)} /></label><button disabled={busy !== null || !approvedBy.trim()} onClick={async () => { setBusy(`promote:${promotable.candidate_id}`); setError(null); try { await promoteCandidate(runId, promotable.candidate_id, approvedBy.trim()); onChanged(); } catch (reason) { reportError(reason); } finally { setBusy(null); } }}>批准并推广 Champion</button></div>)}
    {!confirmations.length && !promotions.length && <div style={{ color: 'var(--text-muted)', marginTop: 10, fontSize: '0.85em' }}>当前没有需要人工确认的 B_test 或 Champion 推广动作。</div>}
  </section>;
}

function SessionOverview({ projection }: { projection: ExperimentProjection }) {
  const task = projection.input_task;
  const goal = task?.user_idea || task?.request || '未能读取已确认研究目标';
  const champion = projection.champion_status === 'absent' ? '暂未产生' : projection.champion_status === 'available' ? '已登记' : projection.champion_status === 'assessment_missing' ? 'Champion 已登记，但科学评价详情缺失' : projection.champion_status === 'assessment_invalid' ? 'Champion 已登记，但科学评价详情无效' : 'Champion 控制面记录无效，不能据此判断不存在 Champion';
  return <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, background: 'var(--bg-panel)' }}>
    <div style={{ fontWeight: 600 }}>{goal}</div>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', marginTop: 10, fontSize: '0.8em', color: 'var(--text-muted)' }}>
      <span>Session：{sessionStatusLabel(projection.session?.status || '')}</span><span>环境：{environmentStatusLabel(projection.session?.environment_status || '')}</span><span>Baseline 状态：{baselineStatusLabel(projection.session?.baseline_status || '')}</span><span>Idea：{projection.summary?.idea_count}</span><span>Champion：{champion}</span>
      {task?.baseline && <span>Baseline：{task.baseline}</span>}{task?.dataset && <span>Dataset：{task.dataset}</span>}{task?.primary_metrics.length ? <span>主指标：{task.primary_metrics.join('；')}</span> : null}{task?.constraints.length ? <span>约束：{task.constraints.join('；')}</span> : null}
      <span>预算：{recordDetail(projection.summary?.budget)}</span><span>已消耗：{recordDetail(projection.summary?.budget_consumed)}</span>
      {Object.entries(projection.summary?.attempt_by_status || {}).map(([status, count]) => <span key={status}>{attemptStatusLabel(status)}：{count}</span>)}
    </div>
    {projection.session?.readiness_blockers.length ? <div style={{ marginTop: 8, color: 'var(--orange)', fontSize: '0.8em' }}>阻塞项：{projection.session.readiness_blockers.join('；')}</div> : null}
    {lostOrFailed(projection.attempts) && <div style={{ marginTop: 8, color: 'var(--orange)', fontSize: '0.8em' }}>异常 Attempt：{lostOrFailed(projection.attempts).map(item => `${item.attempt_id}（${attemptStatusLabel(item.runtime_status)}）`).join('；')}</div>}
  </section>;
}

function AttemptList({ attempts, selectedIdeaId, selectedId, onSelect }: { attempts: ExperimentAttempt[]; selectedIdeaId: string | null; selectedId: string | null; onSelect: (item: ExperimentAttempt) => void }) {
  const relatedAttempts = selectedIdeaId ? attempts.filter(item => item.related_idea_ids.includes(selectedIdeaId)) : attempts;
  if (!relatedAttempts.length) return selectedIdeaId ? <div style={{ borderTop: '1px solid var(--border)', marginTop: 14, paddingTop: 10, color: 'var(--text-muted)', fontSize: '0.85em' }}>该 Idea 暂无关联实验。</div> : null;
  return <div style={{ borderTop: '1px solid var(--border)', marginTop: 14, paddingTop: 10 }}><b style={{ fontSize: '0.85em' }}>{selectedIdeaId ? '关联实验' : '全部实验'}</b>{relatedAttempts.map(item => <button key={item.attempt_id} onClick={() => onSelect(item)} style={{ display: 'block', width: '100%', marginTop: 6, textAlign: 'left', padding: 6, background: selectedId === item.attempt_id ? 'var(--bg)' : 'transparent', border: `1px solid ${selectedId === item.attempt_id ? 'var(--blue)' : 'var(--border)'}`, borderRadius: 5, color: 'var(--text)', cursor: 'pointer' }}>{item.attempt_id} · {attemptStatusLabel(item.runtime_status)}</button>)}</div>;
}

function recordDetail(value: Record<string, unknown> | null | undefined): string { return value && Object.keys(value).length ? Object.entries(value).map(([key, item]) => `${key}: ${String(item)}`).join('；') : '暂无'; }
function lostOrFailed(attempts: ExperimentAttempt[]): ExperimentAttempt[] { return attempts.filter(item => item.runtime_status === 'FAILED' || item.runtime_status === 'LOST'); }

function DeveloperRefs({ projection, show, onToggle }: { projection: ExperimentProjection; show: boolean; onToggle: () => void }) {
  return <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 8 }}><button onClick={onToggle} style={{ background: 'transparent', border: 0, color: 'var(--text-dim)', padding: 0 }}>{show ? '▼' : '▶'} 开发者详情</button>{show && projection.developer_refs && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: '0.72em', color: 'var(--text-dim)' }}>{JSON.stringify(projection.developer_refs, null, 2)}</pre>}</div>;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) { return <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, minHeight: 260 }}><h2 style={{ margin: '0 0 10px', fontSize: '0.95em' }}>{title}</h2>{children}</section>; }
function EmptyState({ title }: { title: string }) { return <section style={{ minHeight: 280, display: 'grid', placeItems: 'center', textAlign: 'center', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-muted)', background: 'var(--bg-panel)', padding: 24 }}><div><div style={{ fontSize: '2em', marginBottom: 12 }}>🔬</div><div>{title}</div></div></section>; }
