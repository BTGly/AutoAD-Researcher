import { useState } from 'react';
import type { SourceItem, JobItem, EvidenceItem, UnusableParsedSource, TabId, DraftField, DraftState, ExperimentControlState } from '../lib/types';

interface Props {
  sources: SourceItem[];
  jobs: JobItem[];
  evidence: EvidenceItem[];
  unusableParsedSources: UnusableParsedSource[];
  evidenceCount: number;
  draftReady: boolean;
  draft?: DraftState | null;
  experimentControl?: ExperimentControlState | null;
  experimentBusy?: boolean;
  onMaterialize?: () => void;
  onRetryMaterialization?: () => void;
  onDeleteSource?: (sourceId: string) => void;
  children?: React.ReactNode;
}

interface DisplayMeta {
  label: string;
  tone: 'good' | 'warn' | 'bad' | 'info' | 'muted';
}

const CORE_DRAFT_FIELDS = new Set(['research_goal', 'baseline', 'dataset', 'primary_metrics', 'success_criteria']);
const METHOD_DRAFT_FIELDS = new Set(['preferred_method_hints', 'user_improvement_hints', 'target_module', 'improvement_idea']);

export function Sidebar({ sources, jobs, evidence, unusableParsedSources, evidenceCount, draftReady, draft, experimentControl, experimentBusy, onMaterialize, onRetryMaterialization, onDeleteSource, children }: Props) {
  const [tab, setTab] = useState<TabId>('sources');

  const tabs: { id: TabId; label: string; count: number }[] = [
    { id: 'sources', label: '资料', count: sources.length },
    { id: 'jobs', label: '任务', count: jobs.length },
    { id: 'evidence', label: '证据', count: evidenceCount + unusableParsedSources.length },
    { id: 'draft', label: '草案', count: draftReady ? 1 : 0 },
  ];

  return (
    <div className="right-sidebar">
      <div className="sidebar-tabs">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`sidebar-tab ${tab === t.id ? 'active' : ''}`}
          >
            <span>{t.label}</span>
            {t.count > 0 && <span className="sidebar-tab-count">{t.count}</span>}
          </button>
        ))}
      </div>
      <div className="sidebar-content">
        {tab === 'sources' && <SourcesList sources={sources} onDeleteSource={onDeleteSource} />}
        {tab === 'jobs' && <JobsList jobs={jobs} />}
        {tab === 'evidence' && <EvidenceList evidence={evidence} unusableParsedSources={unusableParsedSources} />}
        {tab === 'draft' && (
          <DraftPanel
            draft={draft || null}
            experimentControl={experimentControl || null}
            experimentBusy={Boolean(experimentBusy)}
            onMaterialize={onMaterialize}
            onRetryMaterialization={onRetryMaterialization}
          />
        )}
      </div>
      {children}
    </div>
  );
}

function SourcesList({ sources, onDeleteSource }: { sources: SourceItem[]; onDeleteSource?: (sourceId: string) => void }) {
  if (!sources.length) return <EmptyState title="暂无资料" detail="上传论文、粘贴链接或登记仓库后会出现在这里。" />;
  return (
    <div className="sidebar-stack">
      <SectionSummary title="资料来源" detail={`${sources.length} 个已登记资料，解析或采集进度见下方状态。`} />
      {sources.map(source => {
        const kind = sourceKindMeta(source.kind);
        const status = sourceLifecycleMeta(source);
        return (
          <div key={source.sourceId} className="sidebar-card">
            <div className="sidebar-card-head">
              <div className="sidebar-title">{source.label}</div>
              {onDeleteSource && (
                <button
                  onClick={() => onDeleteSource(source.sourceId)}
                  title="删除资料"
                  className="sidebar-icon-button danger"
                >
                  ×
                </button>
              )}
            </div>
            <div className="sidebar-badges">
              <Badge meta={kind} />
              <Badge meta={status} />
            </div>
            <div className="sidebar-muted">来源 ID：{source.sourceId}</div>
          </div>
        );
      })}
    </div>
  );
}

