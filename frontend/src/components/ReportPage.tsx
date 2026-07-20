import { useState, useEffect } from 'react';
import { getLatestVersionedReport } from '../lib/api';
import { MarkdownContent } from './MarkdownContent';

interface Props {
  runId: string;
  onBack: () => void;
}

export function ReportPage({ runId, onBack }: Props) {
  const [report, setReport] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    getLatestVersionedReport(runId)
      .then(res => setReport(res.content || null))
      .catch(() => setReport(null))
      .finally(() => setLoading(false));
  }, [runId]);

  return (
    <div style={{
      flex: 1, height: '100%', overflow: 'auto',
      display: 'flex', justifyContent: 'center',
    }}>
      <div style={{ width: 800, maxWidth: '90%', padding: '32px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div>
            <div style={{ fontSize: '1.3em', fontWeight: 600, color: 'var(--text)' }}>
              Research Report
            </div>
            <div style={{ fontSize: '0.82em', color: 'var(--text-muted)', marginTop: 4 }}>
              {runId ? `Run: ${runId}` : 'No active run'}
            </div>
          </div>
          <button
            onClick={onBack}
            style={{
              padding: '6px 14px', border: '1px solid var(--border)', borderRadius: 6,
              background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer',
              fontSize: '0.85em',
            }}
          >
            Back to Chat
          </button>
        </div>

        {loading && (
          <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 60 }}>
            Loading report...
          </div>
        )}

        {!loading && !report && (
          <div style={{
            textAlign: 'center', padding: 60,
            border: '1px solid var(--border)', borderRadius: 8,
            color: 'var(--text-muted)',
          }}>
            <div style={{ fontSize: '2em', marginBottom: 12 }}>📊</div>
            <div style={{ fontSize: '1em', marginBottom: 8 }}>No Report Generated Yet</div>
            <div style={{ fontSize: '0.82em', color: 'var(--text-dim)' }}>
              A research report will appear here after experiment agents complete their run.
            </div>
          </div>
        )}

        {!loading && report && (
          <div style={{
            border: '1px solid var(--border)', borderRadius: 8,
            padding: 24, background: 'var(--bg)',
            fontSize: '0.9em', lineHeight: 1.7,
            fontFamily: 'inherit',
          }}>
            <MarkdownContent>{report}</MarkdownContent>
          </div>
        )}
      </div>
    </div>
  );
}
