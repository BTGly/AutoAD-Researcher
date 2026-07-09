import { useState } from 'react';
import type { SourceItem, JobItem, EvidenceItem, UnusableParsedSource, TabId, DraftState } from '../lib/types';

interface Props {
  sources: SourceItem[];
  jobs: JobItem[];
  evidence: EvidenceItem[];
  unusableParsedSources: UnusableParsedSource[];
  evidenceCount: number;
  draftReady: boolean;
  draft?: DraftState | null;
  onDeleteSource?: (sourceId: string) => void;
  children?: React.ReactNode;
}

export function Sidebar({ sources, jobs, evidence, unusableParsedSources, evidenceCount, draftReady, draft, onDeleteSource, children }: Props) {
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
        {tab === 'sources' && <SourcesList sources={sources} onDeleteSource={onDeleteSource} />}
        {tab === 'jobs' && <JobsList jobs={jobs} />}
        {tab === 'evidence' && <EvidenceList evidence={evidence} unusableParsedSources={unusableParsedSources} />}
        {tab === 'draft' && <DraftPanel draft={draft || null} />}
      </div>
      {children}
    </div>
  );
}

function EvidenceList({ evidence, unusableParsedSources }: { evidence: EvidenceItem[]; unusableParsedSources: UnusableParsedSource[] }) {
  if (!evidence.length && !unusableParsedSources.length) {
    return <p style={{ color: 'var(--text-muted)', fontSize: '0.85em' }}>暂无 usable evidence。</p>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {unusableParsedSources.map(item => (
        <div key={`${item.sourceId}-${item.parseAttemptId}`} style={{ padding: '8px 9px', background: 'var(--bg)', borderRadius: 6, fontSize: '0.82em', border: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
            <span style={{ color: 'var(--orange)' }}>{item.label}</span>
            <span style={{ color: 'var(--red)', flexShrink: 0 }}>unusable</span>
          </div>
          <div style={{ color: 'var(--text)', lineHeight: 1.45 }}>
            PDF 已处理，但没有产出可读正文 evidence。
          </div>
          <div style={{ color: 'var(--text-dim)', marginTop: 5, overflowWrap: 'anywhere' }}>
            {item.parser || 'parser unknown'} · {item.parseAttemptId || item.sourceId}
          </div>
          {item.warnings.length > 0 && (
            <div style={{ color: 'var(--text-dim)', marginTop: 5, overflowWrap: 'anywhere' }}>
              {item.warnings.slice(0, 2).join(' · ')}
            </div>
          )}
          {knownParseReasons(item).length > 0 && (
            <div style={{ color: 'var(--red)', marginTop: 5, overflowWrap: 'anywhere', lineHeight: 1.35 }}>
              {knownParseReasons(item).slice(0, 3).join(' · ')}
            </div>
          )}
        </div>
      ))}
      {evidence.map((item, index) => (
        <div key={`${item.artifactPath}-${index}`} style={{ padding: '8px 9px', background: 'var(--bg)', borderRadius: 6, fontSize: '0.82em', border: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
            <span style={{ color: 'var(--blue)' }}>{item.evidenceType || 'evidence'}</span>
            <span style={{ color: item.supportLevel === 'supported' ? 'var(--green)' : 'var(--orange)', flexShrink: 0 }}>{item.supportLevel}</span>
          </div>
          {item.summary && (
            <div style={{ color: 'var(--text)', lineHeight: 1.45, overflowWrap: 'anywhere' }}>
              {item.summary.length > 360 ? item.summary.slice(0, 359) + '…' : item.summary}
            </div>
          )}
          <div style={{ color: 'var(--text-dim)', marginTop: 5, overflowWrap: 'anywhere' }}>
            {item.parserName || 'parser unknown'} · {item.artifactPath}
          </div>
          {detailPath(item) && (
            <div style={{ color: 'var(--text-dim)', marginTop: 5, overflowWrap: 'anywhere' }}>
              detail: {detailPath(item)}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function detailPath(item: EvidenceItem): string {
  const raw = item.raw || {};
  if (typeof raw.source_markdown === 'string' && raw.source_markdown) return raw.source_markdown;
  if (typeof raw.detail_path === 'string' && raw.detail_path) return raw.detail_path;
  if (typeof raw.source_of_truth === 'string' && raw.source_of_truth) return raw.source_of_truth;
  return '';
}

function SourcesList({ sources, onDeleteSource }: { sources: SourceItem[]; onDeleteSource?: (sourceId: string) => void }) {
  if (!sources.length) return <p style={{ color: 'var(--text-muted)', fontSize: '0.85em' }}>暂无 source。</p>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {sources.map(s => (
        <div key={s.sourceId} style={{ padding: '6px 8px', background: 'var(--bg)', borderRadius: 6, fontSize: '0.82em' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'start' }}>
            <div style={{ color: 'var(--blue)', marginBottom: 2, overflowWrap: 'anywhere' }}>{s.label}</div>
            {onDeleteSource && (
              <button
                onClick={() => onDeleteSource(s.sourceId)}
                title="删除资料"
                style={{ border: 'none', background: 'transparent', color: 'var(--text-dim)', padding: '0 2px', flexShrink: 0 }}
              >
                ×
              </button>
            )}
          </div>
          <div style={{ color: 'var(--text-dim)' }}>{s.kind} · {s.status}</div>
        </div>
      ))}
    </div>
  );
}

function DraftPanel({ draft }: { draft: DraftState | null }) {
  if (!draft || !draft.has_draft) {
    return <p style={{ color: 'var(--text-muted)', fontSize: '0.85em' }}>暂无研究计划草案。</p>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: '0.82em' }}>
      <div style={{ padding: '8px 9px', background: 'var(--bg)', borderRadius: 6, border: '1px solid var(--border)' }}>
        <div style={{ color: draft.ready ? 'var(--green)' : 'var(--orange)', marginBottom: 6 }}>
          {draft.ready ? '已具备规划条件' : '仍有缺口'}
        </div>
        <div style={{ color: 'var(--text-dim)', lineHeight: 1.4 }}>
          这个草案会随对话、Sources、Evidence 实时更新。
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {draft.fields.map(field => (
          <div key={field.field} style={{ padding: '7px 8px', background: 'var(--bg)', borderRadius: 6 }}>
            <div style={{ color: field.status === 'missing' ? 'var(--orange)' : 'var(--blue)', marginBottom: 3 }}>
              {field.label}
            </div>
            <div style={{ color: 'var(--text)', lineHeight: 1.35, overflowWrap: 'anywhere' }}>{field.value}</div>
          </div>
        ))}
      </div>

      {draft.missing.length > 0 && (
        <div style={{ padding: '8px 9px', background: 'var(--bg)', borderRadius: 6, border: '1px solid var(--border)' }}>
          <div style={{ color: 'var(--orange)', marginBottom: 5 }}>缺少</div>
          <div style={{ color: 'var(--text)', lineHeight: 1.45 }}>
            {draft.missing.map(item => item.label).join('、')}
          </div>
        </div>
      )}

      {draft.next_questions.length > 0 && (
        <div style={{ padding: '8px 9px', background: 'var(--bg)', borderRadius: 6, border: '1px solid var(--border)' }}>
          <div style={{ color: 'var(--blue)', marginBottom: 5 }}>下一步可补充</div>
          {draft.next_questions.map((question, index) => (
            <div key={`${question}-${index}`} style={{ color: 'var(--text)', lineHeight: 1.45, marginTop: index ? 5 : 0 }}>
              {question}
            </div>
          ))}
        </div>
      )}

      {draft.evidence.length > 0 && (
        <div style={{ color: 'var(--text-dim)', lineHeight: 1.4 }}>
          已有 Evidence：{draft.evidence.map(item => item.type).join('、')}
        </div>
      )}
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
          {j.error && (
            <div style={{ color: 'var(--text-dim)', marginTop: 4, overflowWrap: 'anywhere', lineHeight: 1.35 }}>
              {j.error.length > 260 ? j.error.slice(0, 259) + '…' : j.error}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function knownParseReasons(item: UnusableParsedSource): string[] {
  const reasons: string[] = [];
  for (const err of item.fatalErrors || []) {
    if (err) reasons.push(String(err));
  }
  for (const parserError of item.parserErrors || []) {
    const parser = parserError.parser_name || parserError.parserName || 'parser';
    if (parserError.error) reasons.push(`${parser}: ${parserError.error}`);
  }
  return reasons;
}
