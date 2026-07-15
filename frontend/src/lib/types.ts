export interface ToolLine {
  id: string;
  text: string;
  status: 'running' | 'done' | 'error' | 'info';
  duration?: string;
  kind?: 'parse' | 'clone' | 'fetch' | 'search' | 'read';
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  toolLines?: ToolLine[];
  timestamp: number;
}

export interface QueuedChatMessage {
  id: string;
  runId: string;
  content: string;
  createdAt: number;
  status: 'queued';
}

export interface ToastItem {
  id: string;
  message: string;
  kind: 'success' | 'error' | 'info';
}

export interface SourceItem {
  sourceId: string;
  kind: string;
  label: string;
  status: string;
  registrationStatus?: string;
  acquisitionStatus?: string;
  parseStatus?: string;
  analysisStatus?: string;
  evidenceStatus?: string;
}

export interface DraftField {
  field: string;
  label: string;
  value: string;
  status: 'known' | 'missing' | string;
}

export interface DraftState {
  ready: boolean;
  has_draft: boolean;
  title: string;
  fields: DraftField[];
  missing: Array<{ field: string; label: string }>;
  sources: Array<{ source_id: string; label: string; kind: string; status: string }>;
  evidence: Array<{ source_id: string; type: string; artifact_path: string; summary: string }>;
  jobs: Array<{ job_id: string; source_id: string; job_type: string; status: string; error?: string }>;
  next_questions: string[];
  advisory_enrichment?: DraftField[];
  confirmation?: ContractConfirmationState | null;
}

export interface ContractConfirmationState {
  confirmation_id: string;
  draft_hash: string;
  status: 'pending' | 'needs_clarification';
  requested_at: string | null;
  fields: DraftField[];
}

export interface ExperimentControlState {
  session: null | {
    session_id: string;
    prepare_job_id: string;
    status: 'queued' | 'preparing' | 'materialized' | 'failed';
    error?: string;
  };
  readiness: null | {
    revision: number;
    planning_readiness: { ready: boolean; blocking_reasons: string[] };
    implementation_readiness: { ready: boolean; blocking_reasons: string[] };
    execution_readiness: { ready: boolean; blocking_reasons: string[] };
    execution_authorization: { authorized: boolean; reason: string };
  };
  job: null | {
    job_id: string;
    status: string;
    attempt_count: number;
    error?: string;
  };
  requests: Array<{
    request_id: string;
    status: string;
    reason: string;
  }>;
}

export interface JobItem {
  jobId: string;
  jobType: string;
  status: string;
  sourceLabel?: string;
  error?: string;
}

export interface EvidenceItem {
  sourceId: string;
  artifactPath: string;
  evidenceType: string;
  supportLevel: string;
  parserName?: string;
  summary: string;
  raw?: Record<string, any>;
}

export interface UnusableParsedSource {
  sourceId: string;
  label: string;
  status: string;
  parseAttemptId: string;
  parser: string;
  warnings: string[];
  fatalErrors?: string[];
  parserErrors?: Array<{ parser_name?: string; parserName?: string; error?: string }>;
}

export interface TaskRun {
  run_id: string;
  created_at: string | null;
  updated_at: string | null;
  sources_count: number;
  task_title: string;
  task_summary: string;
  task_source: string;
  task_profile_warning: string | null;
  archived_at: string | null;
}

export interface WSMessage {
  type: string;
  messageId?: string;
  message_id?: string;
  message?: string;
  content?: string;
  kind?: string;
  status?: string;
  duration?: string;
  jobId?: string;
  jobType?: string;
  job_id?: string;
  job_type?: string;
  error?: string;
  sourceId?: string;
  source_id?: string;
  sourceLabel?: string;
  stored_path?: string;
  storedPath?: string;
  paths?: string[];
  toast?: boolean;
  delay?: number;
  confirmation_id?: string;
  decision?: 'approved' | 'rejected';
  run_id?: string;
  task_title?: string;
  task_summary?: string;
  task_source?: string;
  updated_at?: string | null;
}

export interface ExperimentConfig {
  provider: string;
  model: string;
  apiKey: string;
  baseUrl: string;
  reasoningEffort: string;
  maxCycles: number;
  maxTurns: number;
  executorTimeout: number;
  searchEnabled: boolean;
  autoSearch: boolean;
}

export type TabId = 'sources' | 'jobs' | 'evidence' | 'draft';

export type PageId = 'chat' | 'settings' | 'report';
