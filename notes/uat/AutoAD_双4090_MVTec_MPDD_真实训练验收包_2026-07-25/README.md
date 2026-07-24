# AutoAD 双 RTX 4090 真实训练 UAT 包（MVTec AD → MPDD 类别隔离）

## 目标

在冻结的 AnomalyCLIP 基线上，根据 FAPrompt 论文和官方参考实现生成并执行候选方法，使用两张 RTX 4090 完成真实训练与真实评价。

本版本**不需要 VisA**：

- 辅助训练数据：完整 MVTec AD；
- B_dev：MPDD 固定类别 `bracket_black`、`connector`；
- B_test：MPDD 固定类别 `bracket_brown`、`bracket_white`、`metal_plate`、`tubes`；
- B_test 类别在 B_dev 通过并经人工批准前不得用于结果读取、调参或 checkpoint 选择；
- baseline 与 candidate 的主指标均为类别宏平均 image AUROC；
- 成功门槛为 candidate 相对 baseline 提升至少 **2.0 个百分点**。

这属于 **cross-dataset zero-shot anomaly detection + target-class holdout**，不是两个独立目标数据集的 cross-dataset 测试。FAPrompt 论文报告的 MPDD 全集 +3.1 pp 仅作为可行性先验，不能证明本次固定类别划分一定达到 +2 pp。

## 固定类别划分

```text
B_dev:
  bracket_black
  connector

B_test:
  bracket_brown
  bracket_white
  metal_plate
  tubes
```

划分在训练前冻结。不得根据任何运行结果更换类别。

## 双卡安排

- GPU 0：AnomalyCLIP baseline；
- GPU 1：AutoAD candidate 或固定 FAPrompt oracle；
- 每个 seed 的 baseline/candidate 并行；
- 不使用 DDP/FSDP；单个 Attempt 只占一张 GPU。

## 执行顺序

```text
0. 解压 AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip
1. 复制 config.example.env 为 config.env，并填写真实绝对路径
2. scripts/00_clone_and_pin.sh
3. 准备 MVTec AD 与 MPDD 官方目录
4. scripts/01_prepare_metadata.sh
5. scripts/02_preflight.sh
6. scripts/03_run_pair.sh 111 b_dev
7. B_dev delta >= +2.0 pp 后，由 AutoAD UI 人工批准
8. scripts/03_run_pair.sh 111 b_test
9. 运行 seeds 222、333 的 b_test
10. scripts/04_summarize.sh
```

脱离产品做环境校准时，才可使用 `scripts/approve_b_test.sh I_APPROVE_B_TEST`。正式产品验收应读取仓库当前真实 Approval Schema，不得伪造兼容字段。

## 候选路径

### 正式 AutoAD candidate

`CANDIDATE_REPO_ROOT` 指向 AutoAD 在隔离 worktree 中生成的候选实现。首选 CAP + DAP，保持官方 CLI 与评价接口兼容。

### FAPrompt oracle

`CANDIDATE_REPO_ROOT` 指向固定提交的官方 FAPrompt 仓库，仅用于校准环境、数据和指标链。Oracle 成功不能替代 AutoAD 生成候选的验收。

## 硬约束

- MPDD 不得用于训练或微调；
- B_test 类别不得用于 Idea 选择、超参数选择或 checkpoint 选择；
- 不得修改 `metrics.py`、冻结后的 `meta.json`、类别划分、指标解析器或评价合同；
- baseline/candidate 必须使用相同 MVTec 数据、seed、epoch、image size 和评价实现；
- 论文指标不是本机结果；
- 未达到门槛时必须保留失败证据，不得伪造 Champion 或 promotion passed。

## 仓库内容

- 可浏览的评价合同、类别划分、Agent 任务提示和真人执行清单；
- 完整可执行资料包 ZIP；
- ZIP SHA256 校验文件。

仓库不包含 MVTec AD、MPDD 数据、论文 PDF、checkpoint 或训练结果。
