import { useState } from 'react';
import type { SourceItem, JobItem, EvidenceItem, UnusableParsedSource, TabId, DraftField, DraftState } from '../lib/types';

interface Props {
  sources: SourceItem[];
  jobs: JobItem[];
  evidence: EvidenceItem[];
  unusableParsedSources: UnusableParsedSource[];
  evidenceCount: number;
  draftReady: boolean;
  draft?: DraftState | null;
  onDeleteSource?: (sourceId: string) => void;
  children?: React.ReactNode;
}

interface DisplayMeta {
  label: string;
  tone: 'good' | 'warn' | 'bad' | 'info' | 'muted';
}

const CORE_DRAFT_FIELDS = new Set(['research_goal', 'baseline', 'dataset', 'primary_metrics', 'success_criteria']);
const METHOD_DRAFT_FIELDS = new Set(['preferred_method_hints', 'user_improvement_hints', 'target_module', 'improvement_idea']);

export function Sidebar({ sources, jobs, evidence, unusableParsedSources, evidenceCount, draftReady, draft, onDeleteSource, children }: Props) {
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
        {tab === 'draft' && <DraftPanel draft={draft || null} />}
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
        const status = sourceStatusMeta(source.status);
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

function DraftPanel({ draft }: { draft: DraftState | null }) {
  if (!draft || !draft.has_draft) {
    return <EmptyState title="暂无研究计划草案" detail="当对话里出现基线、数据集、指标或资料线索后，草案会自动整理。" />;
  }
  const coreFields = draft.fields.filter(field => CORE_DRAFT_FIELDS.has(field.field));
  const methodFields = draft.fields.filter(field => METHOD_DRAFT_FIELDS.has(field.field));
  const otherFields = draft.fields.filter(field => !CORE_DRAFT_FIELDS.has(field.field) && !METHOD_DRAFT_FIELDS.has(field.field));
  return (
    <div className="sidebar-stack">
      <div className="sidebar-card">
        <div className="sidebar-card-head">
          <div className="sidebar-title">{draft.title || '研究计划草案'}</div>
          <Badge meta={{ label: '自动整理', tone: 'info' }} />
        </div>
        <div className="sidebar-body">
          根据当前对话和材料持续更新。
        </div>
      </div>

      <DraftSection title="核心信息" fields={coreFields} />
      <DraftSection title="方法线索" fields={methodFields} />
      <DraftSection title="执行与来源" fields={otherFields} />

      <div className="sidebar-muted">
        草案来源：{draft.sources.length} 个资料，{draft.evidence.length} 条证据，{draft.jobs.length} 个相关任务。
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
