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
}

export interface BasedStatement {
  statement: string;
  basis: string;
}

export interface IntentSummary {
  goal: string;
  confirmed_facts: string[];
  inferred_facts: BasedStatement[];
  unresolved_conflicts: BasedStatement[];
  blocking_question: string | null;
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

export type TabId = 'sources' | 'jobs' | 'evidence' | 'summary';

export type PageId = 'chat' | 'settings' | 'report';