function JobsList({ jobs }: { jobs: JobItem[] }) {
  if (!jobs.length) return <EmptyState title="暂无后台任务" detail="解析论文、克隆仓库、搜索资料等动作会显示在这里。" />;
  const latestFirst = [...jobs].reverse();
  const running = jobs.filter(job => job.status === 'running' || job.status === 'queued').length;
  const failed = jobs.filter(job => job.status === 'failed').length;
  return (
    <div className="sidebar-stack">
      <SectionSummary
        title="后台任务"
        detail={`共 ${jobs.length} 个；${running ? `${running} 个进行中；` : ''}${failed ? `${failed} 个失败。` : '当前没有失败任务。'}`}
      />
      {latestFirst.map(job => {
        const type = jobTypeMeta(job.jobType);
        const status = jobStatusMeta(job.status);
        return (
          <div key={job.jobId} className="sidebar-card">
            <div className="sidebar-card-head">
              <div>
                <div className="sidebar-title">{type.label}</div>
                {job.sourceLabel && <div className="sidebar-muted">{job.sourceLabel}</div>}
              </div>
              <Badge meta={status} />
            </div>
            {job.error && (
              <div className={`sidebar-note ${status.tone === 'bad' ? 'bad' : ''}`}>
                {humanJobError(job.error)}
              </div>
            )}
            <div className="sidebar-muted">任务 ID：{job.jobId}</div>
            {job.error && <div className="sidebar-muted clamp">原始错误：{job.error}</div>}
          </div>
        );
      })}
    </div>
  );
}

function EvidenceList({ evidence, unusableParsedSources }: { evidence: EvidenceItem[]; unusableParsedSources: UnusableParsedSource[] }) {
  if (!evidence.length && !unusableParsedSources.length) {
    return <EmptyState title="暂无可用证据" detail="资料解析完成后，可用于回答和规划的证据会显示在这里。" />;
  }
  return (
    <div className="sidebar-stack">
      <SectionSummary
        title="证据状态"
        detail={`${evidence.length} 条可用证据${unusableParsedSources.length ? `，${unusableParsedSources.length} 个资料解析后不可用。` : '。'}`}
      />
      {unusableParsedSources.map(item => (
        <div key={`${item.sourceId}-${item.parseAttemptId}`} className="sidebar-card warning">
          <div className="sidebar-card-head">
            <div className="sidebar-title">{item.label}</div>
            <Badge meta={{ label: '不可用', tone: 'bad' }} />
          </div>
          <div className="sidebar-note bad">PDF 已处理，但没有产出可读正文证据。</div>
          <div className="sidebar-muted">{parserLabel(item.parser)} · {item.parseAttemptId || item.sourceId}</div>
          {item.warnings.length > 0 && (
            <div className="sidebar-muted clamp">{item.warnings.slice(0, 2).join(' · ')}</div>
          )}
          {knownParseReasons(item).length > 0 && (
            <div className="sidebar-note bad">{knownParseReasons(item).slice(0, 3).join(' · ')}</div>
          )}
        </div>
      ))}
      {evidence.map((item, index) => {
        const type = evidenceTypeMeta(item.evidenceType);
        const support = supportMeta(item.supportLevel);
        const detail = detailPath(item);
        const preview = evidencePreview(item);
        return (
          <div key={`${item.artifactPath}-${index}`} className="sidebar-card">
            <div className="sidebar-card-head">
              <div className="sidebar-title">{type.label}</div>
              <Badge meta={support} />
            </div>
            {preview && (
              <div className={`sidebar-body evidence-preview ${preview.tone || ''}`}>
                {preview.text}
              </div>
            )}
            <div className="sidebar-muted">{parserLabel(item.parserName)} · {item.artifactPath}</div>
            {detail && <div className="sidebar-muted">详情：{detail}</div>}
          </div>
        );
      })}
    </div>
  );
}

