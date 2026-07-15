import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ContractConfirmationModal } from './ContractConfirmationModal';
import type { DraftState } from '../lib/types';


describe('ContractConfirmationModal', () => {
  it('shows a pending contract and delegates approval to the explicit button', () => {
    const onConfirm = vi.fn();
    const draft: DraftState = {
      ready: true,
      has_draft: true,
      title: '研究计划草案',
      fields: [],
      missing: [],
      sources: [],
      evidence: [],
      jobs: [],
      next_questions: [],
      confirmation: {
        confirmation_id: 'contract_confirmation_1',
        draft_hash: 'a'.repeat(64),
        status: 'pending',
        requested_at: '2026-07-14T00:00:00Z',
        fields: [{ field: 'baseline', label: '基线方法', value: 'PatchCore', status: 'known' }],
      },
    };

    render(
      <ContractConfirmationModal
        draft={draft}
        busy={false}
        error=""
        onConfirm={onConfirm}
        onRevise={vi.fn()}
      />,
    );

    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('PatchCore')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '确认合同' }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});
