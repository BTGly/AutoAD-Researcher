import type {
  ExperimentTaskConfirmationResult,
  ExperimentTaskDraft,
  ExperimentProjection,
  SourceInstruction,
  TaskRun,
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
    const exp = c.experiment || {};
    return {
      'Content-Type': 'application/json',
      'X-AutoAD-API-Key': c.apiKey || '',
      'X-AutoAD-Base-URL': c.baseUrl || '',
      'X-AutoAD-Model': c.model || '',
      'X-AutoAD-Exp-Provider': exp.provider || '',
      'X-AutoAD-Exp-Model': exp.model || '',
      'X-AutoAD-Exp-Api-Key': exp.apiKey || '',
      'X-AutoAD-Exp-Base-URL': exp.baseUrl || '',
      'X-AutoAD-Exp-Reasoning': exp.reasoningEffort || '',
      'X-AutoAD-Exp-Max-Cycles': exp.maxCycles ? String(exp.maxCycles) : '',
      'X-AutoAD-Exp-Max-Turns': exp.maxTurns ? String(exp.maxTurns) : '',
      'X-AutoAD-Exp-Timeout': exp.executorTimeout ? String(exp.executorTimeout) : '',
      'X-AutoAD-Exp-Search': exp.searchEnabled ? '1' : '0',
      'X-AutoAD-Exp-Auto-Search': exp.autoSearch ? '1' : '0',
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

export async function getExperimentConfig(runId: string): Promise<any> {
  const res = await fetch(`/api/runs/${runId}/experiment-config`);
  if (!res.ok) return {};
  return res.json();
}

export async function getExperimentProjection(runId: string, sessionId?: string, signal?: AbortSignal): Promise<ExperimentProjection> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const res = await fetch(`/api/runs/${runId}/experiment/projection${query}`, { headers: getHeaders(), signal });
  if (!res.ok) throw await apiError(res, `Experiment projection error: ${res.status}`);
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

export async function saveExperimentConfig(runId: string, config: any): Promise<any> {
  const res = await fetch(`/api/runs/${runId}/experiment-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error(`Save experiment config error: ${res.status}`);
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