function DraftPanel({
  draft,
  experimentControl,
  experimentBusy,
  onMaterialize,
  onRetryMaterialization,
}: {
  draft: DraftState | null;
  experimentControl: ExperimentControlState | null;
  experimentBusy: boolean;
  onMaterialize?: () => void;
  onRetryMaterialization?: () => void;
}) {
  if (!draft || !draft.has_draft) {
    return <EmptyState title="暂无研究计划草案" detail="当对话里出现基线、数据集、指标或资料线索后，草案会自动整理。" />;
  }
  const coreFields = draft.fields.filter(field => CORE_DRAFT_FIELDS.has(field.field));
  const methodFields = draft.fields.filter(field => METHOD_DRAFT_FIELDS.has(field.field));
  const otherFields = draft.fields.filter(field => !CORE_DRAFT_FIELDS.has(field.field) && !METHOD_DRAFT_FIELDS.has(field.field));
  const confirmationNeedsClarification = draft.confirmation?.status === 'needs_clarification';
  const jobCounts = {
    pending: draft.jobs.filter(job => job.status === 'queued' || job.status === 'running').length,
    failed: draft.jobs.filter(job => job.status === 'failed').length,
    completed: draft.jobs.filter(job => job.status === 'completed').length,
  };
  return (
    <div className="sidebar-stack">
      <div className="sidebar-card">
        <div className="sidebar-card-head">
          <div className="sidebar-title">{draft.title || '研究计划草案'}</div>
          <Badge meta={confirmationNeedsClarification
            ? { label: '需要澄清', tone: 'warn' }
            : { label: draft.ready ? '可生成计划' : '还需补充', tone: draft.ready ? 'good' : 'warn' }} />
        </div>
        <div className="sidebar-body">
          {confirmationNeedsClarification
            ? '当前确认已暂挂，需要先澄清；旧草案和确认内容仍然保留。'
            : draft.ready ? '核心研究信息已经齐，可以进入计划生成。' : '仍有缺口，补齐后再生成计划更稳。'}
        </div>
      </div>

      <DraftSection title="核心信息" fields={coreFields} />
      <DraftSection title="方法线索" fields={methodFields} />
      <DraftSection title="执行与来源" fields={otherFields} />
      <DraftSection
        title="未进入本次授权的推断/建议"
        fields={draft.advisory_enrichment || []}
      />

      {(experimentControl?.session || onMaterialize) && (
        <div className="sidebar-card">
          <div className="sidebar-card-head">
            <div className="sidebar-title">实验准备控制面</div>
            {experimentControl?.session && (
              <Badge meta={jobStatusMeta(experimentControl.job?.status || experimentControl.session.status)} />
            )}
          </div>
          {experimentControl?.session && (
            <>
              <div className="sidebar-muted">Session：{experimentControl.session.session_id}</div>
              <div className="sidebar-muted">
                planning：{experimentControl.readiness?.planning_readiness.ready ? 'ready' : 'blocked'} · implementation：{experimentControl.readiness?.implementation_readiness.ready ? 'ready' : 'blocked'} · execution：{experimentControl.readiness?.execution_readiness.ready ? 'ready' : 'blocked'}
              </div>
            </>
          )}
          <div className="sidebar-muted">只重新读取合同和已登记事实，不修改代码、不运行实验。</div>
          {experimentControl?.job?.status === 'failed' && onRetryMaterialization ? (
            <button onClick={onRetryMaterialization} disabled={experimentBusy}>
              {experimentBusy ? '处理中...' : '重试准备检查'}
            </button>
          ) : onMaterialize && (
            <button onClick={onMaterialize} disabled={experimentBusy}>
              {experimentBusy
                ? '处理中...'
                : experimentControl?.session ? '重新检查实验准备状态' : '检查实验准备状态'}
            </button>
          )}
        </div>
      )}

      {draft.missing.length > 0 && (
        <div className="sidebar-card warning">
          <div className="sidebar-title">还缺这些</div>
          <div className="sidebar-body">{draft.missing.map(item => item.label).join('、')}</div>
        </div>
      )}

      {draft.next_questions.length > 0 && (
        <div className="sidebar-card">
          <div className="sidebar-title">下一步可问</div>
          {draft.next_questions.map((question, index) => (
            <div key={`${question}-${index}`} className="sidebar-body spaced">
              {question}
            </div>
          ))}
        </div>
      )}

      <div className="sidebar-muted">
        草案来源：{draft.sources.length} 个资料，{draft.evidence.length} 条证据；关联任务共 {draft.jobs.length} 个（待处理 {jobCounts.pending}、失败 {jobCounts.failed}、已完成 {jobCounts.completed}）。
      </div>
    </div>
  );
}

function DraftSection({ title, fields }: { title: string; fields: DraftField[] }) {
  if (!fields.length) return null;
  return (
    <div className="sidebar-section">
      <div className="sidebar-section-title">{title}</div>
      {fields.map(field => (
        <div key={field.field} className="draft-field-row">
          <div className="draft-field-label">{field.label}</div>
          <div className="draft-field-value">{field.value}</div>
        </div>
      ))}
    </div>
  );
}

function SectionSummary({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="sidebar-summary">
      <div className="sidebar-section-title">{title}</div>
      <div className="sidebar-muted">{detail}</div>
    </div>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="sidebar-empty">
      <div className="sidebar-title">{title}</div>
      <div className="sidebar-muted">{detail}</div>
    </div>
  );
}

function Badge({ meta }: { meta: DisplayMeta }) {
  return <span className={`sidebar-badge ${meta.tone}`}>{meta.label}</span>;
}

