import { useRef, useState } from 'react';
import type { ExperimentTaskDraft, ExperimentTaskReadiness, SourceItem } from '../lib/types';
import { useDialogFocus } from '../hooks/useDialogFocus';

interface Props {
  task: ExperimentTaskDraft;
  readiness: ExperimentTaskReadiness;
  sources: SourceItem[];
  onConfirm: (executionRepositorySourceId?: string) => Promise<void>;
  onClose: () => void;
}

export function ExperimentTaskConfirmation({ task, readiness, sources, onConfirm, onClose }: Props) {
  const [executionRepositorySourceId, setExecutionRepositorySourceId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(confirmButtonRef);
  const repositories = sources.filter(source => source.kind === 'github_repo' || source.kind === 'local_repo');
  const availableRepositories = repositories.filter(source =>
    readiness.admitted_execution_repository_source_ids.includes(source.sourceId),
  );
  const executionContractReady = task.input_task.primary_metrics.length > 0;
  const selectedRepository = availableRepositories.find(source => source.sourceId === executionRepositorySourceId);
  const goal = task.input_task.user_idea || task.input_task.request;

  const submit = async () => {
    if (!selectedRepository || !executionContractReady) return;
    setSubmitting(true);
    setError('');
    try {
      await onConfirm(selectedRepository.sourceId);
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

        <div style={{ marginBottom: 8, fontSize: '0.82em' }}>
          <span style={{ color: 'var(--text-muted)' }}>主指标：</span>
          <span style={{ color: 'var(--green)' }}>{task.input_task.primary_metrics.join('、')}</span>
        </div>
        <div style={{ marginBottom: 16, fontSize: '0.84em', color: 'var(--text)' }}>
          确认后将交给实验 Agent 继续准备环境并编排实验。
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 6 }}>执行仓库</div>
          <select
            value={executionRepositorySourceId}
            onChange={event => setExecutionRepositorySourceId(event.target.value)}
            aria-label="执行仓库"
          >
            <option value="">请选择代码仓库</option>
            {availableRepositories.map(source => (
              <option key={source.sourceId} value={source.sourceId}>
                {source.label}
              </option>
            ))}
          </select>
          {selectedRepository && (
            <div style={{ marginTop: 6, fontSize: '0.82em', color: 'var(--text-muted)' }}>
              已选择：{selectedRepository.label}
            </div>
          )}
        </div>

        {error && <div style={{ color: 'var(--red)', fontSize: '0.82em', marginBottom: 12 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="primary"
            ref={confirmButtonRef}
            onClick={submit}
            disabled={submitting || !executionContractReady || !selectedRepository}
            style={{ flex: 1 }}
          >
            {submitting ? '交接中…' : '确认并交给实验 Agent'}
          </button>
          <button onClick={onClose} disabled={submitting} style={{ flex: 1 }}>还需要细化实验草案</button>
        </div>
      </div>
    </div>
  );
}
