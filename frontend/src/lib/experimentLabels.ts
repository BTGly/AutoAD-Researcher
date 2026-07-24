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
export const executionStatusLabel = (value: string) => label(value, { COMPLETED: '已完成', CRASHED: '执行崩溃', TIMEOUT: '执行超时', CANCELLED: '已取消', LOST: '运行状态丢失' });
export const attemptPurposeLabel = (value: string) => label(value, { baseline: '基线', exploration: '探索', confirmation: '确认评估', noise_calibration: '噪声校准', repair: '修复' });
export const evaluationStatusLabel = (value: string) => label(value, { COMPARABLE: '可比较', NON_COMPARABLE: '不可比较' });
export const scientificEffectLabel = (value: string) => label(value, { IMPROVEMENT: '改进', NO_EFFECT: '无显著效应', REGRESSION: '退化', INCONCLUSIVE: '结论不充分' });
export const costLabel = (value: string) => label(value, { low: '低', medium: '中', high: '高', unknown: '未记录' });
