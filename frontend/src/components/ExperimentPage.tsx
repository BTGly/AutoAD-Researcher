import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { ActivityFeed } from './ActivityFeed';
import { DetailDrawer, type ExperimentDetailSelection } from './DetailDrawer';
import { IdeaTree } from './IdeaTree';
import { AppButton } from './ui/AppButton';
import { EmptyState } from './ui/EmptyState';
import { StatusBadge } from './ui/StatusBadge';
import { ApiError, confirmCandidate, getExperimentProjection, promoteCandidate, startBaseline } from '../lib/api';
import { attemptStatusLabel, baselineStatusLabel, environmentStatusLabel, sessionStatusLabel } from '../lib/experimentLabels';
import type { BaselineContractInput, BaselineMetricInput, ExperimentActivity, ExperimentAttempt, ExperimentIdeaNode, ExperimentProjection } from '../lib/types';

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

  const loadProjection = useCallback(async (targetRunId: string, targetSessionId: string | undefined, options: { suppressError?: boolean } = {}): Promise<boolean> => {
    currentRequest.current?.abort();
    const controller = new AbortController();
    currentRequest.current = controller;
    const id = ++requestId.current;
    setLoading(true);
    let accepted = false;
    try {
      const value = await getExperimentProjection(targetRunId, targetSessionId, controller.signal);
      if (id === requestId.current) {
        setProjection(value);
        setError(null);
        accepted = true;
      }
    } catch (reason) {
      if (id === requestId.current && !options.suppressError && !(reason instanceof DOMException && reason.name === 'AbortError')) {
        setError('工作台刷新失败，仍保留上一份有效快照。');
      }
    } finally {
      if (id === requestId.current) setLoading(false);
    }
    return accepted;
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
      <ExperimentActions runId={runId} projection={projection} onChanged={() => loadProjection(runId, sessionId, { suppressError: true })} />
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

function ExperimentActions({ runId, projection, onChanged }: { runId: string; projection: ExperimentProjection; onChanged: () => Promise<boolean> }) {
  const [noise, setNoise] = useState('');
  const [approvedBy, setApprovedBy] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const baselineAvailable = projection.actions.baseline_launch_available === true;
  if (projection.session?.execution_mode !== 'approve_each_step' && !baselineAvailable) return null;
  const confirmations = projection.actions.candidate_confirmations;
  const promotions = projection.actions.candidate_promotions;
  const reportError = (reason: unknown) => setError(reason instanceof ApiError ? reason.message : '操作未完成，请保留当前证据后重试。');
  return <section className="experiment-actions surface" aria-label="实验确认动作">
    <div className="experiment-actions-header"><div><h2>需要确认的实验动作</h2><p>动作由服务端投影决定，浏览器不会接受任意命令。</p></div><StatusBadge tone={confirmations.length || promotions.length ? 'warning' : 'success'}>{confirmations.length || promotions.length ? '待确认' : '已同步'}</StatusBadge></div>
    {error && <div role="alert" style={{ color: 'var(--orange)', marginTop: 8 }}>{error}</div>}
    {baselineAvailable && <BaselineLaunchForm runId={runId} projection={projection} onChanged={onChanged} />}
    {confirmations.map(confirmation => <div className="experiment-action-item" key={confirmation.candidate_attempt_id}><div className="experiment-action-copy">候选 {confirmation.candidate_attempt_id} 已记录 B_dev 比较结果。提交阈值后，服务端会重新验证是否可进行 B_test。</div><div className="experiment-action-form"><label>噪声阈值 <input aria-label={`噪声阈值 ${confirmation.candidate_attempt_id}`} value={noise} onChange={event => setNoise(event.target.value)} inputMode="decimal" /></label><AppButton variant="primary" disabled={busy !== null || !Number.isFinite(Number(noise)) || Number(noise) < 0} aria-busy={busy === `confirm:${confirmation.candidate_attempt_id}`} onClick={async () => { setBusy(`confirm:${confirmation.candidate_attempt_id}`); setError(null); try { await confirmCandidate(runId, projection.session!.session_id, confirmation.candidate_attempt_id, Number(noise)); if (!await onChanged()) setError('B_test 请求已提交，但工作台刷新失败。当前证据已保留，请刷新后继续。'); } catch (reason) { reportError(reason); } finally { setBusy(null); } }}>确认 B_test 评估</AppButton></div></div>)}
    {promotions.map(promotable => <div className="experiment-action-item" key={promotable.candidate_id}><div className="experiment-action-copy">候选 {promotable.candidate_id} 已具备服务端投影的推广事实。推广会合并到 run-owned 主 checkout，并记录 Champion journal。</div><div className="experiment-action-form"><label>批准人 <input aria-label={`批准人 ${promotable.candidate_id}`} value={approvedBy} onChange={event => setApprovedBy(event.target.value)} /></label><AppButton variant="primary" disabled={busy !== null || !approvedBy.trim()} aria-busy={busy === `promote:${promotable.candidate_id}`} onClick={async () => { setBusy(`promote:${promotable.candidate_id}`); setError(null); try { await promoteCandidate(runId, promotable.candidate_id, approvedBy.trim()); if (!await onChanged()) setError('推广请求已提交，但工作台刷新失败。当前证据已保留，请刷新后继续。'); } catch (reason) { reportError(reason); } finally { setBusy(null); } }}>批准并推广 Champion</AppButton></div></div>)}
    {!baselineAvailable && !confirmations.length && !promotions.length && <div className="experiment-actions-empty">当前没有需要人工确认的 B_test 或 Champion 推广动作。</div>}
  </section>;
}

type MetricDraft = {
  name: string;
  direction: BaselineMetricInput['direction'] | '';
  implementation_ref: string;
  role: 'primary' | 'guardrail' | '';
};

function BaselineLaunchForm({ runId, projection, onChanged }: { runId: string; projection: ExperimentProjection; onChanged: () => Promise<boolean> }) {
  const task = projection.input_task;
  const sessionId = projection.session?.session_id;
  const [datasetIdentity, setDatasetIdentity] = useState(task?.dataset || '');
  const [splitIdentity, setSplitIdentity] = useState('');
  const [bDevRef, setBDevRef] = useState('');
  const [bTestRef, setBTestRef] = useState('');
  const [categoryText, setCategoryText] = useState('');
  const [seedsText, setSeedsText] = useState('');
  const [checkpointSelection, setCheckpointSelection] = useState('');
  const [maxWallSeconds, setMaxWallSeconds] = useState('');
  const [maxGpuSeconds, setMaxGpuSeconds] = useState('');
  const [requiredDeviceCount, setRequiredDeviceCount] = useState('');
  const [requiredVramMb, setRequiredVramMb] = useState('');
  const [metrics, setMetrics] = useState<MetricDraft[]>(() => (task?.primary_metrics || []).map(name => ({ name, direction: '', implementation_ref: '', role: '' })));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateMetric = (index: number, patch: Partial<MetricDraft>) => {
    setMetrics(values => values.map((value, itemIndex) => itemIndex === index ? { ...value, ...patch } : value));
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!sessionId) return;
    const primary = metrics.filter(metric => metric.role === 'primary');
    const incompleteMetric = metrics.find(metric => !metric.direction || !metric.implementation_ref.trim());
    const seeds = parseIntegers(seedsText);
    const wall = parseNonNegativeInteger(maxWallSeconds);
    const gpu = parseNonNegativeInteger(maxGpuSeconds);
    const devices = parseOptionalNonNegativeInteger(requiredDeviceCount);
    const vram = parseOptionalNonNegativeInteger(requiredVramMb);
    let validationError: string | null = null;
    if (!datasetIdentity.trim() || !splitIdentity.trim() || !bDevRef.trim() || !bTestRef.trim() || !checkpointSelection.trim()) validationError = '数据集、split、checkpoint 选择和冻结文件引用均不能为空。';
    else if (primary.length !== 1) validationError = '必须明确选择一个 primary metric。';
    else if (incompleteMetric) validationError = '每个已确认指标都需要方向和实现引用；未设为 primary 或 guardrail 的指标仅记录。';
    else if (!seeds.length) validationError = '至少填写一个整数 seed。';
    else if (seeds.length !== new Set(seeds).size) validationError = 'seed 不能重复。';
    else if (wall === null || wall <= 0) validationError = '最大墙钟时间必须是正整数。';
    else if (gpu === null) validationError = '最大 GPU 时间必须是大于等于 0 的整数。';
    else if (devices === null || vram === null) validationError = 'GPU 设备数和显存需求必须是非负整数。';
    else if (devices === 0 && vram !== 0) validationError = '没有 GPU 设备请求时，显存需求必须为 0。';
    else if (gpu === 0 && devices !== 0) validationError = 'GPU 秒数为 0 时不能请求 GPU 设备。';
    else if (gpu > 0 && devices === 0) validationError = 'GPU 秒数为正时必须明确填写 GPU 设备数。';
    if (validationError) {
      setError(validationError);
      return;
    }
    const contract: BaselineContractInput = {
      primary_metric: primary[0].name,
      metrics: metrics.map(metric => ({ name: metric.name, direction: metric.direction as BaselineMetricInput['direction'], implementation_ref: metric.implementation_ref.trim() })),
      guardrails: metrics.filter(metric => metric.role === 'guardrail').map(metric => metric.name),
      dataset_identity: datasetIdentity.trim(),
      split_identity: splitIdentity.trim(),
      b_dev_ref: bDevRef.trim(),
      b_test_ref: bTestRef.trim(),
      category_set: splitLines(categoryText),
      seeds,
      checkpoint_selection: checkpointSelection.trim(),
      max_wall_seconds: wall as number,
      max_gpu_seconds: gpu as number,
      required_device_count: devices as number,
      required_vram_mb: vram as number,
    };
    setBusy(true);
    setError(null);
    try {
      await startBaseline(runId, sessionId, contract);
      const refreshed = await onChanged();
      if (!refreshed) setError('Baseline 已启动，但工作台刷新失败。当前契约已保留，请刷新后继续。');
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Baseline 启动失败，请保留当前契约后重试。');
    } finally {
      setBusy(false);
    }
  };

  return <form onSubmit={submit} style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
    <h3 style={{ margin: 0, fontSize: '0.95em' }}>启动 Baseline</h3>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 8, marginTop: 10 }}>
      <label>数据集标识<input aria-label="数据集标识" value={datasetIdentity} onChange={event => setDatasetIdentity(event.target.value)} /></label>
      <label>Split 标识<input aria-label="Split 标识" value={splitIdentity} onChange={event => setSplitIdentity(event.target.value)} /></label>
      <label>B_dev 文件引用<input aria-label="B_dev 文件引用" placeholder="run-relative path" value={bDevRef} onChange={event => setBDevRef(event.target.value)} /></label>
      <label>B_test 文件引用<input aria-label="B_test 文件引用" placeholder="run-relative path" value={bTestRef} onChange={event => setBTestRef(event.target.value)} /></label>
      <label>Checkpoint 选择<input aria-label="Checkpoint 选择" value={checkpointSelection} onChange={event => setCheckpointSelection(event.target.value)} /></label>
      <label>Seeds<input aria-label="Seeds" placeholder="例如 1, 2" value={seedsText} onChange={event => setSeedsText(event.target.value)} /></label>
      <label>最大墙钟秒数<input aria-label="最大墙钟秒数" inputMode="numeric" value={maxWallSeconds} onChange={event => setMaxWallSeconds(event.target.value)} /></label>
      <label>最大 GPU 秒数<input aria-label="最大 GPU 秒数" inputMode="numeric" value={maxGpuSeconds} onChange={event => setMaxGpuSeconds(event.target.value)} /></label>
      <label>所需 GPU 数量<input aria-label="所需 GPU 数量" inputMode="numeric" placeholder="CPU 填 0 或留空" value={requiredDeviceCount} onChange={event => setRequiredDeviceCount(event.target.value)} /></label>
      <label>每个 GPU 所需显存 MB<input aria-label="每个 GPU 所需显存 MB" inputMode="numeric" placeholder="CPU 填 0 或留空" value={requiredVramMb} onChange={event => setRequiredVramMb(event.target.value)} /></label>
    </div>
    <label style={{ display: 'block', marginTop: 8 }}>类别集合（可为空）<textarea aria-label="类别集合" rows={2} placeholder="每行一个类别" value={categoryText} onChange={event => setCategoryText(event.target.value)} /></label>
    <div style={{ marginTop: 10 }}><b style={{ fontSize: '0.85em' }}>已确认指标</b>{metrics.map((metric, index) => <div key={metric.name} style={{ display: 'grid', gridTemplateColumns: 'minmax(110px, .8fr) minmax(120px, 1fr) minmax(120px, 1fr) minmax(150px, 1.4fr)', gap: 6, marginTop: 6, alignItems: 'center' }}><input aria-label={`指标名称 ${metric.name}`} value={metric.name} readOnly /><select aria-label={`指标方向 ${metric.name}`} value={metric.direction} onChange={event => updateMetric(index, { direction: event.target.value as MetricDraft['direction'] })}><option value="">方向</option><option value="maximize">越大越好（maximize）</option><option value="minimize">越小越好（minimize）</option></select><select aria-label={`指标角色 ${metric.name}`} value={metric.role} onChange={event => updateMetric(index, { role: event.target.value as MetricDraft['role'] })}><option value="">仅记录</option><option value="primary">主指标（primary）</option><option value="guardrail">护栏指标（guardrail）</option></select><input aria-label={`指标实现引用 ${metric.name}`} placeholder="worktree-relative implementation path" value={metric.implementation_ref} onChange={event => updateMetric(index, { implementation_ref: event.target.value })} /></div>)}</div>
    {error && <div role="alert" style={{ color: 'var(--orange)', marginTop: 8 }}>{error}</div>}
    <button type="submit" disabled={busy} style={{ marginTop: 10 }}>{busy ? 'Baseline 排队中…' : '冻结契约并启动 Baseline'}</button>
  </form>;
}

function splitLines(value: string): string[] { return value.split(/\r?\n/).map(item => item.trim()).filter(Boolean); }
function parseIntegers(value: string): number[] { const items = value.split(/[\s,，]+/).map(item => item.trim()).filter(Boolean); return items.every(item => /^-?\d+$/.test(item)) ? items.map(Number) : []; }
function parseNonNegativeInteger(value: string): number | null { return /^\d+$/.test(value.trim()) ? Number(value.trim()) : null; }
function parseOptionalNonNegativeInteger(value: string): number | null { return value.trim() === '' ? 0 : parseNonNegativeInteger(value); }

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
