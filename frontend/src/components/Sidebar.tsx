import { useState } from 'react';
import type { SourceItem, JobItem, TabId } from '../lib/types';

interface Props {
  sources: SourceItem[];
  jobs: JobItem[];
  evidenceCount: number;
  draftReady: boolean;
}

export function Sidebar({ sources, jobs, evidenceCount, draftReady }: Props) {
  const [tab, setTab] = useState<TabId>('sources');

  const tabs: { id: TabId; label: string; count: number }[] = [
    { id: 'sources', label: 'Sources', count: sources.length },
    { id: 'jobs', label: 'Jobs', count: jobs.length },
    { id: 'evidence', label: 'Evidence', count: evidenceCount },
    { id: 'draft', label: 'Draft', count: draftReady ? 1 : 0 },
  ];

  return (
    <div style={{
      width: 280, borderLeft: '1px solid var(--border)', background: 'var(--bg-panel)',
      display: 'flex', flexDirection: 'column', height: '100%',
    }}>
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)' }}>
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              flex: 1, borderRadius: 0, border: 'none',
              borderBottom: tab === t.id ? '2px solid var(--blue)' : '2px solid transparent',
              background: 'transparent', padding: '10px 8px', fontSize: '0.8em',
              color: tab === t.id ? 'var(--blue)' : 'var(--text-muted)',
            }}
          >
            {t.label} {t.count > 0 && <span style={{ color: 'var(--text-dim)' }}>({t.count})</span>}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
        {tab === 'sources' && <SourcesList sources={sources} />}
        {tab === 'jobs' && <JobsList jobs={jobs} />}
        {tab === 'evidence' && <p style={{ color: 'var(--text-muted)', fontSize: '0.85em' }}>{evidenceCount} usable evidence items</p>}
        {tab === 'draft' && <p style={{ color: 'var(--text-muted)', fontSize: '0.85em' }}>{draftReady ? 'Research draft ready.' : 'No draft yet.'}</p>}
      </div>
    </div>
  );
}

function SourcesList({ sources }: { sources: SourceItem[] }) {
  if (!sources.length) return <p style={{ color: 'var(--text-muted)', fontSize: '0.85em' }}>暂无 source。</p>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {sources.map(s => (
        <div key={s.sourceId} style={{ padding: '6px 8px', background: 'var(--bg)', borderRadius: 6, fontSize: '0.82em' }}>
          <div style={{ color: 'var(--blue)', marginBottom: 2 }}>{s.label}</div>
          <div style={{ color: 'var(--text-dim)' }}>{s.kind} · {s.status}</div>
        </div>
      ))}
    </div>
  );
}

function JobsList({ jobs }: { jobs: JobItem[] }) {
  if (!jobs.length) return <p style={{ color: 'var(--text-muted)', fontSize: '0.85em' }}>暂无 job。</p>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {jobs.map(j => (
        <div key={j.jobId} style={{ padding: '6px 8px', background: 'var(--bg)', borderRadius: 6, fontSize: '0.82em' }}>
          <div style={{ marginBottom: 2 }}>{j.jobType}</div>
          <div style={{ color: j.status === 'completed' ? 'var(--green)' : j.status === 'failed' ? 'var(--red)' : 'var(--orange)' }}>
            {j.status}
          </div>
        </div>
      ))}
    </div>
  );
}