function sourceKindMeta(kind: string): DisplayMeta {
  const labels: Record<string, string> = {
    paper_pdf: '论文 PDF',
    github_repo: '代码仓库',
    local_repo: '本地仓库包',
    archive_bundle: '资料包',
    document: '文档',
    markdown: 'Markdown',
    text: '文本',
    web_url: '网页链接',
    arxiv_abs: 'arXiv 摘要',
  };
  return { label: labels[kind] || kind || '资料', tone: 'info' };
}

function sourceStatusMeta(status: string): DisplayMeta {
  const map: Record<string, DisplayMeta> = {
    parsed: { label: '已解析', tone: 'good' },
    registered: { label: '已登记', tone: 'info' },
    uploaded_not_parsed: { label: '待解析', tone: 'warn' },
    user_provided_not_ingested: { label: '已登记，待采集', tone: 'warn' },
    parsing: { label: '解析中', tone: 'warn' },
    running: { label: '处理中', tone: 'warn' },
    failed: { label: '失败', tone: 'bad' },
  };
  return map[status] || { label: status || '未知状态', tone: 'muted' };
}

export function sourceLifecycleMeta(source: SourceItem): DisplayMeta {
  const dimensions = [
    ['registration', source.registrationStatus],
    ['acquisition', source.acquisitionStatus],
    ['parse', source.parseStatus],
    ['analysis', source.analysisStatus],
    ['evidence', source.evidenceStatus],
  ] as const;
  const failed = dimensions.find(([, status]) => status === 'failed')?.[0];
  if (failed) {
    const labels: Record<string, string> = {
      registration: '登记失败',
      acquisition: '采集失败',
      parse: '解析失败',
      analysis: '分析失败',
      evidence: '证据不可用',
    };
    return { label: labels[failed], tone: 'bad' };
  }
  const running = dimensions.find(([, status]) => status === 'running')?.[0];
  if (running === 'acquisition') {
    return { label: source.kind === 'github_repo' || source.kind === 'local_repo' ? '正在下载仓库' : '正在采集', tone: 'warn' };
  }
  if (running === 'parse') return { label: '正在解析', tone: 'warn' };
  if (running === 'analysis') return { label: source.kind === 'github_repo' || source.kind === 'local_repo' ? '正在分析代码' : '正在分析', tone: 'warn' };
  if (source.analysisStatus === 'succeeded') return { label: '分析完成', tone: 'good' };
  if (source.parseStatus === 'succeeded' && source.evidenceStatus === 'succeeded') return { label: '解析完成', tone: 'good' };
  if (source.acquisitionStatus === 'succeeded' && source.analysisStatus === 'pending') {
    return { label: source.kind === 'github_repo' || source.kind === 'local_repo' ? '仓库已下载，待分析' : '已采集，待分析', tone: 'warn' };
  }
  if (source.acquisitionStatus === 'succeeded' && source.parseStatus === 'pending') return { label: '已采集，待解析', tone: 'warn' };
  if (source.acquisitionStatus === 'pending') {
    return { label: source.kind === 'github_repo' ? '等待下载仓库' : '等待采集', tone: 'warn' };
  }
  return sourceStatusMeta(source.status);
}

function jobTypeMeta(jobType: string): DisplayMeta {
  const labels: Record<string, string> = {
    paper_parse_mineru: '解析论文',
    paper_parse_pdftotext: '提取 PDF 文本',
    paper_reading_summary: '生成论文摘要',
    git_clone: '克隆仓库',
    local_repo_unpack: '解包仓库',
    local_repo_acquire: '登记本地仓库',
    archive_unpack_classify: '解包分类',
    document_markitdown: '转换文档',
    repo_summarize: '分析仓库',
    web_search: '搜索资料',
    web_fetch: '抓取网页',
    experiment_prepare: '物化实验 readiness',
  };
  return { label: labels[jobType] || jobType || '后台任务', tone: 'info' };
}

function jobStatusMeta(status: string): DisplayMeta {
  const map: Record<string, DisplayMeta> = {
    queued: { label: '排队中', tone: 'warn' },
    pending: { label: '等待中', tone: 'warn' },
    running: { label: '运行中', tone: 'warn' },
    completed: { label: '已完成', tone: 'good' },
    failed: { label: '失败', tone: 'bad' },
    cancelled: { label: '已取消', tone: 'muted' },
    skipped: { label: '已跳过', tone: 'muted' },
  };
  return map[status] || { label: status || '未知状态', tone: 'muted' };
}

