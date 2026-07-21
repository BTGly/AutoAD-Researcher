import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { ActivityFeed } from './ActivityFeed';
import { DetailDrawer, type ExperimentDetailSelection } from './DetailDrawer';
import { IdeaTree } from './IdeaTree';
import { AppButton } from './ui/AppButton';
import { EmptyState } from './ui/EmptyState';
import { StatusBadge } from './ui/StatusBadge';
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

  return <main className="observatory" aria-busy={loading}>
    <header className="observatory-header">
      <div className="observatory-heading"><h1>实验工作台</h1><p>持久化实验状态的只读观测与受限审批</p></div>
      <div className="observatory-header-actions">
        {loading && <span className="observatory-sync-state" role="status" aria-live="polite"><RefreshCw className="observatory-sync-icon" size={14} aria-hidden="true" />同步中</span>}
        <AppButton onClick={() => void loadProjection(runId, sessionId)} disabled={!runId || loading} aria-label="刷新"> <RefreshCw size={15} aria-hidden="true" />刷新</AppButton>
      </div>
    </header>
    {!runId && <EmptyState title="请先创建一个研究任务。" />}
    {runId && loading && !projection && <EmptyState title="正在读取实验状态…" detail="当前没有可继续显示的快照。" />}
    {runId && error && <div role="alert" style={{ color: 'var(--orange)', marginBottom: 12 }}>{error}</div>}
    {runId && projection?.selection_status === 'no_session' && <EmptyState title="实验尚未启动。" detail="请先在研究助手中确认实验任务。" />}
    {runId && projection?.selection_status === 'ambiguous' && <section className="observatory-session-picker surface" aria-label="Session 选择">
      <div><h2>选择实验 Session</h2><p>发现多个实验 Session，请明确选择。选择后才会读取对应观测快照。</p></div>
      <select aria-label="实验 Session" value={sessionId || ''} onChange={event => chooseSession(event.target.value)}><option value="">请选择</option>{projection.session_candidates.map(item => <option key={item.session_id} value={item.session_id}>{item.session_id} · {sessionStatusLabel(item.status)}</option>)}</select>
    </section>}
    {runId && projection?.selection_status === 'selected' && projection.session && projection.summary && <>
      <SessionOverview projection={projection} />
      <ExperimentActions runId={runId} projection={projection} onChanged={() => void loadProjection(runId, sessionId)} />
      <div className="observatory-layout">
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
  return <section className="experiment-actions surface" aria-label="实验确认动作">
    <div className="experiment-actions-header"><div><h2>需要确认的实验动作</h2><p>动作由服务端投影决定，浏览器不会接受任意命令。</p></div><StatusBadge tone={confirmations.length || promotions.length ? 'warning' : 'success'}>{confirmations.length || promotions.length ? '待确认' : '已同步'}</StatusBadge></div>
    {error && <div role="alert" style={{ color: 'var(--orange)', marginTop: 8 }}>{error}</div>}
    {confirmations.map(confirmation => <div className="experiment-action-item" key={confirmation.candidate_attempt_id}><div className="experiment-action-copy">候选 {confirmation.candidate_attempt_id} 已记录 B_dev 比较结果。提交阈值后，服务端会重新验证是否可进行 B_test。</div><div className="experiment-action-form"><label>噪声阈值 <input aria-label={`噪声阈值 ${confirmation.candidate_attempt_id}`} value={noise} onChange={event => setNoise(event.target.value)} inputMode="decimal" /></label><AppButton variant="primary" disabled={busy !== null || !Number.isFinite(Number(noise)) || Number(noise) < 0} aria-busy={busy === `confirm:${confirmation.candidate_attempt_id}`} onClick={async () => { setBusy(`confirm:${confirmation.candidate_attempt_id}`); setError(null); try { await confirmCandidate(runId, projection.session!.session_id, confirmation.candidate_attempt_id, Number(noise)); onChanged(); } catch (reason) { reportError(reason); } finally { setBusy(null); } }}>确认 B_test 评估</AppButton></div></div>)}
    {promotions.map(promotable => <div className="experiment-action-item" key={promotable.candidate_id}><div className="experiment-action-copy">候选 {promotable.candidate_id} 已具备服务端投影的推广事实。推广会合并到 run-owned 主 checkout，并记录 Champion journal。</div><div className="experiment-action-form"><label>批准人 <input aria-label={`批准人 ${promotable.candidate_id}`} value={approvedBy} onChange={event => setApprovedBy(event.target.value)} /></label><AppButton variant="primary" disabled={busy !== null || !approvedBy.trim()} aria-busy={busy === `promote:${promotable.candidate_id}`} onClick={async () => { setBusy(`promote:${promotable.candidate_id}`); setError(null); try { await promoteCandidate(runId, promotable.candidate_id, approvedBy.trim()); onChanged(); } catch (reason) { reportError(reason); } finally { setBusy(null); } }}>批准并推广 Champion</AppButton></div></div>)}
    {!confirmations.length && !promotions.length && <div className="experiment-actions-empty">当前没有需要人工确认的 B_test 或 Champion 推广动作。</div>}
  </section>;
}

