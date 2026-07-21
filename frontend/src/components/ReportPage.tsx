import { useEffect, useState } from 'react';
import { ArrowLeft, FileText, LoaderCircle } from 'lucide-react';
import { getReport } from '../lib/api';
import { MarkdownContent } from './MarkdownContent';
import { AppButton } from './ui/AppButton';
import { EmptyState } from './ui/EmptyState';
import { Surface } from './ui/Surface';

interface Props {
  runId: string;
  onBack: () => void;
}

export function ReportPage({ runId, onBack }: Props) {
  const [report, setReport] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    getReport(runId)
      .then(res => setReport(res.content || null))
      .catch(() => setReport(null))
      .finally(() => setLoading(false));
  }, [runId]);

  return (
    <main className="report-workspace">
      <header className="report-toolbar">
        <div>
          <h1>研究报告</h1>
          <div className="report-subtitle">当前 run 的已持久化 Markdown 报告</div>
        </div>
        <AppButton onClick={onBack}><ArrowLeft size={15} aria-hidden="true" />返回研究对话</AppButton>
      </header>

      <div className="report-layout">
        <aside className="report-outline" aria-label="报告状态">
          <Surface className="report-outline-surface">
            <FileText size={18} aria-hidden="true" />
            <div><strong>报告正文</strong><span>{report ? '已读取 Markdown 内容' : loading ? '正在读取' : '当前未返回内容'}</span></div>
          </Surface>
          <p>HTML、PDF、Bundle 及其依赖状态未包含在当前报告接口中，因此本页不推断或伪造这些状态。</p>
        </aside>

        <section className="report-paper-region">
          {loading && <EmptyState title="正在读取报告…" detail="报告内容会在读取完成后显示。" />}
          {!loading && !report && <EmptyState title="当前没有可显示的研究报告" detail="报告接口尚未返回 Markdown 正文。" />}
          {!loading && report && <article className="report-paper"><MarkdownContent>{report}</MarkdownContent></article>}
        </section>

        <aside className="report-inspector" aria-label="报告说明">
          <Surface className="report-inspector-surface">
            <h2>报告来源</h2>
            <p>内容由当前 run 的只读报告接口返回。</p>
            <code>{runId || '未选择 run'}</code>
          </Surface>
          {loading && <div className="report-loading"><LoaderCircle size={15} aria-hidden="true" />读取中</div>}
        </aside>
      </div>
    </main>
  );
}
