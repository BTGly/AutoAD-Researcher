import { useMemo, useRef, useState } from 'react';
import { BookOpen, Check, GitBranch, X } from 'lucide-react';
import type { ExperimentTaskDraft, ExperimentTaskReadiness } from '../lib/types';
import { useDialogFocus } from '../hooks/useDialogFocus';
import { AppButton } from './ui/AppButton';

interface Props {
  task: ExperimentTaskDraft;
  readiness: ExperimentTaskReadiness;
  onDecision: (
    sourceId: string,
    decision: 'confirm' | 'reference_only' | 'cancel',
  ) => Promise<void>;
  onClose: () => void;
}

export function ExecutionRepositoryAuthorization({ task, readiness, onDecision, onClose }: Props) {
  const candidates = readiness.execution_repository_candidates;
  const initialSourceId = useMemo(() => {
    const executable = candidates.find(candidate => candidate.assigned_role === 'executable');
    return executable?.source_id || (candidates.length === 1 ? candidates[0].source_id : '');
  }, [candidates]);
  const [sourceId, setSourceId] = useState(initialSourceId);
  const [submitting, setSubmitting] = useState<'confirm' | 'reference_only' | 'cancel' | null>(null);
  const [error, setError] = useState('');
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(confirmRef, { dialogRef, onClose });

  const selected = candidates.find(candidate => candidate.source_id === sourceId);
  const submit = async (decision: 'confirm' | 'reference_only' | 'cancel') => {
    if (!selected) return;
    setSubmitting(decision);
    setError('');
    try {
      await onDecision(selected.source_id, decision);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '执行仓库授权未生效');
      setSubmitting(null);
    }
  };

  return (
    <div ref={dialogRef} className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="repository-authorization-title">
      <div className="modal repository-authorization-modal">
        <header className="decision-modal-heading">
          <span className="decision-modal-icon"><GitBranch size={18} strokeWidth={1.8} aria-hidden="true" /></span>
          <div>
            <h2 id="repository-authorization-title">确认执行仓库</h2>
            <p>{task.input_task.user_idea || task.input_task.request}</p>
          </div>
        </header>

        <p className="decision-modal-impact">
          获得授权的仓库将用于后续环境准备、代码修改和实验运行。此处授权不会立即启动实验。
        </p>

        {readiness.execution_repository_state === 'repository_admission_failed' && (
          <div className="decision-modal-error" role="alert">
            {readiness.execution_repository_admission_blocker || '仓库身份或执行适配检查已失效，请重新确认。'}
          </div>
        )}

        <fieldset className="repository-choice-list">
          <legend>可用代码仓库</legend>
          {candidates.map(candidate => (
            <label className={`repository-choice${candidate.source_id === sourceId ? ' selected' : ''}`} key={candidate.source_id}>
              <input
                type="radio"
                name="execution-repository"
                value={candidate.source_id}
                checked={candidate.source_id === sourceId}
                onChange={() => setSourceId(candidate.source_id)}
              />
              <span className="repository-choice-copy">
                <strong>{candidate.label}</strong>
                {candidate.stored_path && candidate.stored_path !== candidate.label && <span>{candidate.stored_path}</span>}
                <small>{candidate.adapter_id ? `执行适配：${candidate.adapter_id}` : '执行适配已检查'}</small>
              </span>
            </label>
          ))}
        </fieldset>

        {error && <div className="decision-modal-error" role="alert">{error}</div>}
        <div className="decision-modal-actions">
          <AppButton
            ref={confirmRef}
            variant="primary"
            disabled={!selected || submitting !== null}
            aria-busy={submitting === 'confirm'}
            onClick={() => void submit('confirm')}
          >
            <Check size={16} strokeWidth={1.9} aria-hidden="true" />
            {submitting === 'confirm' ? '授权中…' : '授权用于实验'}
          </AppButton>
          <AppButton
            disabled={!selected || submitting !== null}
            aria-busy={submitting === 'reference_only'}
            onClick={() => void submit('reference_only')}
          >
            <BookOpen size={16} strokeWidth={1.8} aria-hidden="true" />
            仅作参考
          </AppButton>
          <AppButton
            variant="plain"
            disabled={!selected || submitting !== null}
            aria-busy={submitting === 'cancel'}
            onClick={() => void submit('cancel')}
          >
            <X size={16} strokeWidth={1.8} aria-hidden="true" />
            稍后决定
          </AppButton>
        </div>
      </div>
    </div>
  );
}
