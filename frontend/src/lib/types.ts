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

export interface JobItem {
  jobId: string;
  jobType: string;
  status: string;
  sourceLabel?: string;
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
  message?: string;
  content?: string;
  kind?: string;
  status?: string;
  duration?: string;
  jobId?: string;
  jobType?: string;
  sourceId?: string;
  sourceLabel?: string;
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

export type TabId = 'sources' | 'jobs' | 'evidence' | 'draft';

export type PageId = 'chat' | 'settings' | 'report';
