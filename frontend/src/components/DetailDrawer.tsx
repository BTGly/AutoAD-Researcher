import type { ExperimentActivity, ExperimentAttempt, ExperimentIdeaNode } from '../lib/types';
import { attemptPurposeLabel, attemptStatusLabel, costLabel, evaluationStatusLabel, executionStatusLabel, ideaStatusLabel, scientificEffectLabel } from '../lib/experimentLabels';

type Selection = { kind: 'idea'; value: ExperimentIdeaNode } | { kind: 'attempt'; value: ExperimentAttempt } | { kind: 'activity'; value: ExperimentActivity } | null;

export function DetailDrawer({ selection, onDiscuss }: { selection: Selection; onDiscuss: (text: string) => void }) {
  if (!selection) return <div style={{ color: 'var(--text-muted)', padding: 12 }}>选择一个 Idea、实验或动态以查看详情。</div>;
  if (selection.kind === 'idea') return <IdeaDetail item={selection.value} onDiscuss={onDiscuss} />;
  if (selection.kind === 'attempt') return <AttemptDetail item={selection.value} onDiscuss={onDiscuss} />;
  return <ActivityDetail item={selection.value} onDiscuss={onDiscuss} />;
}

function IdeaDetail({ item, onDiscuss }: { item: ExperimentIdeaNode; onDiscuss: (text: string) => void }) {
  return <div style={{ display: 'grid', gap: 10 }}>
    <h3 style={{ margin: 0 }}>{item.mechanism || (item.is_root ? '研究根节点' : '未记录机制')}</h3>
    <Field label="状态" value={ideaStatusLabel(item.status)} /><Field label="假设" value={item.hypothesis} /><Field label="可观察量" value={item.observable} />
    <Field label="研究轴" value={item.research_axis} /><Field label="最小干预" value={item.minimal_intervention} /><Field label="证伪条件" value={item.falsification} />
    <Field label="与既有 Idea 的关系" value={item.relationship_to_previous_ideas} /><Field label="预期成本" value={costLabel(item.expected_cost)} />
    <Field label="依据" value={join(item.grounding)} /><Field label="证据引用" value={join(item.evidence_refs)} />
    <Field label="关联实验" value={join(item.attempt_refs)} /><Field label="关联认知提交" value={join(item.cognitive_commit_refs)} />
    <Field label="父节点" value={item.parent_id} /><Field label="子节点" value={join(item.children)} />
    <Field label="已记录观察" value={item.insights.length ? item.insights.map(insight => typeof insight.text === 'string' ? insight.text : '已记录观察').join('；') : '暂无已记录原因'} />
    <button onClick={() => onDiscuss(`请讨论 Idea ${item.node_id}：${item.mechanism || ''}`)}>在研究助手中讨论</button>
  </div>;
}

function AttemptDetail({ item, onDiscuss }: { item: ExperimentAttempt; onDiscuss: (text: string) => void }) {
  const assessmentDetail = scientificAssessmentDetail(item);
  const outcome = item.execution_outcome;
  const assessment = item.scientific_assessment;
  return <div style={{ display: 'grid', gap: 12 }}>
    <h3 style={{ margin: 0 }}>实验 {item.attempt_id}</h3>
    <section><b>执行事实</b>
      <Field label="运行状态" value={attemptStatusLabel(item.runtime_status)} /><Field label="用途" value={attemptPurposeLabel(item.attempt_purpose)} /><Field label="任务类型" value={item.job_type} />
      <Field label="命令" value={item.command_plan_summary} /><Field label="重试" value={retryDetail(item)} /><Field label="失败码" value={item.failure_code} />
      <Field label="资源请求" value={`${item.required_device_count} 个设备；${item.required_vram_mb} MB 显存`} /><Field label="资源租约" value={item.resource_lease_id} /><Field label="最近心跳" value={item.heartbeat_at} />
      <Field label="OutcomeCard" value={outcomeDetail(outcome)} /><Field label="指标已解析" value={bool(outcome?.metrics_parsed)} />
    </section>
    <section><b>科学评价</b>
      <Field label="状态" value={assessmentDetail.status} /><Field label="详情" value={assessmentDetail.detail} />
      {assessment && <><Field label="Guardrail 变化" value={recordDetail(assessment.guardrail_deltas)} /><Field label="补丁已应用" value={bool(assessment.patch_applied)} /><Field label="Smoke 测试通过" value={bool(assessment.smoke_passed)} /><Field label="OutcomeCard 引用" value={assessment.outcome_card_ref} /><Field label="评价输入引用" value={assessment.inputs_ref} /></>}
    </section>
    <section><b>权威边界</b><Field label="Assessment reconciliation" value={reconciliationDetail(item.assessment_reconciliation)} /></section>
    <button onClick={() => onDiscuss(`请讨论实验 ${item.attempt_id} 的结果。`)}>在研究助手中讨论</button>
  </div>;
}

