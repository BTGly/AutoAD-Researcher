import { Check, PencilLine, ShieldCheck } from 'lucide-react';
import type { DraftState } from '../lib/types';

interface Props {
  draft: DraftState;
  busy: boolean;
  error: string;
  onConfirm: () => void;
  onRevise: () => void;
}

const SUMMARY_FIELDS = new Set([
  'research_goal',
  'baseline',
  'dataset',
  'primary_metrics',
  'success_criteria',
  'execution_mode',
]);

export function ContractConfirmationModal({ draft, busy, error, onConfirm, onRevise }: Props) {
  const fields = draft.fields.filter(field => SUMMARY_FIELDS.has(field.field) && field.status === 'known');

  return (
    <div className="modal-overlay confirmation-overlay">
      <div
        className="modal contract-confirmation-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="contract-confirmation-title"
      >
        <div className="confirmation-heading">
          <ShieldCheck size={22} aria-hidden="true" />
          <div>
            <h2 id="contract-confirmation-title">确认研究任务合同</h2>
            <p>请核对将交给后续 agents 的研究边界。</p>
          </div>
        </div>

        <div className="confirmation-fields">
          {fields.map(field => (
            <div className="confirmation-field" key={field.field}>
              <span>{field.label}</span>
              <strong>{field.value}</strong>
            </div>
          ))}
        </div>

        <p className="confirmation-boundary">
          确认只会保存合同，不会自动修改代码或运行实验。
        </p>
        {error && <p className="confirmation-error" role="alert">{error}</p>}

        <div className="confirmation-actions">
          <button onClick={onRevise} disabled={busy}>
            <PencilLine size={16} aria-hidden="true" />
            继续修改
          </button>
          <button className="primary" onClick={onConfirm} disabled={busy}>
            <Check size={16} aria-hidden="true" />
            {busy ? '处理中...' : '确认合同'}
          </button>
        </div>
      </div>
    </div>
  );
}
