function label(value: string, labels: Record<string, string>): string {
  return labels[value] || `未知状态（原始值：${value}）`;
}

export const sessionStatusLabel = (value: string, baselineStatus = '') => {
  if (value === 'READY_FOR_BASELINE' && baselineStatus === 'b_dev_completed') return 'B_dev 已完成';
  return label(value, {
  CREATED: '已创建', ENVIRONMENT_PENDING: '环境准备中', ENVIRONMENT_RUNNING: '环境配置中', ENVIRONMENT_FAILED: '环境配置失败', READY_FOR_BASELINE: '等待基线', BASELINE_RUNNING: '基线运行中', READY: '就绪', FAILED: '失败', CANCELLED: '已取消',
  });
};
export const environmentStatusLabel = (value: string) => label(value, { not_started: '未开始', pending: '等待中', running: '运行中', ready: '就绪', failed: '失败' });
export const baselineStatusLabel = (value: string) => label(value, { not_started: '未开始', queued: '已排队', running: '运行中', b_dev_completed: 'B_dev 已完成', completed: '已完成', failed: '失败' });
export const ideaStatusLabel = (value: string) => label(value, { DRAFT: '草稿', REVIEWED: '已审阅', READY: '等待实验', RUNNING: '实验中', SUPPORTED: '获得证据支持', NOT_SUPPORTED: '未获得支持', INCONCLUSIVE: '证据不足', PRUNED: '已停止探索', MERGED: '已合并' });
export const attemptStatusLabel = (value: string) => label(value, { QUEUED: '等待运行', STARTING: '正在启动', RUNNING: '运行中', TERMINATING: '正在终止', COMPLETED: '已完成', FAILED: '运行失败', TIMED_OUT: '运行超时', CANCELLED: '已取消', LOST: '运行状态丢失' });
export const attemptJobTypeLabel = (value: string) => label(value, { experiment_baseline: '基线评估', experiment_baseline_b_test: '基线 B_test 评估', experiment_attempt: '候选探索', experiment_confirmatory: '确认评估' });
export const attemptCategoryLabel = (value: string) => label(value, { scientifically_evaluable: '科学上可评价', run_failed: '运行失败', protocol_violated: '协议违规' });
export const authorityLabel = (value: string) => label(value, { outcome_card: 'OutcomeCard（执行事实）', scientific_assessment: 'ScientificAssessment（科学比较）' });
export const eventTypeLabel = (value: string) => label(value, {
  'experiment.session.created': '实验 Session 创建',
  'experiment.idea_tree.created': 'Idea 树初始化',
  'experiment.idea_tree.mutated': 'Idea 树更新',
  'experiment.attempt.created': '实验创建',
  'experiment.attempt.queued': '实验排队',
  'experiment.attempt.running': '实验开始运行',
  'experiment.attempt.finalized': '实验完成',
  'experiment.attempt.retry_queued': '重试排队',
  'experiment.attempt.reconnected': '实验已重新连接',
  'experiment.cognitive_commit.appended': '认知提交记录',
  'experiment.observation_snapshot.written': '观察快照写入',
  'experiment.cognitive_usage.recorded': '认知预算记录',
  'experiment.candidate.b_test_queued': 'B_test 排队',
  'experiment.candidate.registered': '候选登记',
  'experiment.coordinator.checkpoint.recorded': '协调器检查点记录',
  'experiment.coordinator.recovered': '协调器恢复',
  'experiment.coordinator.context_pruned': '协调器上下文裁剪',
  'experiment.coordinator.compact_cycle.committed': '协调器紧凑周期提交',
  'experiment.coordinator.exploratory_cycle.committed': '协调器探索周期提交',
  'experiment.convergence.alert': '收敛提醒',
  'experiment.stop_policy.evaluated': '停止策略评估',
  'experiment.strategy.filtered': '策略筛选',
  'experiment.champion.rolled_back': 'Champion 回滚',
  'experiment.champion.promoted_and_merged': 'Champion 推广并合并',
});
export const executionStatusLabel = (value: string) => label(value, { COMPLETED: '已完成', CRASHED: '执行崩溃', TIMEOUT: '执行超时', CANCELLED: '已取消', LOST: '运行状态丢失' });
export const attemptPurposeLabel = (value: string) => label(value, { baseline: '基线', exploration: '探索', confirmation: '确认评估', noise_calibration: '噪声校准', repair: '修复' });
export const evaluationStatusLabel = (value: string) => label(value, { COMPARABLE: '可比较', NON_COMPARABLE: '不可比较' });
export const scientificEffectLabel = (value: string) => label(value, { IMPROVEMENT: '改进', NO_EFFECT: '无显著效应', REGRESSION: '退化', INCONCLUSIVE: '结论不充分' });
export const costLabel = (value: string) => label(value, { low: '低', medium: '中', high: '高', unknown: '未记录' });
