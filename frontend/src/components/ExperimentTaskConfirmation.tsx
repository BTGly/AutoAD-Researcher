import { useRef, useState } from 'react';
import type { ExperimentTaskDraft, SourceItem } from '../lib/types';
import { useDialogFocus } from '../hooks/useDialogFocus';

interface Props {
  task: ExperimentTaskDraft;
  sources: SourceItem[];
  onConfirm: (
    executionMode: ExperimentTaskDraft['execution_mode'],
    executionRepositorySourceId?: string,
  ) => Promise<void>;
  onConfirmPrimaryMetrics: (primaryMetrics: string[]) => Promise<void>;
  onClose: () => void;
}

const MODE_OPTIONS: Array<{ value: ExperimentTaskDraft['execution_mode']; label: string; detail: string }> = [
  { value: 'plan_only', label: '只生成方案', detail: '不准备环境，不运行实验' },
  { value: 'approve_each_step', label: '逐步确认', detail: '每一步开始前确认' },
  { value: 'agent_assisted_after_approval', label: '确认后协助', detail: '确认后由 Agent 准备环境' },
];

export function ExperimentTaskConfirmation({ task, sources, onConfirm, onConfirmPrimaryMetrics, onClose }: Props) {
  const [executionMode, setExecutionMode] = useState<ExperimentTaskDraft['execution_mode']>('plan_only');
  const [executionRepositorySourceId, setExecutionRepositorySourceId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [selectedPrimaryMetrics, setSelectedPrimaryMetrics] = useState<string[]>(task.input_task.primary_metrics);
  const executionModeRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(executionModeRef);
  const repositories = sources.filter(source => source.kind === 'github_repo' || source.kind === 'local_repo');
  const availableRepositories = repositories.filter(source => source.intakeStatus === 'ok');
  const requiresRepository = executionMode !== 'plan_only';
  const selectedRepository = availableRepositories.find(source => source.sourceId === executionRepositorySourceId);
  const metricCandidates = task.primary_metric_candidates ?? [];
  const goal = task.input_task.user_idea || task.input_task.request;

  const submit = async () => {
    if (requiresRepository && !selectedRepository) return;
    setSubmitting(true);
    setError('');
    try {
      await onConfirm(executionMode, selectedRepository?.sourceId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '实验任务确认失败');
      setSubmitting(false);
    }
  };

  const savePrimaryMetrics = async () => {
    if (!selectedPrimaryMetrics.length) return;
    setSubmitting(true);
    setError('');
    try {
      await onConfirmPrimaryMetrics(selectedPrimaryMetrics);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '主指标确认失败');
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="确认实验任务">
      <div className="modal" style={{ maxWidth: 620 }}>
        <h2 style={{ fontSize: '1.2em', marginBottom: 12, color: 'var(--blue)' }}>确认实验任务</h2>
        <div style={{ fontSize: '0.86em', color: 'var(--text-muted)', marginBottom: 16 }}>
          目标：{goal}
        </div>

        <fieldset style={{ border: 0, padding: 0, margin: '0 0 16px' }}>
          <legend style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 6 }}>执行模式</legend>
          <div role="radiogroup" aria-label="执行模式" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
            {MODE_OPTIONS.map(({ value, label, detail }, index) => (
              <button
                key={value}
                ref={index === 0 ? executionModeRef : undefined}
                type="button"
                aria-pressed={executionMode === value}
                onClick={() => {
                  setExecutionMode(value);
                  setExecutionRepositorySourceId('');
                }}
                style={{ textAlign: 'left', minHeight: 58, border: executionMode === value ? '1px solid var(--blue)' : '1px solid var(--border)', background: executionMode === value ? 'var(--bg-hover)' : 'transparent', color: 'var(--text)', borderRadius: 4, padding: '8px 9px' }}
              >
                <strong style={{ display: 'block', fontSize: '0.82em' }}>{label}</strong>
                <span style={{ display: 'block', marginTop: 3, color: 'var(--text-muted)', fontSize: '0.72em' }}>{detail}</span>
              </button>
            ))}
          </div>
        </fieldset>

        {task.input_task.primary_metrics.length === 0 ? (
          <div style={{ marginBottom: 16, padding: 10, border: '1px solid var(--orange)', borderRadius: 4 }}>
            <div style={{ color: 'var(--orange)', fontSize: '0.84em', marginBottom: 8 }}>主指标需要在讨论中确认。当前只能保留方案，不能确认执行。</div>
            {metricCandidates.length > 0 ? (
              <>
                <div role="group" aria-label="主指标候选" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                  {metricCandidates.map(metric => {
                    const selected = selectedPrimaryMetrics.includes(metric);
                    return <button key={metric} type="button" aria-pressed={selected} onClick={() => setSelectedPrimaryMetrics(current => selected ? current.filter(item => item !== metric) : [...current, metric])} style={{ border: selected ? '1px solid var(--blue)' : '1px solid var(--border)', background: selected ? 'var(--bg-hover)' : 'transparent', color: 'var(--text)', borderRadius: 4, padding: '6px 9px' }}>{metric}</button>;
                  })}
                </div>
                <button onClick={savePrimaryMetrics} disabled={submitting || !selectedPrimaryMetrics.length}>确认所选主指标并刷新草案</button>
              </>
            ) : (
              <button type="button" onClick={onClose}>回到讨论确认主指标</button>
            )}
          </div>
        ) : (
          <div style={{ marginBottom: 16, fontSize: '0.82em', color: 'var(--green)' }}>
            主指标已确认：{task.input_task.primary_metrics.join('、')}
          </div>
        )}

        {requiresRepository && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 6 }}>实验代码仓库</div>
            {availableRepositories.length > 0 ? (
              <>
                <select
                  value={executionRepositorySourceId}
                  onChange={event => setExecutionRepositorySourceId(event.target.value)}
                  aria-label="执行仓库"
                >
                  <option value="">请选择本次实验使用的代码仓库</option>
                  {availableRepositories.map(source => (
                    <option key={source.sourceId} value={source.sourceId}>
                      {source.label}
                    </option>
                  ))}
                </select>
                {selectedRepository && (
                  <div style={{ marginTop: 8, padding: 8, border: '1px solid var(--blue)', borderRadius: 4, fontSize: '0.82em' }}>
                    本次实验使用：{selectedRepository.label}<br />
                    一个任务只绑定一个代码仓库；其他已登记来源仍可作为参考材料。
                  </div>
                )}
              </>
            ) : (
              <div style={{ padding: 8, border: '1px solid var(--orange)', borderRadius: 4, color: 'var(--orange)', fontSize: '0.82em' }}>
                没有已完成采集的本地或 GitHub 仓库，暂不能确认执行。
              </div>
            )}
            {repositories.filter(source => source.intakeStatus !== 'ok').map(source => (
              <div key={source.sourceId} style={{ marginTop: 6, color: 'var(--text-dim)', fontSize: '0.78em' }}>
                不可选：{source.label} / {source.sourceId}（采集状态：{source.intakeStatus || '未知'}）
              </div>
            ))}
          </div>
        )}

        {error && <div style={{ color: 'var(--red)', fontSize: '0.82em', marginBottom: 12 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="primary"
            onClick={submit}
            disabled={submitting || (requiresRepository && !selectedRepository)}
            style={{ flex: 1 }}
          >
            {submitting ? '确认中…' : '确认任务'}
          </button>
          <button onClick={onClose} disabled={submitting} style={{ flex: 1 }}>取消</button>
        </div>
      </div>
    </div>
  );
}
