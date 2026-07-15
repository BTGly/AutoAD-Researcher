import { describe, expect, it } from 'vitest';
import { sourceLifecycleMeta } from './Sidebar';

describe('sourceLifecycleMeta', () => {
  it('distinguishes acquired repositories from analyzed repositories', () => {
    const acquired = sourceLifecycleMeta({
      sourceId: 'src_repo',
      kind: 'github_repo',
      label: 'Library-A',
      status: 'uploaded_not_parsed',
      registrationStatus: 'succeeded',
      acquisitionStatus: 'succeeded',
      parseStatus: 'not_applicable',
      analysisStatus: 'pending',
      evidenceStatus: 'pending',
    });
    const analyzed = sourceLifecycleMeta({
      sourceId: 'src_repo',
      kind: 'github_repo',
      label: 'Library-A',
      status: 'parsed',
      registrationStatus: 'succeeded',
      acquisitionStatus: 'succeeded',
      parseStatus: 'not_applicable',
      analysisStatus: 'succeeded',
      evidenceStatus: 'succeeded',
    });

    expect(acquired.label).toBe('仓库已下载，待分析');
    expect(analyzed.label).toBe('分析完成');
  });

  it('reports the failed lifecycle dimension', () => {
    const failed = sourceLifecycleMeta({
      sourceId: 'src_pdf',
      kind: 'paper_pdf',
      label: 'paper.pdf',
      status: 'failed',
      registrationStatus: 'succeeded',
      acquisitionStatus: 'succeeded',
      parseStatus: 'failed',
      analysisStatus: 'pending',
      evidenceStatus: 'pending',
    });

    expect(failed).toEqual({ label: '解析失败', tone: 'bad' });
  });
});
