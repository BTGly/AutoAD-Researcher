# 给 AutoAD 实验 Agents 的正式任务提示

你正在处理一个真实 zero-shot anomaly detection 实验。

输入：
- executable baseline：固定提交的 AnomalyCLIP；
- reference paper：FAPrompt；
- reference repository：固定提交的 FAPrompt，只读；
- auxiliary training dataset：完整 MVTec AD；
- target dataset：MPDD；
- B_dev 类别：`bracket_black`、`connector`；
- B_test 类别：`bracket_brown`、`bracket_white`、`metal_plate`、`tubes`。

目标：
- 提出 1-3 个有论文证据且能在当前基线中实现的 Idea；
- 首选 CAP + DAP；
- B_dev 与 B_test 的类别宏平均 image AUROC 相对 baseline 均至少 +2.0 pp；
- 使用真实训练、真实 checkpoint、真实日志和真实评价。

硬约束：
- 不得修改 `metrics.py`、冻结后的 `meta.json`、类别划分、评价合同或指标解析器；
- 不得在 MPDD 上训练、微调或选择 checkpoint；
- B_test 未经批准前不得读取指标；
- baseline/candidate 必须使用相同 seed、MVTec 数据、15 epochs、518 输入与评价实现；
- 只在隔离 worktree 修改候选代码；
- 每轮保留 patch、环境、命令、checkpoint、log、metrics JSON 和 protected-hash report；
- 未通过门禁时保留失败证据，不得伪造成功；
- 不得把论文数字当成本次实验结果。

实现边界：
- 可以修改 prompt learner、prior/conditioning、训练损失和候选 anomaly scoring；
- 不可以修改 sklearn AUROC/AP 实现、标签、类别集合或聚合规则；
- 优先复用参考仓库的成熟实现，不采用关键词特判或 fixture 名称硬编码。
