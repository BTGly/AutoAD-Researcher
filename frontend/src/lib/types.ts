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
  intakeStatus: string | null;
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

export interface SourceInstruction {
  action: 'request_source_removal';
  source_id: string;
  label_hint: string;
  reason: string;
}

export interface PipelineInputTask {
  run_id: string;
  request: string;
  source_ids: string[];
  target_domain: string | null;
  user_idea: string | null;
  baseline: string | null;
  dataset: string | null;
  compute_budget: string | null;
  primary_metrics: string[];
  constraints: string[];
}

export interface ExperimentTaskDraft {
  schema_version: 1;
  task_id: string;
  run_id: string;
  status: 'pending_confirmation' | 'confirmed';
  execution_mode: 'plan_only' | 'approve_each_step' | 'agent_assisted_after_approval';
  input_task: PipelineInputTask;
  evidence_refs: string[];
  summary_sha256: string;
  created_at: string;
  confirmed_at: string | null;
}

export interface ExperimentTaskConfirmationResult {
  task: ExperimentTaskDraft;
  session_id: string | null;
  session_status: string | null;
  environment_job_id: string | null;
  disposition: 'plan_only' | 'created' | 'repaired' | 'reused';
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

export interface ExperimentProjectionSession {
  session_id: string;
  task_ref: string;
  task_hash: string;
  status: string;
  execution_mode: string;
  readiness_status: string;
  readiness_blockers: string[];
  environment_status: string;
  baseline_status: string;
  budget: Record<string, unknown>;
}

export interface ExperimentIdeaNode {
  node_id: string;
  parent_id: string | null;
  is_root: boolean;
  depth: number;
  mechanism: string | null;
  hypothesis: string | null;
  observable: string | null;
  research_axis: string | null;
  minimal_intervention: string | null;
  falsification: string | null;
  relationship_to_previous_ideas: string | null;
  grounding: string[];
  expected_cost: string;
  status: string;
  attempt_refs: string[];
  evidence_refs: string[];
  cognitive_commit_refs: string[];
  insights: Array<Record<string, unknown>>;
  children: string[];
  attempt_summary: Record<string, number>;
}

export interface ExperimentAttempt {
  attempt_id: string;
  attempt_purpose: string;
  runtime_status: string;
  job_type: string;
  pipeline_job_id: string | null;
  required_device_count: number;
  required_vram_mb: number;
  retry_of: string | null;
  retry_count: number;
  max_retries: number;
  retry_exhausted: boolean;
  failure_code: string | null;
  command_plan_summary: string;
  execution_outcome: Record<string, unknown> | null;
  scientific_assessment: Record<string, unknown> | null;
  assessment_reconciliation: Record<string, unknown> | null;
  scientific_assessment_status: 'available' | 'not_materialized' | 'invalid';
  related_idea_ids: string[];
  pid: number | null;
  heartbeat_at: string | null;
  resource_lease_id: string | null;
}

export interface ExperimentActivity {
  event_id: number;
  event_type: string;
  created_at: string;
  title: string;
  summary: string;
  card_kind: string;
  related_idea_id: string | null;
  related_attempt_id: string | null;
  related_commit_id: string | null;
  related_outcome: Record<string, unknown> | null;
  detail: string;
  evidence_refs: string[];
}

export interface ExperimentProjection {
  schema_version: 1;
  selection_status: 'no_session' | 'selected' | 'ambiguous';
  session: ExperimentProjectionSession | null;
  session_candidates: Array<{ session_id: string; task_hash: string; status: string; created_at: string }>;
  input_task: PipelineInputTask | null;
  summary: {
    idea_count: number;
    idea_rooted_count: number;
    attempt_by_status: Record<string, number>;
    budget: Record<string, unknown>;
    budget_consumed: Record<string, unknown> | null;
    champion_status: string;
  } | null;
  idea_tree: { session_id: string; revision: number; root_node_id: string; nodes: ExperimentIdeaNode[] } | null;
  attempts: ExperimentAttempt[];
  champion_status: 'absent' | 'available' | 'assessment_missing' | 'assessment_invalid';
  champion: { candidate_id: string; idea_id: string; attempt_id: string; assessment_error: string | null } | null;
  activity: ExperimentActivity[];
  activity_limit: number;
  activity_truncated: boolean;
  developer_refs: {
    run_id: string; session_id: string; event_ids: number[]; artifact_paths: string[]; pipeline_job_ids: string[]; event_log_path: string;
  } | null;
}

export type TabId = 'sources' | 'jobs' | 'evidence' | 'summary';

export type PageId = 'chat' | 'experiment' | 'report';
