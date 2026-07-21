import { useEffect, useState } from 'react';
import type { ExperimentTaskDraft, SourceItem } from '../lib/types';
import { AppButton } from './ui/AppButton';

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

const MODE_LABELS: Record<ExperimentTaskDraft['execution_mode'], string> = {
  plan_only: '仅生成实验输入，不创建执行环境',
  approve_each_step: '环境准备和后续每一步都需要确认',
  agent_assisted_after_approval: '确认后允许 Agent 协助准备环境',
};

export function ExperimentTaskConfirmation({ task, sources, onConfirm, onConfirmPrimaryMetrics, onClose }: Props) {
  const [executionMode, setExecutionMode] = useState<ExperimentTaskDraft['execution_mode']>('plan_only');
  const [executionRepositorySourceId, setExecutionRepositorySourceId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [primaryMetricsText, setPrimaryMetricsText] = useState(task.input_task.primary_metrics.join('\n'));
  const repositories = sources.filter(source => source.kind === 'github_repo' || source.kind === 'local_repo');
  const availableRepositories = repositories.filter(source => source.intakeStatus === 'ok');
  const requiresRepository = executionMode !== 'plan_only';
  const selectedRepository = availableRepositories.find(source => source.sourceId === executionRepositorySourceId);
  const goal = task.input_task.user_idea || task.input_task.request;

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, submitting]);

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
    const primaryMetrics = primaryMetricsText.split('\n').map(value => value.trim()).filter(Boolean);
    if (!primaryMetrics.length) return;
    setSubmitting(true);
    setError('');
    try {
      await onConfirmPrimaryMetrics(primaryMetrics);
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

        <label style={{ display: 'block', marginBottom: 16 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>执行模式</div>
                  <select autoFocus value={executionMode} onChange={event => {
            setExecutionMode(event.target.value as ExperimentTaskDraft['execution_mode']);
            setExecutionRepositorySourceId('');
          }}>
            {Object.entries(MODE_LABELS).map(([mode, label]) => (
              <option key={mode} value={mode}>{mode} — {label}</option>
            ))}
          </select>
        </label>

        {task.input_task.primary_metrics.length === 0 ? (
          <div style={{ marginBottom: 16, padding: 10, border: '1px solid var(--orange)', borderRadius: 4 }}>
            <div style={{ color: 'var(--orange)', fontSize: '0.84em', marginBottom: 6 }}>主指标未确认；可以保留 plan_only 草案，但不能确认执行。</div>
            <textarea
              value={primaryMetricsText}
              onChange={event => setPrimaryMetricsText(event.target.value)}
              placeholder="每行一个主指标，例如 image_auroc"
              aria-label="主指标"
              rows={3}
              style={{ width: '100%', marginBottom: 8 }}
            />
            <AppButton onClick={savePrimaryMetrics} disabled={submitting || !primaryMetricsText.trim()}>
              确认主指标并刷新草案
            </AppButton>
          </div>
        ) : (
          <div style={{ marginBottom: 16, fontSize: '0.82em', color: 'var(--green)' }}>
            主指标已确认：{task.input_task.primary_metrics.join('、')}
          </div>
        )}

        {requiresRepository && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 6 }}>执行仓库</div>
            {availableRepositories.length > 0 ? (
              <>
                <select
                  value={executionRepositorySourceId}
                  onChange={event => setExecutionRepositorySourceId(event.target.value)}
                  aria-label="执行仓库"
                >
                  <option value="">请选择明确授权的执行仓库</option>
                  {availableRepositories.map(source => (
                    <option key={source.sourceId} value={source.sourceId}>
                      {source.label} / {source.kind} / {source.sourceId}
                    </option>
                  ))}
                </select>
                {selectedRepository && (
                  <div style={{ marginTop: 8, padding: 8, border: '1px solid var(--blue)', borderRadius: 4, fontSize: '0.82em' }}>
                    将执行：{selectedRepository.label}<br />
                    source_id：{selectedRepository.sourceId}<br />
                    类型：{selectedRepository.kind}；采集状态：{selectedRepository.intakeStatus}
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
          <AppButton
            variant="primary"
            onClick={submit}
            disabled={submitting || (requiresRepository && !selectedRepository)}
            style={{ flex: 1 }}
          >
            {submitting ? '确认中…' : '确认任务'}
          </AppButton>
          <AppButton onClick={onClose} disabled={submitting} style={{ flex: 1 }}>取消</AppButton>
        </div>
      </div>
    </div>
  );
}
