import { useState } from 'react';
import type { ExperimentTaskDraft, SourceItem } from '../lib/types';

interface Props {
  task: ExperimentTaskDraft;
  sources: SourceItem[];
  onConfirm: (
    executionMode: ExperimentTaskDraft['execution_mode'],
    executionRepositorySourceId?: string,
  ) => Promise<void>;
  onClose: () => void;
}

const MODE_LABELS: Record<ExperimentTaskDraft['execution_mode'], string> = {
  plan_only: '仅生成实验输入，不创建执行环境',
  approve_each_step: '环境准备和后续每一步都需要确认',
  agent_assisted_after_approval: '确认后允许 Agent 协助准备环境',
};

export function ExperimentTaskConfirmation({ task, sources, onConfirm, onClose }: Props) {
  const [executionMode, setExecutionMode] = useState<ExperimentTaskDraft['execution_mode']>('plan_only');
  const [executionRepositorySourceId, setExecutionRepositorySourceId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const repositories = sources.filter(source => source.kind === 'github_repo' || source.kind === 'local_repo');
  const availableRepositories = repositories.filter(source => source.intakeStatus === 'ok');
  const requiresRepository = executionMode !== 'plan_only';
  const selectedRepository = availableRepositories.find(source => source.sourceId === executionRepositorySourceId);
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

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="确认实验任务">
      <div className="modal" style={{ maxWidth: 620 }}>
        <h2 style={{ fontSize: '1.2em', marginBottom: 12, color: 'var(--blue)' }}>确认实验任务</h2>
        <div style={{ fontSize: '0.86em', color: 'var(--text-muted)', marginBottom: 16 }}>
          目标：{goal}
        </div>

        <label style={{ display: 'block', marginBottom: 16 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>执行模式</div>
          <select value={executionMode} onChange={event => {
            setExecutionMode(event.target.value as ExperimentTaskDraft['execution_mode']);
            setExecutionRepositorySourceId('');
          }}>
            {Object.entries(MODE_LABELS).map(([mode, label]) => (
              <option key={mode} value={mode}>{mode} — {label}</option>
            ))}
          </select>
        </label>

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
