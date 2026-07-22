import type { SourceItem, JobItem } from '../lib/types';

interface Props {
  sources: SourceItem[];
  jobs: JobItem[];
  evidenceCount: number;
  summaryAvailable: boolean;
}

export function StatusBar({ sources, jobs, evidenceCount, summaryAvailable }: Props) {
  const parsedSources = sources.filter(s => s.status === 'parsed').length;
  const pendingJobs = jobs.filter(j => j.status === 'running').length;
  const parts: string[] = [];

  if (sources.length) parts.push(`资料：${sources.length}（${parsedSources} 个已解析）`);
  if (jobs.length) parts.push(`任务：${jobs.length}（${pendingJobs} 个运行中）`);
  if (evidenceCount) parts.push(`证据：${evidenceCount} 条可用`);
  if (summaryAvailable) parts.push('研究摘要：已生成');

  if (!parts.length) return null;

  return (
    <div className="kbd-hint" style={{ textAlign: 'left', paddingLeft: 8 }}>
      {parts.join('  │  ')}
    </div>
  );
}
