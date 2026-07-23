function label(value: string, labels: Record<string, string>): string {
  return labels[value] || `未识别状态（原始值：${value}）`;
}

export const reportGenerationStatusLabel = (value: string) => label(value, {
  queued: '排队中',
  building_snapshot: '冻结报告输入',
  assembling_facts: '整理证据',
  generating_narrative: '生成报告正文',
  validating: '校验报告',
  content_ready: '内容可读',
  failed: '生成失败',
});

export const reportReviewStatusLabel = (value: string) => label(value, {
  unreviewed: '待审阅',
  accepted: '已接受',
  needs_more: '需要更多证据',
  needs_repair: '需要修复',
  disputed: '存在争议',
});

export const reportFormatLabel = (value: string) => label(value, {
  markdown: 'Markdown',
  html: 'HTML',
  pdf: 'PDF',
  bundle: '报告包',
});

export const reportFormatStatusLabel = (value: string) => label(value, {
  missing: '未生成',
  unavailable: '不可用',
  queued: '排队中',
  ready: '已就绪',
  failed: '生成失败',
});

export const reportJobTypeLabel = (value: string) => label(value, {
  report_snapshot_build: '冻结报告输入',
  report_facts_assemble: '整理证据',
  report_narrative_generate: '生成报告正文',
  report_validate: '校验报告',
  report_render_html: '生成 HTML',
  report_render_pdf: '生成 PDF',
  report_package: '打包报告',
});

export const reportJobStatusLabel = (value: string) => label(value, {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
});

export const reportEngineeringStatusLabel = (value: string) => label(value, {
  READY: '就绪',
  NOT_READY: '尚未就绪',
  FAILED: '失败',
  EVIDENCE_INSUFFICIENT: '证据不足',
});

export const reportExecutionStatusLabel = (value: string) => label(value, {
  COMPLETED: '已完成',
  CRASHED: '执行崩溃',
  TIMEOUT: '执行超时',
  CANCELLED: '已取消',
  LOST: '运行状态丢失',
});

export const reportScientificStatusLabel = (value: string) => label(value, {
  IMPROVEMENT: '改进',
  NO_EFFECT: '无显著效应',
  REGRESSION: '退化',
  INCONCLUSIVE: '结论不充分',
  EVIDENCE_INSUFFICIENT: '证据不足',
});

export const reportChampionStatusLabel = (value: string) => label(value, {
  available: '已记录',
  absent: '尚未形成',
  not_materialized: '尚未物化',
  control_plane_invalid: '控制面无效',
});

export const reportProposalTypeLabel = (value: string) => label(value, {
  ADD_CONFIRMATION: '增加确认评估',
  RETRY_FAILED: '重试失败实验',
  REFINE_CURRENT: '细化当前方向',
  PIVOT: '切换研究方向',
  REQUEST_HUMAN: '请求人工判断',
});

export const reportProposalStatusLabel = (value: string) => label(value, {
  DRAFT: '草稿',
  READY_FOR_CONFIRMATION: '待确认',
  CONFIRMED: '已确认',
  REJECTED: '已拒绝',
  SUPERSEDED: '已替代',
  HANDED_OFF: '已转交人工',
});

export const reportEvidenceKindLabel = (value: string) => label(value, {
  frozen_session: '冻结的 Session',
  outcome_card: '实验结果卡片',
  attempt_stdout_log: '实验标准输出',
  patch_diff: '代码变更',
  approval_artifact: '审批记录',
});

export const reportHandoffKindLabel = (value: string) => label(value, {
  human_queue: '人工队列',
  retry: '失败重试',
  confirmation: '确认评估',
  pivot: '研究方向切换',
});