function retryDetail(item: ExperimentAttempt): string {
  const source = item.retry_of ? `重试自 ${item.retry_of}` : '首次尝试';
  return `${source}；第 ${item.retry_count}/${item.max_retries} 次${item.retry_exhausted ? '；已耗尽重试次数' : ''}`;
}

function outcomeDetail(value: Record<string, unknown> | null): string {
  if (!value) return '尚未产生';
  const execution = typeof value.execution_status === 'string' ? `执行：${executionStatusLabel(value.execution_status)}` : null;
  const category = typeof value.attempt_category === 'string' ? `类别：${value.attempt_category}` : null;
  const protocol = typeof value.protocol_intact === 'boolean' ? `协议完整：${bool(value.protocol_intact)}` : null;
  return [execution, category, protocol].filter(Boolean).join('；') || '已记录执行结果';
}

function reconciliationDetail(value: Record<string, unknown> | null): string {
  if (!value) return '暂无';
  const status = typeof value.effective_evaluation_status === 'string' ? `有效比较状态：${evaluationStatusLabel(value.effective_evaluation_status)}` : null;
  const execution = typeof value.execution_protocol_authority === 'string' ? `执行事实权威：${value.execution_protocol_authority}` : null;
  const science = typeof value.scientific_comparison_authority === 'string' ? `科学比较权威：${value.scientific_comparison_authority}` : null;
  return [status, execution, science].filter(Boolean).join('；') || '已记录评价链路';
}

function scientificAssessmentDetail(item: ExperimentAttempt): { status: string; detail: string } {
  if (item.scientific_assessment_status === 'not_materialized') return { status: '尚未物化', detail: item.execution_outcome ? '执行事实已记录，科学评价尚未物化。' : '尚未产生可评价的执行结果。' };
  if (item.scientific_assessment_status === 'invalid') return { status: '工件无效', detail: '科学评价工件存在但未通过校验，不能作为研究结论。' };
  const assessment = item.scientific_assessment;
  if (!assessment) return { status: '工件无效', detail: '科学评价状态与工件内容不一致，不能作为研究结论。' };
  const effect = typeof assessment.scientific_effect === 'string' ? scientificEffectLabel(assessment.scientific_effect) : '未形成';
  const delta = typeof assessment.primary_delta === 'number' ? `；主指标变化：${assessment.primary_delta}` : '';
  const comparison = typeof assessment.evaluation_status === 'string' ? `比较状态：${evaluationStatusLabel(assessment.evaluation_status)}` : '比较状态未记录';
  return { status: '可用', detail: `${comparison}；科学效应：${effect}${delta}` };
}

function ActivityDetail({ item, onDiscuss }: { item: ExperimentActivity; onDiscuss: (text: string) => void }) {
  return <div style={{ display: 'grid', gap: 10 }}><h3 style={{ margin: 0 }}>{item.title}</h3><Field label="摘要" value={item.summary} /><Field label="时间" value={item.created_at} /><Field label="事件类型" value={item.event_type} /><Field label="关联 Idea" value={item.related_idea_id} /><Field label="关联实验" value={item.related_attempt_id} /><Field label="关联认知提交" value={item.related_commit_id} /><Field label="证据引用" value={join(item.evidence_refs)} /><button onClick={() => onDiscuss(`请讨论实验动态 ${item.event_id}：${item.title}`)}>在研究助手中讨论</button></div>;
}

function bool(value: unknown): string { return value === true ? '是' : value === false ? '否' : '未记录'; }
function join(values: string[]): string { return values.length ? values.join('；') : '暂无'; }
function recordDetail(value: unknown): string { return value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length ? Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${String(item)}`).join('；') : '暂无'; }
function Field({ label, value }: { label: string; value: unknown }) { const text = value === null || value === undefined || value === '' ? '暂无' : String(value); return <div style={{ marginTop: 5 }}><div style={{ fontSize: '0.76em', color: 'var(--text-dim)' }}>{label}</div><div style={{ fontSize: '0.86em', overflowWrap: 'anywhere' }}>{text}</div></div>; }

export type ExperimentDetailSelection = Selection;
