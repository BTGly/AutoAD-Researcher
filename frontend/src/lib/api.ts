import type {
  ExperimentTaskConfirmationResult,
  ExperimentTaskDraft,
  ExperimentProjection,
  SourceInstruction,
  ReportDigest,
  ReportEvidence,
  ReportManifest,
  ReportState,
  DiscussionMessage,
  DiscussionTurn,
  ReportProposal,
  TaskRun,
  BaselineContractInput,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function apiError(res: Response, fallback: string): Promise<ApiError> {
  const payload = await res.json().catch(() => null);
  const detail = payload?.detail;
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
    return new ApiError(res.status, detail.message, typeof detail.code === 'string' ? detail.code : null);
  }
  return new ApiError(res.status, typeof detail === 'string' ? detail : fallback);
}

function getHeaders(): Record<string, string> {
  const cfg = localStorage.getItem('autoad_config');
  if (!cfg) return { 'Content-Type': 'application/json' };
  try {
    const c = JSON.parse(cfg);
    return {
      'Content-Type': 'application/json',
      'X-AutoAD-API-Key': c.apiKey || '',
      'X-AutoAD-Base-URL': c.baseUrl || '',
      'X-AutoAD-Model': c.dialogueModel || '',
      'X-AutoAD-Dialogue-Model': c.dialogueModel || '',
      'X-AutoAD-Report-Model': c.reportModel || '',
      'X-AutoAD-Experiment-Model': c.experimentModel || '',
    };
  } catch {
    return { 'Content-Type': 'application/json' };
  }
}

export async function sendChat(
  userInput: string,
  runId: string,
  requestId: string,
  transcriptTail: Array<{ role: string; content: string }> = [],
): Promise<{
  reply: string;
  reply_kind: string;
  source_action: SourceInstruction | null;
  experiment_task: ExperimentTaskDraft | null;
}> {
  const res = await fetch('/api/chat/send', {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({
      user_input: userInput,
      run_id: runId,
      request_id: requestId,
      transcript_tail: transcriptTail,
    }),
  });
  if (!res.ok) throw new Error(`Chat API error: ${res.status}`);
  return res.json();
}

export async function confirmExperimentTask(
  runId: string,
  taskId: string,
  executionMode: ExperimentTaskDraft['execution_mode'],
  executionRepositorySourceId?: string,
): Promise<ExperimentTaskConfirmationResult> {
  const res = await fetch(`/api/runs/${runId}/experiment-task/${taskId}/confirm`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({
      execution_mode: executionMode,
      execution_repository_source_id:
        executionMode === 'plan_only' ? null : executionRepositorySourceId,
    }),
  });
  if (!res.ok) throw await apiError(res, `Experiment task confirmation error: ${res.status}`);
  return res.json();
}

export async function getPendingExperimentTask(runId: string): Promise<ExperimentTaskDraft | null> {
  const res = await fetch(`/api/runs/${runId}/experiment-task/pending`, { headers: getHeaders() });
  if (res.status === 404) return null;
  if (!res.ok) throw await apiError(res, `Pending experiment task error: ${res.status}`);
  return res.json();
}

export async function confirmPrimaryMetrics(
  runId: string,
  primaryMetrics: string[],
): Promise<ExperimentTaskDraft> {
  const res = await fetch(`/api/runs/${runId}/intent-summary/primary-metrics`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify({ primary_metrics: primaryMetrics }),
  });
  if (!res.ok) throw await apiError(res, `Primary metric confirmation error: ${res.status}`);
  return res.json();
}

export async function getRuns(includeArchived = false): Promise<TaskRun[]> {
  const res = await fetch(`/api/runs?include_archived=${includeArchived ? 'true' : 'false'}`);
  if (!res.ok) throw new Error(`Runs API error: ${res.status}`);
  return res.json();
}

export async function createRun(taskTitle?: string): Promise<TaskRun> {
  const res = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_title: taskTitle || null }),
  });
  if (!res.ok) throw new Error(`Create run error: ${res.status}`);
  return res.json();
}

