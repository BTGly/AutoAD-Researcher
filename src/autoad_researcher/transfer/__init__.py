"""
可迁移性判断模块 (Transferability Judge)。

判断论文方法是否适合迁移到异常检测任务。

判断维度：
- 数据假设（是否需要标签异常样本）
- 方法结构（可迁移的是 backbone / loss / feature fusion / anomaly score）
- 实现难度
- 计算成本
- 指标兼容性
- 科学有效性（评价泄漏、协议一致性）
"""
