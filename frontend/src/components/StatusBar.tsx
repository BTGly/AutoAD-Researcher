import type { SourceItem, JobItem } from '../lib/types';

interface Props {
  sources: SourceItem[];
  jobs: JobItem[];
  evidenceCount: number;
  draftReady: boolean;
}

export function StatusBar({ sources, jobs, evidenceCount, draftReady }: Props) {
  const parsedSources = sources.filter(s => s.status === 'parsed').length;
  const pendingJobs = jobs.filter(j => j.status === 'running').length;
  const parts: string[] = [];

  if (sources.length) parts.push(`📄 Sources: ${sources.length} (${parsedSources} parsed)`);
  if (jobs.length) parts.push(`⚙ Jobs: ${jobs.length} (${pendingJobs} running)`);
  if (evidenceCount) parts.push(`🔬 Evidence: ${evidenceCount} usable`);
  if (draftReady) parts.push('📝 Draft: Ready');

  return (
    <div className="kbd-hint" style={{ textAlign: 'left', paddingLeft: 8 }}>
      {parts.length ? parts.join('  │  ') : '尚无资料。输入问题开始…'}
    </div>
  );
}
