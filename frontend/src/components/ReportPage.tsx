import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, ExternalLink, RefreshCw, Send } from 'lucide-react';
import { confirmReportProposal, createHumanProposal, getLatestContentReadyReport, getLatestCreatedReport, getReportContent, getReportDigest, getReportDiscussion, getReportState, listReportEvidence, listReportProposals, listReports, recordReportReview, rejectReportProposal, sendReportDiscussion } from '../lib/api';
import type { DiscussionMessage, ReportDigest, ReportEvidence, ReportManifest, ReportProposal, ReportState } from '../lib/types';
import { reportChampionStatusLabel, reportEngineeringStatusLabel, reportEvidenceKindLabel, reportExecutionStatusLabel, reportFormatLabel, reportFormatStatusLabel, reportGenerationStatusLabel, reportHandoffKindLabel, reportJobStatusLabel, reportJobTypeLabel, reportProposalStatusLabel, reportProposalTypeLabel, reportReviewStatusLabel, reportScientificStatusLabel } from '../lib/reportLabels';
import { MarkdownContent } from './MarkdownContent';
import { AppButton } from './ui/AppButton';
import { EmptyState } from './ui/EmptyState';
import { StatusBadge } from './ui/StatusBadge';

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
  const [selectedRevision, setSelectedRevision] = useState(0);
  const [proposals, setProposals] = useState<ReportProposal[]>([]);
  const [proposalRationale, setProposalRationale] = useState('');
  const [reviewComment, setReviewComment] = useState('');
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const discussionRetry = useRef<{ reportId: string; content: string; requestId: string } | null>(null);
  const load = useCallback(async () => {
    if (!runId) return;
    setLoading(true); setError(null);
    try {
      const [all, ready, created] = await Promise.all([listReports(runId), getLatestContentReadyReport(runId), getLatestCreatedReport(runId)]);
      setReports(all); setLatest(created);
      setSelectedId(current => current && all.some(item => item.report_id === current) ? current : (ready?.report_id ?? created?.report_id ?? null));
    } catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取报告状态'); }
    finally { setLoading(false); }
  }, [runId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!selectedId) { setState(null); setDigest(null); setContent(null); setEvidence([]); setDiscussion([]); setProposals([]); return; }
    let active = true;
    void getReportState(runId, selectedId)
      .then(async nextState => {
        if (!active) return;
        setState(nextState);
        if (nextState.generation_status !== 'content_ready') {
          setDigest(null); setContent(null); setEvidence([]); setDiscussion([]); setProposals([]);
          return;
        }
        const [nextDigest, nextContent, nextEvidence, nextDiscussion, nextProposals] = await Promise.all([
          getReportDigest(runId, selectedId),
          getReportContent(runId, selectedId),
          listReportEvidence(runId, selectedId),
          getReportDiscussion(runId, selectedId),
          listReportProposals(runId, selectedId),
        ]);
        if (!active) return;
        setDigest(nextDigest); setContent(nextContent); setEvidence(nextEvidence); setDiscussion(nextDiscussion.messages); setProposals(nextProposals);
      })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '无法读取固定版本报告'); });
    return () => { active = false; };
  }, [runId, selectedId, selectedRevision]);
  const selected = reports.find(item => item.report_id === selectedId) ?? null;
  const visibleState = state?.report_id === selectedId ? state : null;
  const visibleDigest = visibleState ? digest : null;
  const visibleContent = visibleState ? content : null;
  const visibleEvidence = visibleState ? evidence : [];
  const visibleDiscussion = visibleState ? discussion : [];
  const visibleProposals = visibleState ? proposals : [];
  const artifacts = visibleState?.available_artifacts ?? [];
  const readable = visibleState?.generation_status === 'content_ready';
  const discussionReady = readable && artifacts.includes('report.md') && artifacts.includes('report_validation.json');
  const sendDiscussion = async () => {
    if (!selected || !discussionReady || !question.trim() || sending) return;
    const requestedQuestion = question.trim();
    const previous = discussionRetry.current;
    const requestId = previous?.reportId === selected.report_id && previous.content === requestedQuestion
      ? previous.requestId
      : `report.${selected.report_id}.${crypto.randomUUID()}`;
    discussionRetry.current = { reportId: selected.report_id, content: requestedQuestion, requestId };
    setSending(true);
    try {
      await sendReportDiscussion(runId, selected.report_id, requestId, requestedQuestion);
      discussionRetry.current = null;
      setError(null);
      setQuestion('');
      setDiscussion((await getReportDiscussion(runId, selected.report_id)).messages);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '讨论请求失败');
    } finally {
      setSending(false);
    }
  };
  const refreshProposals = async () => { if (selected) setProposals(await listReportProposals(runId, selected.report_id)); };
  const proposeHuman = async () => { if (!selected || !readable || !proposalRationale.trim() || actionBusy) return; setActionBusy('proposal:create'); try { await createHumanProposal(runId, selected.report_id, proposalRationale.trim()); setProposalRationale(''); await refreshProposals(); } catch (reason) { setError(reason instanceof Error ? reason.message : 'Proposal 创建失败'); } finally { setActionBusy(null); } };
  const changeProposal = async (proposalId: string, action: 'confirm' | 'reject') => { if (!selected || actionBusy) return; setActionBusy(`proposal:${proposalId}:${action}`); try { if (action === 'confirm') await confirmReportProposal(runId, selected.report_id, proposalId); else await rejectReportProposal(runId, selected.report_id, proposalId); await refreshProposals(); } catch (reason) { setError(reason instanceof Error ? reason.message : 'Proposal 更新失败'); } finally { setActionBusy(null); } };
  const submitReview = async (decision: string) => { if (!selected || !readable || actionBusy) return; setActionBusy(`review:${decision}`); try { await recordReportReview(runId, selected.report_id, decision, reviewComment); setReviewComment(''); await load(); setSelectedRevision(value => value + 1); } catch (reason) { setError(reason instanceof Error ? reason.message : '审阅提交失败'); } finally { setActionBusy(null); } };
  return <main className="report-workspace" aria-busy={loading || Boolean(selectedId && !visibleState)}>
    <header className="report-toolbar">
      <div className="report-heading"><h1>实验报告</h1><p>固定版本、证据与交付制品</p></div>
      <div className="report-toolbar-actions"><span className="report-toolbar-state" role="status" aria-live="polite">{loading ? '同步中' : selectedId && !visibleState ? '读取版本中' : '已同步'}</span><AppButton title="刷新报告" aria-label="刷新报告" onClick={() => void load()} disabled={loading}><RefreshCw size={15} aria-hidden="true" />刷新</AppButton><AppButton onClick={onBack}>返回对话</AppButton></div>
    </header>
    {latest && selected && latest.version > selected.version && <div className="report-version-notice">较新版本 v{latest.version} 正在{reportGenerationStatusLabel(latest.generation_status)}，当前继续显示已选可读版本。</div>}
    {error && <div role="alert" style={{ color: 'var(--orange)', marginBottom: 12 }}>{error}</div>}
    {!selected && !loading && <EmptyState title="当前 run 尚未生成报告。" detail="报告生成后，版本、证据和交付制品会在这里显示。" />}
    {selected && <><section className="report-status-band">
      <div className="report-version-control"><span>版本</span><select aria-label="报告版本" value={selectedId ?? ''} onChange={event => setSelectedId(event.target.value)}>{reports.map(item => <option key={item.report_id} value={item.report_id}>v{item.version} · {reportGenerationStatusLabel(item.generation_status)}</option>)}</select></div>
      <div className="report-status-list" aria-label="报告状态"><StatusBadge tone={reportTone(visibleState?.generation_status ?? selected.generation_status)}>生成：{reportGenerationStatusLabel(visibleState?.generation_status ?? selected.generation_status)}</StatusBadge><StatusBadge tone={reportTone(visibleState?.review_status ?? selected.review_status)}>审阅：{reportReviewStatusLabel(visibleState?.review_status ?? selected.review_status)}</StatusBadge>{Object.entries(visibleState?.format_status ?? selected.format_status).map(([name, value]) => <StatusBadge key={name} tone={reportTone(value)}>{reportFormatLabel(name)}：{reportFormatStatusLabel(value)}</StatusBadge>)}</div>
      <div className="report-delivery-actions" aria-label="报告交付制品">{artifacts.includes('report.html') && <a className="report-delivery-link" title="在新窗口打开 HTML" aria-label="在新窗口打开 HTML" href={artifactUrl(runId, selected.report_id, 'report.html')} target="_blank" rel="noreferrer"><ExternalLink size={16} aria-hidden="true" /></a>}{['report.pdf', 'report_bundle.zip'].filter(item => artifacts.includes(item)).map(item => <a className="report-delivery-link" key={item} title={`下载 ${item}`} aria-label={`下载 ${item}`} href={artifactUrl(runId, selected.report_id, item)}><Download size={16} aria-hidden="true" /></a>)}</div>
    </section>
    {visibleState?.jobs.length ? <section className="report-job-list">
      {visibleState.jobs.map(job => <div key={job.job_id}>{reportJobTypeLabel(job.job_type)}：{reportJobStatusLabel(job.status)}{job.blocked_reason ? `（下一步：${job.blocked_reason}）` : ''}</div>)}
    </section> : null}
    <div className="report-layout">
      <section className="report-paper" data-loading={!visibleState} data-state={visibleState?.generation_status ?? selected.generation_status} aria-live="polite">{!visibleState ? <div className="report-loading-state" role="status"><RefreshCw className="report-loading-icon" size={18} aria-hidden="true" />正在读取固定版本…</div> : visibleContent ? <MarkdownContent>{visibleContent}</MarkdownContent> : <div className="report-empty-content">{visibleState.last_error ? `生成失败：${visibleState.last_error}` : '此版本尚无可读 Markdown。'}</div>}</section>
      <aside className="report-inspector">
        <section className="report-section"><h2>摘要</h2>{visibleDigest ? <div className="report-digest"><div>工程：{visibleDigest.engineering_status ? reportEngineeringStatusLabel(visibleDigest.engineering_status) : '未记录'}</div><div>执行：{visibleDigest.execution_status ? reportExecutionStatusLabel(visibleDigest.execution_status) : '未记录'}</div><div>科学：{visibleDigest.scientific_status ? reportScientificStatusLabel(visibleDigest.scientific_status) : '未记录'}</div><div>Champion：{String(visibleDigest.champion.current_by_contract ? '已记录' : visibleDigest.champion.status ? reportChampionStatusLabel(String(visibleDigest.champion.status)) : '未记录')}</div><div>停止：{String(visibleDigest.stop_decision.reason ?? '未记录')}</div><div>实验轮次：{visibleDigest.attempt_count}，失败：{visibleDigest.failed_attempt_count}，不可比：{visibleDigest.non_comparable_attempt_count}</div>{visibleDigest.primary_metrics.map(item => <div key={`${item.attempt_id}.${item.metric}`}>{item.attempt_id} · {item.metric}: {String(item.value)}</div>)}{visibleDigest.uncertainties.map(item => <div key={item} className="report-uncertainty">{item}</div>)}</div> : <div className="report-muted">{visibleState ? '摘要尚不可用。' : '等待版本状态返回。'}</div>}</section>
        <section className="report-section report-review"><div className="report-section-heading"><h2>审阅与后续</h2><div className="report-section-state">{actionBusy && <span className="report-action-state" role="status"><RefreshCw className="report-loading-icon" size={13} aria-hidden="true" />提交中</span>}{!readable && <StatusBadge tone="warning">{visibleState?.generation_status ? `当前状态：${reportGenerationStatusLabel(visibleState.generation_status)}` : '等待可读内容'}</StatusBadge>}</div></div>{!readable && <p className="report-unavailable-note">当前版本尚未形成可读报告，审阅、人工跟进和讨论会在内容可用后开放。</p>}<textarea aria-label="审阅说明" value={reviewComment} onChange={event => setReviewComment(event.target.value)} placeholder="可选审阅说明" disabled={!readable || actionBusy !== null} /><div className="report-action-row"><AppButton variant="primary" onClick={() => void submitReview('accept')} disabled={!readable || actionBusy !== null} aria-busy={actionBusy === 'review:accept'}>接受</AppButton><AppButton onClick={() => void submitReview('needs_more')} disabled={!readable || actionBusy !== null} aria-busy={actionBusy === 'review:needs_more'}>需要更多证据</AppButton></div><input aria-label="人工跟进 Proposal" value={proposalRationale} onChange={event => setProposalRationale(event.target.value)} placeholder="需要人工跟进的事项" disabled={!readable || actionBusy !== null} /><AppButton onClick={() => void proposeHuman()} disabled={!readable || actionBusy !== null || !proposalRationale.trim()} aria-busy={actionBusy === 'proposal:create'}>创建人工 Proposal</AppButton></section>
        {visibleProposals.length > 0 && <section className="report-section report-proposals"><h2>人工跟进</h2>{visibleProposals.map(item => <div key={item.proposal_id} className="report-proposal"><div>{reportProposalTypeLabel(item.proposal_type)} · {reportProposalStatusLabel(item.status)}</div><div>{item.rationale}</div>{item.validation_errors.map(error => <div key={error} className="report-error-detail">{error}</div>)}{item.status === 'READY_FOR_CONFIRMATION' && <div className="report-action-row"><AppButton onClick={() => void changeProposal(item.proposal_id, 'confirm')} disabled={actionBusy !== null} aria-busy={actionBusy === `proposal:${item.proposal_id}:confirm`}>确认转交</AppButton><AppButton onClick={() => void changeProposal(item.proposal_id, 'reject')} disabled={actionBusy !== null} aria-busy={actionBusy === `proposal:${item.proposal_id}:reject`}>拒绝</AppButton></div>}{item.handoff && <div className="report-muted">已转交：{reportHandoffKindLabel(item.handoff.kind)}</div>}</div>)}</section>}
        <section className="report-section report-evidence"><h2>证据</h2>{visibleEvidence.map(item => <details id={`evidence-${item.evidence_id}`} key={item.evidence_id}><summary>{reportEvidenceKindLabel(item.evidence_kind)} · {item.evidence_id}</summary><div>{item.summary}</div>{item.attempt_id && <div>实验轮次：{item.attempt_id}</div>}{item.idea_id && <div>研究想法：{item.idea_id}</div>}<div className="report-break">SHA：{item.artifact_ref.sha256}</div></details>)}</section>
        <section className="report-section report-discussion"><h2>讨论</h2><div className="report-discussion-list">{visibleDiscussion.map(item => <div key={item.message_id} className={item.role === 'assistant' ? 'report-discussion-assistant' : 'report-discussion-user'}>{item.content}</div>)}</div><div className="report-discussion-input"><input aria-label="报告讨论" value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void sendDiscussion(); }} disabled={!discussionReady} /><button className={sending ? 'is-sending' : ''} aria-label="发送报告讨论" title="发送报告讨论" onClick={() => void sendDiscussion()} disabled={!discussionReady || sending || !question.trim()}><Send className="report-discussion-send-icon" size={16} /></button></div></section>
      </aside>
    </div></>}
  </main>;
}

function reportTone(value: string): 'neutral' | 'success' | 'warning' | 'danger' {
  if (['failed', 'rejected', 'invalid'].includes(value)) return 'danger';
  if (['queued', 'pending', 'running', 'generating', 'unreviewed'].includes(value)) return 'warning';
  if (['ready', 'content_ready', 'accepted', 'completed', 'HANDED_OFF'].includes(value)) return 'success';
  return 'neutral';
}
