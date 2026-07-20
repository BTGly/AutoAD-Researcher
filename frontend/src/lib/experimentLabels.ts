const labels: Record<string, string> = {
  READY: '就绪', BASELINE_RUNNING: '基线运行中', FAILED: '失败',
  QUEUED: '等待运行', STARTING: '正在启动', RUNNING: '运行中', TERMINATING: '正在终止', COMPLETED: '已完成', TIMED_OUT: '运行超时', CANCELLED: '已取消', LOST: '运行状态丢失',
  DRAFT: '草稿', REVIEWED: '已审阅', SUPPORTED: '获得证据支持', NOT_SUPPORTED: '未获得支持', PRUNED: '已停止探索', MERGED: '已合并',
  not_started: '未开始', pending: '等待中', running: '运行中', ready: '就绪', completed: '已完成', failed: '失败',
  unresolved: '待确认', resolving: '准备中', blocked: '受阻',
  baseline: '基线', exploration: '探索', confirmation: '确认评估', noise_calibration: '噪声校准', repair: '修复',
  COMPARABLE: '可比较', NON_COMPARABLE: '不可比较', IMPROVEMENT: '改进', NO_EFFECT: '无显著效应', REGRESSION: '退化', INCONCLUSIVE: '结论不充分',
  low: '低', medium: '中', high: '高', unknown: '未记录',
};

export function experimentLabel(value: string): string {
  return labels[value] || `未知状态（原始值：${value}）`;
}
