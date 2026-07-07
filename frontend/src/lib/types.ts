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

export interface WSMessage {
  type: string;
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

export type TabId = 'sources' | 'jobs' | 'evidence' | 'draft';
