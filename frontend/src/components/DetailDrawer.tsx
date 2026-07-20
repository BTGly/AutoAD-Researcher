import type { ExperimentActivity, ExperimentAttempt, ExperimentIdeaNode } from '../lib/types';

type Selection = { kind: 'idea'; value: ExperimentIdeaNode } | { kind: 'attempt'; value: ExperimentAttempt } | { kind: 'activity'; value: ExperimentActivity } | null;

export function DetailDrawer({ selection, onDiscuss }: { selection: Selection; onDiscuss: (text: string) => void }) {
  if (!selection) return <div style={{ color: 'var(--text-muted)', padding: 12 }}>选择一个 Idea、实验或动态以查看详情。</div>;
  if (selection.kind === 'idea') return <IdeaDetail item={selection.value} onDiscuss={onDiscuss} />;
  if (selection.kind === 'attempt') return <AttemptDetail item={selection.value} onDiscuss={onDiscuss} />;
  return <ActivityDetail item={selection.value} onDiscuss={onDiscuss} />;
}

function IdeaDetail({ item, onDiscuss }: { item: ExperimentIdeaNode; onDiscuss: (text: string) => void }) {
  return <div style={{ display: 'grid', gap: 10 }}>
    <h3 style={{ margin: 0 }}>{item.mechanism || '未记录机制'}</h3>
    <Field label="状态" value={item.status} /><Field label="假设" value={item.hypothesis} /><Field label="可观察量" value={item.observable} />
    <Field label="研究轴" value={item.research_axis} /><Field label="证伪条件" value={item.falsification} /><Field label="预期成本" value={item.expected_cost} />
    <Field label="已记录观察" value={item.insights.length ? JSON.stringify(item.insights) : '暂无已记录原因'} />
    <button onClick={() => onDiscuss(`请讨论 Idea ${item.node_id}：${item.mechanism || ''}`)}>在研究助手中讨论</button>
  </div>;
}

function AttemptDetail({ item, onDiscuss }: { item: ExperimentAttempt; onDiscuss: (text: string) => void }) {
  return <div style={{ display: 'grid', gap: 12 }}>
    <h3 style={{ margin: 0 }}>实验 {item.attempt_id}</h3>
    <section><b>执行事实</b><Field label="运行状态" value={item.runtime_status} /><Field label="用途" value={item.attempt_purpose} /><Field label="命令" value={item.command_plan_summary} /><Field label="失败码" value={item.failure_code} /><Field label="OutcomeCard" value={item.execution_outcome ? JSON.stringify(item.execution_outcome) : '尚未产生'} /></section>
    <section><b>科学评价</b><Field label="状态" value={item.scientific_assessment_status} /><Field label="详情" value={item.scientific_assessment ? JSON.stringify(item.scientific_assessment) : '执行结果已产生，科学评价尚未物化'} /></section>
    <section><b>权威边界</b><Field label="Assessment reconciliation" value={item.assessment_reconciliation ? JSON.stringify(item.assessment_reconciliation) : '暂无'} /></section>
    <button onClick={() => onDiscuss(`请讨论实验 ${item.attempt_id} 的结果。`)}>在研究助手中讨论</button>
  </div>;
}

function ActivityDetail({ item, onDiscuss }: { item: ExperimentActivity; onDiscuss: (text: string) => void }) {
  return <div style={{ display: 'grid', gap: 10 }}><h3 style={{ margin: 0 }}>{item.title}</h3><Field label="摘要" value={item.summary} /><Field label="时间" value={item.created_at} /><button onClick={() => onDiscuss(`请讨论实验动态 ${item.event_id}：${item.title}`)}>在研究助手中讨论</button></div>;
}

function Field({ label, value }: { label: string; value: unknown }) {
  const text = value === null || value === undefined || value === '' ? '暂无' : String(value);
  return <div style={{ marginTop: 5 }}><div style={{ fontSize: '0.76em', color: 'var(--text-dim)' }}>{label}</div><div style={{ fontSize: '0.86em', overflowWrap: 'anywhere' }}>{text}</div></div>;
}

export type ExperimentDetailSelection = Selection;
