import { useEffect, useState } from 'react';
import { Download, ExternalLink, RefreshCw, Send } from 'lucide-react';
import { getLatestContentReadyReport, getLatestCreatedReport, getReportContent, getReportDigest, getReportDiscussion, getReportState, listReportEvidence, listReports, sendReportDiscussion } from '../lib/api';
import type { DiscussionMessage, ReportDigest, ReportEvidence, ReportManifest, ReportState } from '../lib/types';
import { MarkdownContent } from './MarkdownContent';

interface Props { runId: string; onBack: () => void; }
const artifactUrl = (runId: string, reportId: string, artifact: string) => `/api/runs/${runId}/reports/${reportId}/download/${artifact}`;

export function ReportPage({ runId, onBack }: Props) {
  const [reports, setReports] = useState<ReportManifest[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [latest, setLatest] = useState<ReportManifest | null>(null);
  const [state, setState] = useState<ReportState | null>(null);
  const [digest, setDigest] = useState<ReportDigest | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<ReportEvidence[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [discussion, setDiscussion] = useState<DiscussionMessage[]>([]);
  const [question, setQuestion] = useState('');
  const [sending, setSending] = useState(false);
  const load = async () => {
    if (!runId) return;
    setLoading(true); setError(null);
    try {
      const [all, ready, created] = await Promise.all([listReports(runId), getLatestContentReadyReport(runId), getLatestCreatedReport(runId)]);
      setReports(all); setLatest(created);
      setSelectedId(current => current && all.some(item => item.report_id === current) ? current : (ready?.report_id ?? created?.report_id ?? null));
    } catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取报告状态'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [runId]);
  useEffect(() => {
    if (!selectedId) { setState(null); setDigest(null); setContent(null); setEvidence([]); return; }
    let active = true;
    void getReportState(runId, selectedId)
      .then(async nextState => {
        if (!active) return;
        setState(nextState);
        if (nextState.generation_status !== 'content_ready') {
          setDigest(null); setContent(null); setEvidence([]); setDiscussion([]);
          return;
        }
        const [nextDigest, nextContent, nextEvidence, nextDiscussion] = await Promise.all([
          getReportDigest(runId, selectedId),
          getReportContent(runId, selectedId),
          listReportEvidence(runId, selectedId),
          getReportDiscussion(runId, selectedId),
        ]);
        if (!active) return;
        setDigest(nextDigest); setContent(nextContent); setEvidence(nextEvidence); setDiscussion(nextDiscussion.messages);
      })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '无法读取固定版本报告'); });
    return () => { active = false; };
  }, [runId, selectedId]);
  const selected = reports.find(item => item.report_id === selectedId) ?? null;
  const artifacts = state?.available_artifacts ?? [];
  const discussionReady = state?.generation_status === 'content_ready' && artifacts.includes('report.md') && artifacts.includes('report_validation.json');
  const sendDiscussion = async () => { if (!selected || !discussionReady || !question.trim() || sending) return; setSending(true); try { await sendReportDiscussion(runId, selected.report_id, `report.${selected.report_id}.${crypto.randomUUID()}`, question.trim()); setQuestion(''); setDiscussion((await getReportDiscussion(runId, selected.report_id)).messages); } catch (reason) { setError(reason instanceof Error ? reason.message : '讨论请求失败'); } finally { setSending(false); } };
  return <main style={{ flex: 1, overflow: 'auto', padding: '20px 28px', color: 'var(--text)' }}>
    <header style={{ display: 'flex', gap: 12, justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
      <div><h1 style={{ fontSize: '1.15rem', margin: 0 }}>实验报告</h1><div style={{ color: 'var(--text-muted)', fontSize: '.82rem', marginTop: 3 }}>固定版本、证据与交付制品</div></div>
      <div style={{ display: 'flex', gap: 8 }}><button title="刷新报告" aria-label="刷新报告" onClick={() => void load()} disabled={loading}><RefreshCw size={16} /></button><button onClick={onBack}>返回对话</button></div>
    </header>
    {latest && latest.report_id !== selectedId && <div style={{ border: '1px solid var(--orange)', padding: 9, borderRadius: 6, marginBottom: 12, fontSize: '.84rem' }}>较新版本 v{latest.version} 正在{latest.generation_status}，当前继续显示已选可读版本。</div>}
    {error && <div role="alert" style={{ color: 'var(--orange)', marginBottom: 12 }}>{error}</div>}
    {!selected && !loading && <div style={{ color: 'var(--text-muted)', padding: 36 }}>当前 run 尚未生成报告。</div>}
    {selected && <><section style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: 12 }}>
      <label>版本 <select value={selectedId ?? ''} onChange={event => setSelectedId(event.target.value)}>{reports.map(item => <option key={item.report_id} value={item.report_id}>v{item.version} · {item.generation_status}</option>)}</select></label>
      <span>生成：{state?.generation_status ?? selected.generation_status}</span><span>审阅：{state?.review_status ?? selected.review_status}</span>
      {Object.entries(state?.format_status ?? selected.format_status).map(([name, value]) => <span key={name}>{name}: {value}</span>)}
      {artifacts.includes('report.html') && <a title="在新窗口打开 HTML" href={artifactUrl(runId, selected.report_id, 'report.html')} target="_blank" rel="noreferrer"><ExternalLink size={16} /></a>}
      {['report.pdf', 'report_bundle.zip'].filter(item => artifacts.includes(item)).map(item => <a key={item} title={`下载 ${item}`} href={artifactUrl(runId, selected.report_id, item)}><Download size={16} /></a>)}
    </section>
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(250px, .42fr)', gap: 18, marginTop: 16 }}>
      <section style={{ minWidth: 0 }}>{content ? <MarkdownContent>{content}</MarkdownContent> : <div style={{ color: 'var(--text-muted)', padding: 24 }}>{state?.last_error ? `生成失败：${state.last_error}` : '此版本尚无可读 Markdown。'}</div>}</section>
      <aside style={{ borderLeft: '1px solid var(--border)', paddingLeft: 16 }}><h2 style={{ fontSize: '.95rem', marginTop: 0 }}>摘要</h2>{digest ? <div style={{ fontSize: '.84rem', lineHeight: 1.6 }}><div>执行状态：{digest.execution_status ?? '未记录'}</div><div>Champion：{String(digest.champion.current_by_contract ? '已记录' : digest.champion.status ?? '未记录')}</div><div>停止：{String(digest.stop_decision.reason ?? digest.stop_decision.status ?? '未记录')}</div><div>Attempts：{digest.attempt_count}，失败：{digest.failed_attempt_count}，不可比：{digest.non_comparable_attempt_count}</div>{digest.uncertainties.map(item => <div key={item} style={{ color: 'var(--text-muted)', marginTop: 6 }}>{item}</div>)}</div> : <div style={{ color: 'var(--text-muted)' }}>摘要尚不可用。</div>}<h2 style={{ fontSize: '.95rem', marginTop: 20 }}>证据</h2>{evidence.map(item => <details key={item.evidence_id} style={{ borderTop: '1px solid var(--border)', padding: '8px 0', fontSize: '.8rem' }}><summary>{item.evidence_kind} · {item.evidence_id}</summary><div style={{ marginTop: 5, color: 'var(--text-muted)' }}>{item.summary}</div>{item.attempt_id && <div>Attempt：{item.attempt_id}</div>}{item.idea_id && <div>Idea：{item.idea_id}</div>}<div style={{ overflowWrap: 'anywhere' }}>SHA：{item.artifact_ref.sha256}</div></details>)}<h2 style={{ fontSize: '.95rem', marginTop: 20 }}>讨论</h2><div style={{ maxHeight: 220, overflow: 'auto', fontSize: '.82rem' }}>{discussion.map(item => <div key={item.message_id} style={{ margin: '8px 0', color: item.role === 'assistant' ? 'var(--text)' : 'var(--text-muted)' }}>{item.content}</div>)}</div><div style={{ display: 'flex', gap: 6, marginTop: 8 }}><input aria-label="报告讨论" value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void sendDiscussion(); }} disabled={!discussionReady} /><button aria-label="发送报告讨论" title="发送报告讨论" onClick={() => void sendDiscussion()} disabled={!discussionReady || sending || !question.trim()}><Send size={16} /></button></div></aside>
    </div></>}
  </main>;
}
