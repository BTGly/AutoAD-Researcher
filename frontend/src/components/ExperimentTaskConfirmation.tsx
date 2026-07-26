import { useRef, useState } from 'react';
import { FlaskConical, GitBranch, Pencil } from 'lucide-react';
import type { ExperimentTaskDraft, SourceItem } from '../lib/types';
import { useDialogFocus } from '../hooks/useDialogFocus';
import { AppButton } from './ui/AppButton';

interface Props {
  task: ExperimentTaskDraft;
  sources: SourceItem[];
  onConfirm: () => Promise<void>;
  onClose: () => void;
}

export function ExperimentTaskConfirmation({ task, sources, onConfirm, onClose }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(confirmButtonRef, { dialogRef, onClose });
  const binding = task.execution_repository_binding;
  const repository = sources.find(source => source.sourceId === binding?.source_id);
  const goal = task.input_task.user_idea || task.input_task.request;

  if (!binding) return null;

  const submit = async () => {
    setSubmitting(true);
    setError('');
    try {
      await onConfirm();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '实验任务确认失败');
      setSubmitting(false);
    }
  };

  return (
    <div ref={dialogRef} className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="experiment-confirmation-title">
      <div className="modal experiment-confirmation-modal">
        <header className="decision-modal-heading">
          <span className="decision-modal-icon"><FlaskConical size={18} strokeWidth={1.8} aria-hidden="true" /></span>
          <div>
            <h2 id="experiment-confirmation-title">确认实验任务</h2>
            <p>{goal}</p>
          </div>
        </header>

        <dl className="experiment-confirmation-facts">
          <div>
            <dt>主指标</dt>
            <dd>{task.input_task.primary_metrics.join('、')}</dd>
          </div>
          <div>
            <dt><GitBranch size={14} strokeWidth={1.8} aria-hidden="true" />执行仓库</dt>
            <dd>{repository?.label || binding.repository_ref}</dd>
          </div>
        </dl>

        <p className="decision-modal-impact">
          确认后将创建实验 Session，并排队环境准备任务。执行仓库和主指标将按当前任务冻结。
        </p>

        {error && <div className="decision-modal-error" role="alert">{error}</div>}
        <div className="decision-modal-actions experiment-confirmation-actions">
          <AppButton ref={confirmButtonRef} variant="primary" onClick={() => void submit()} disabled={submitting} aria-busy={submitting}>
            <FlaskConical size={16} strokeWidth={1.8} aria-hidden="true" />
            {submitting ? '交接中…' : '确认并交给实验 Agent'}
          </AppButton>
          <AppButton onClick={onClose} disabled={submitting}>
            <Pencil size={16} strokeWidth={1.8} aria-hidden="true" />
            继续细化草案
          </AppButton>
        </div>
      </div>
    </div>
  );
}