function evidenceTypeMeta(type: string): DisplayMeta {
  const labels: Record<string, string> = {
    paper_markdown_fallback: '论文正文',
    paper_text: '论文正文片段',
    paper_reading_summary: '论文阅读摘要',
    paper_method_cards: '论文方法卡片',
    paper_artifact_manifest: '论文产物清单',
    paper_summary: '论文结构摘要',
    paper_candidates: '论文候选线索',
    method_components: '方法组件',
    sections: '论文章节',
    uploaded_text: '上传文本',
    document_markdown: '文档正文',
    archive_manifest: '资料包清单',
    web_markdown: '网页正文',
    repo_summary: '仓库摘要',
  };
  return { label: labels[type] || type || '证据', tone: 'info' };
}

function supportMeta(level: string): DisplayMeta {
  const map: Record<string, DisplayMeta> = {
    supported: { label: '可用', tone: 'good' },
    weak: { label: '弱证据', tone: 'warn' },
    unsupported: { label: '不可用', tone: 'bad' },
  };
  return map[level] || { label: level || '未知', tone: 'muted' };
}

function parserLabel(parser: string | undefined): string {
  const labels: Record<string, string> = {
    pdftotext: 'PDF 文本提取',
    paper_reading_summarizer: '论文摘要器',
    mineru_pipeline_v1: 'MinerU 解析',
    direct_upload: '直接上传',
  };
  if (!parser) return '解析器未知';
  return labels[parser] || parser;
}

function compactSummary(summary: string): string {
  const cleaned = summary
    .replace(/^#+\s*/gm, '')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned.length > 180 ? `${cleaned.slice(0, 179)}…` : cleaned;
}

function evidencePreview(item: EvidenceItem): { text: string; tone?: 'muted' } | null {
  if (item.evidenceType === 'paper_markdown_fallback') {
    return { text: '已提取论文正文，可作为详细事实源。正文较长，默认不在侧栏展开。', tone: 'muted' };
  }
  if (item.evidenceType === 'paper_text') {
    return { text: compactSummary(item.summary || '已定位到论文正文片段，可用于回答具体论文内容。'), tone: 'muted' };
  }
  if (item.evidenceType === 'paper_artifact_manifest') {
    const raw = item.raw || {};
    const summaryPath = typeof raw.summary_path === 'string' ? raw.summary_path : '';
    return {
      text: summaryPath ? `记录可用论文产物；默认阅读入口：${summaryPath}` : '记录可用论文产物和详情路径。',
      tone: 'muted',
    };
  }
  if (!item.summary) return null;
  return { text: localizeEvidenceSummary(compactSummary(item.summary)) };
}

function localizeEvidenceSummary(summary: string): string {
  return summary
    .replace(/\bTitle:\s*/g, '标题：')
    .replace(/\bReadable sections:\s*/g, '可读章节：')
    .replace(/\bMost relevant excerpt:\s*/g, '相关摘录：')
    .replace(/\bPaper reading summary:\s*/g, '论文阅读摘要：')
    .replace(/\bPaper artifact manifest:\s*/g, '论文产物清单：')
    .replace(/\bMethod cards:\s*/g, '方法卡片：')
    .replace(/\bParsed paper text\b/g, '论文正文片段')
    .replace(/\bpage\s+(\d+)/gi, '第 $1 页')
    .replace(/Use this summary as a routing artifact;?.*$/i, '用于定位详细论文证据。');
}

function humanJobError(error: string): string {
  const lowered = error.toLowerCase();
  if (error.includes('dependency failed')) return '上游任务失败，所以这个任务没有继续执行。';
  if (lowered.includes('timed_out') || lowered.includes('timeout')) return '连接或拉取超时，当前没有成功拿到远端内容。';
  if (lowered.includes('gnutls') || lowered.includes('tls connection') || lowered.includes('recv error')) return 'TLS/网络传输中断，仓库拉取没有完成。';
  if (lowered.includes('cloning into') && lowered.includes('tool_git_clone')) return 'clone 传输没有完成，通常是网络连接中断或远端传输失败。';
  if (lowered.includes('unable to access')) return '无法访问目标仓库地址；请确认 URL 是否干净且当前环境可访问。';
  return error.length > 180 ? `${error.slice(0, 179)}…` : error;
}

function detailPath(item: EvidenceItem): string {
  const raw = item.raw || {};
  if (typeof raw.source_markdown === 'string' && raw.source_markdown) return raw.source_markdown;
  if (typeof raw.detail_path === 'string' && raw.detail_path) return raw.detail_path;
  if (typeof raw.source_of_truth === 'string' && raw.source_of_truth) return raw.source_of_truth;
  return '';
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