export async function renameRun(runId: string, taskTitle: string): Promise<TaskRun> {
  const res = await fetch(`/api/runs/${runId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_title: taskTitle }),
  });
  if (!res.ok) throw new Error(`Rename run error: ${res.status}`);
  return res.json();
}

export async function archiveRun(runId: string): Promise<TaskRun> {
  const res = await fetch(`/api/runs/${runId}/archive`, { method: 'POST' });
  if (!res.ok) throw new Error(`Archive run error: ${res.status}`);
  return res.json();
}

export async function restoreRun(runId: string): Promise<TaskRun> {
  const res = await fetch(`/api/runs/${runId}/restore`, { method: 'POST' });
  if (!res.ok) throw new Error(`Restore run error: ${res.status}`);
  return res.json();
}

export async function deleteRun(runId: string): Promise<{ run_id: string; deleted: boolean }> {
  const res = await fetch(`/api/runs/${runId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete run error: ${res.status}`);
  return res.json();
}

export async function getTranscript(runId: string): Promise<Array<{ role: string; content: string; created_at: string | null }>> {
  const res = await fetch(`/api/runs/${runId}/transcript`);
  if (!res.ok) return [];
  return res.json();
}

export async function getSources(runId: string): Promise<any[]> {
  const res = await fetch(`/api/runs/${runId}/sources`);
  if (!res.ok) return [];
  return res.json();
}

export async function uploadSource(runId: string, file: File): Promise<any> {
  const res = await fetch(`/api/runs/${runId}/sources/upload`, {
    method: 'POST',
    headers: { 'X-AutoAD-Filename': encodeURIComponent(file.name) },
    body: await file.arrayBuffer(),
  });
  if (!res.ok) throw new Error(`Upload source error: ${res.status}`);
  return res.json();
}

export async function deleteSource(runId: string, sourceId: string): Promise<any> {
  const res = await fetch(`/api/runs/${runId}/sources/${sourceId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete source error: ${res.status}`);
  return res.json();
}

export async function getJobs(runId: string): Promise<any[]> {
  const res = await fetch(`/api/runs/${runId}/jobs`);
  if (!res.ok) return [];
  return res.json();
}

export async function getEvidence(runId: string): Promise<any[]> {
  const res = await fetch(`/api/runs/${runId}/evidence`);
  if (!res.ok) return [];
  return res.json();
}

export async function getEvidenceState(runId: string): Promise<any> {
  const res = await fetch(`/api/runs/${runId}/evidence/state`);
  if (!res.ok) return { usable_evidence: [], unusable_parsed_sources: [] };
  return res.json();
}

export async function getIntentSummary(runId: string): Promise<any> {
  const res = await fetch(`/api/runs/${runId}/intent-summary`);
  if (!res.ok) return { goal: '', confirmed_facts: [], inferred_facts: [], unresolved_conflicts: [], blocking_question: null };
  return res.json();
}

export async function getArtifact(runId: string, path: string): Promise<{ path: string; content: string }> {
  const res = await fetch(`/api/runs/${runId}/artifacts/${path}`);
  if (!res.ok) throw new Error(`Artifact not found: ${path}`);
  return res.json();
}

export async function getExperimentProjection(runId: string, sessionId?: string, signal?: AbortSignal): Promise<ExperimentProjection> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const res = await fetch(`/api/runs/${runId}/experiment/projection${query}`, { headers: getHeaders(), signal });
  if (!res.ok) throw await apiError(res, `Experiment projection error: ${res.status}`);
  return res.json();
}

export async function startBaseline(
  runId: string,
  sessionId: string,
  contract: BaselineContractInput,
): Promise<unknown> {
  const res = await fetch(`/api/runs/${runId}/sessions/${sessionId}/baseline`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ contract }),
  });
  if (!res.ok) throw await apiError(res, `Baseline launch error: ${res.status}`);
  return res.json();
}

export async function confirmCandidate(
  runId: string,
  sessionId: string,
  candidateAttemptId: string,
  noiseThreshold: number,
): Promise<unknown> {
  const res = await fetch(`/api/runs/${runId}/sessions/${sessionId}/candidate-confirmations`, {
    method: 'POST', headers: getHeaders(),
    body: JSON.stringify({ candidate_attempt_id: candidateAttemptId, noise_threshold: noiseThreshold, idempotency_key: `ui-confirm:${candidateAttemptId}` }),
  });
  if (!res.ok) throw await apiError(res, `Candidate confirmation error: ${res.status}`);
  return res.json();
}

export async function promoteCandidate(runId: string, candidateId: string, approvedBy: string): Promise<unknown> {
  const res = await fetch(`/api/runs/${runId}/promotions`, {
    method: 'POST', headers: getHeaders(), body: JSON.stringify({ candidate_id: candidateId, approved_by: approvedBy }),
  });
  if (!res.ok) throw await apiError(res, `Champion promotion error: ${res.status}`);
  return res.json();
}

export async function getReport(runId: string): Promise<{ content: string }> {
  const res = await fetch(`/api/runs/${runId}/report`);
  if (!res.ok) throw new Error(`Report not found: ${res.status}`);
  return res.json();
}

export function wsUrl(runId: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/api/runs/${runId}/ws`;
}

export async function getLatestVersionedReport(runId: string): Promise<{ content: string; reportId?: string }> {
  const latest = await fetch(`/api/runs/${runId}/reports/latest-content-ready`);
  if (latest.status === 404) return getReport(runId);
  if (!latest.ok) throw new Error(`Latest report error: ${latest.status}`);
  const manifest = await latest.json();
  const content = await fetch(`/api/runs/${runId}/reports/${manifest.report_id}/content?format=md`);
  if (!content.ok) throw new Error(`Report content error: ${content.status}`);
  const payload = await content.json();
  return { content: payload.content || "", reportId: manifest.report_id };
}

export async function listReports(runId: string): Promise<ReportManifest[]> { const res = await fetch(`/api/runs/${runId}/reports`); if (!res.ok) throw await apiError(res, 'Report list unavailable'); return (await res.json()).reports; }
export async function getLatestCreatedReport(runId: string): Promise<ReportManifest | null> { const res = await fetch(`/api/runs/${runId}/reports/latest-created`); if (res.status === 404) return null; if (!res.ok) throw await apiError(res, 'Latest report unavailable'); return res.json(); }
export async function getLatestContentReadyReport(runId: string): Promise<ReportManifest | null> { const res = await fetch(`/api/runs/${runId}/reports/latest-content-ready`); if (res.status === 404) return null; if (!res.ok) throw await apiError(res, 'Readable report unavailable'); return res.json(); }
export async function getReportState(runId: string, reportId: string): Promise<ReportState> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/state`); if (!res.ok) throw await apiError(res, 'Report state unavailable'); return res.json(); }
export async function getReportDigest(runId: string, reportId: string): Promise<ReportDigest | null> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/digest`); if (res.status === 409) return null; if (!res.ok) throw await apiError(res, 'Report digest unavailable'); return res.json(); }
export async function getReportContent(runId: string, reportId: string): Promise<string | null> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/content?format=md`); if (res.status === 409) return null; if (!res.ok) throw await apiError(res, 'Report content unavailable'); return (await res.json()).content || null; }
export async function listReportEvidence(runId: string, reportId: string): Promise<ReportEvidence[]> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/evidence`); if (res.status === 409) return []; if (!res.ok) throw await apiError(res, 'Report evidence unavailable'); return (await res.json()).entries; }
export async function getReportDiscussion(runId: string, reportId: string): Promise<{ messages: DiscussionMessage[]; turns: DiscussionTurn[] }> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/discussion`); if (!res.ok) throw await apiError(res, 'Discussion unavailable'); return res.json(); }
export async function sendReportDiscussion(runId: string, reportId: string, requestId: string, content: string): Promise<DiscussionTurn> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/discussion`, { method: 'POST', headers: getHeaders(), body: JSON.stringify({ request_id: requestId, content }) }); if (!res.ok) throw await apiError(res, 'Discussion request failed'); return res.json(); }
export async function listReportProposals(runId: string, reportId: string): Promise<ReportProposal[]> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/proposals`, { headers: getHeaders() }); if (res.status === 404) return []; if (!res.ok) throw await apiError(res, 'Proposal list unavailable'); const payload = await res.json(); return Array.isArray(payload?.proposals) ? payload.proposals : []; }
export async function createHumanProposal(runId: string, reportId: string, rationale: string): Promise<ReportProposal> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/proposals`, { method: 'POST', headers: getHeaders(), body: JSON.stringify({ proposal_type: 'REQUEST_HUMAN', rationale }) }); if (!res.ok) throw await apiError(res, 'Proposal creation failed'); return res.json(); }
export async function confirmReportProposal(runId: string, reportId: string, proposalId: string): Promise<ReportProposal> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/proposals/${proposalId}/confirm`, { method: 'POST', headers: getHeaders() }); if (!res.ok) throw await apiError(res, 'Proposal confirmation failed'); return res.json(); }
export async function rejectReportProposal(runId: string, reportId: string, proposalId: string): Promise<ReportProposal> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/proposals/${proposalId}/reject`, { method: 'POST', headers: getHeaders() }); if (!res.ok) throw await apiError(res, 'Proposal rejection failed'); return res.json(); }
export async function recordReportReview(runId: string, reportId: string, decision: string, userComment: string): Promise<unknown> { const res = await fetch(`/api/runs/${runId}/reports/${reportId}/review-decision`, { method: 'POST', headers: getHeaders(), body: JSON.stringify({ request_id: `review.${reportId}.${crypto.randomUUID()}`, decision, user_comment: userComment }) }); if (!res.ok) throw await apiError(res, 'Review submission failed'); return res.json(); }