function SessionOverview({ projection }: { projection: ExperimentProjection }) {
  const task = projection.input_task;
  const goal = task?.user_idea || task?.request || '未能读取已确认研究目标';
  const champion = projection.champion_status === 'absent' ? '暂未产生' : projection.champion_status === 'available' ? '已登记' : projection.champion_status === 'assessment_missing' ? 'Champion 已登记，但科学评价详情缺失' : projection.champion_status === 'assessment_invalid' ? 'Champion 已登记，但科学评价详情无效' : 'Champion 控制面记录无效，不能据此判断不存在 Champion';
  return <section className="session-overview surface">
    <div className="session-overview-heading"><div className="session-goal">{goal}</div><StatusBadge tone={championTone(projection.champion_status)}>Champion：{champion}</StatusBadge></div>
    <div className="observatory-facts">
      <Fact label="Session" value={<StatusBadge tone={statusTone(projection.session?.status || '')}>{sessionStatusLabel(projection.session?.status || '')}</StatusBadge>} />
      <Fact label="环境" value={<StatusBadge tone={statusTone(projection.session?.environment_status || '')}>{environmentStatusLabel(projection.session?.environment_status || '')}</StatusBadge>} />
      <Fact label="基线状态" value={<StatusBadge tone={statusTone(projection.session?.baseline_status || '')}>{baselineStatusLabel(projection.session?.baseline_status || '')}</StatusBadge>} />
      <Fact label="Idea" value={String(projection.summary?.idea_count ?? 0)} />
      {task?.baseline && <Fact label="基线" value={task.baseline} />}{task?.dataset && <Fact label="Dataset" value={task.dataset} />}{task?.primary_metrics.length ? <Fact label="主指标" value={task.primary_metrics.join('；')} /> : null}{task?.constraints.length ? <Fact label="约束" value={task.constraints.join('；')} /> : null}
      <Fact label="预算" value={recordDetail(projection.summary?.budget)} /><Fact label="已消耗" value={recordDetail(projection.summary?.budget_consumed)} />
      {Object.entries(projection.summary?.attempt_by_status || {}).map(([status, count]) => <Fact key={status} label={attemptStatusLabel(status)} value={String(count)} />)}
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

function Fact({ label, value }: { label: string; value: React.ReactNode }) { return <div className="observatory-fact"><span>{label}：</span><strong>{value}</strong></div>; }
function statusTone(value: string): 'neutral' | 'success' | 'warning' | 'danger' {
  if (['FAILED', 'failed', 'LOST', 'invalid', 'CANCELLED', 'control_plane_invalid'].includes(value)) return 'danger';
  if (['RUNNING', 'running', 'pending', 'queued', 'ENVIRONMENT_PENDING', 'ENVIRONMENT_RUNNING', 'BASELINE_RUNNING'].includes(value)) return 'warning';
  if (['READY', 'ready', 'completed', 'COMPLETED', 'available', 'SUPPORTED'].includes(value)) return 'success';
  return 'neutral';
}
function championTone(value: ExperimentProjection['champion_status']): 'neutral' | 'success' | 'warning' | 'danger' {
  if (value === 'available') return 'success';
  if (value === 'assessment_missing') return 'warning';
  if (value === 'assessment_invalid' || value === 'control_plane_invalid') return 'danger';
  return 'neutral';
}

function DeveloperRefs({ projection, show, onToggle }: { projection: ExperimentProjection; show: boolean; onToggle: () => void }) {
  return <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 8 }}><button onClick={onToggle} style={{ background: 'transparent', border: 0, color: 'var(--text-dim)', padding: 0 }}>{show ? '▼' : '▶'} 开发者详情</button>{show && projection.developer_refs && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: '0.72em', color: 'var(--text-dim)' }}>{JSON.stringify(projection.developer_refs, null, 2)}</pre>}</div>;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) { return <section className="observatory-panel surface"><h2>{title}</h2>{children}</section>; }
