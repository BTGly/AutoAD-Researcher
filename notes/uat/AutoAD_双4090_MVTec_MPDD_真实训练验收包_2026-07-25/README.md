# AutoAD 双 RTX 4090 真实训练 UAT 包（MVTec AD → MPDD 类别隔离）

## 目标

在用户提供的冻结 baseline/reference 仓库上，由实验 agents 先调查真实入口、数据映射、checkpoint、指标和 GPU 拓扑，再生成候选并执行真实训练与真实评价。

本版本**不需要 VisA**，也不会替用户下载 MPDD 或任何未提供的仓库/数据：

- 辅助训练数据：完整 MVTec AD；
- B_dev：MPDD 固定类别 `bracket_black`、`connector`；
- B_test：MPDD 固定类别 `bracket_brown`、`bracket_white`、`metal_plate`、`tubes`；
- B_test 类别在 B_dev 通过并经人工批准前不得用于结果读取、调参或 checkpoint 选择；
- baseline 与 candidate 的主指标均为类别宏平均 image AUROC；
- 成功门槛为 candidate 相对 baseline 提升至少 **2.0 个百分点**。

MVTec 已是当前可运行资产；MPDD 缺失时只阻断 MPDD B_dev/B_test，不阻断 MVTec baseline 校准。用户补充 MPDD 后重新运行 metadata/preflight 即可继续。

这属于 **cross-dataset zero-shot anomaly detection + target-class holdout**，不是两个独立目标数据集的 cross-dataset 测试。FAPrompt 论文报告的 MPDD 全集 +3.1 pp 仅作为可行性先验，不能证明本次固定类别划分一定达到 +2 pp。

## GitHub 克隆后的资料包重建

仓库不再直接提交二进制 ZIP。此前 GitHub API 写入的 ZIP 被截断，因此已删除，改为 Git-safe Base64 分片和确定性重建程序。

在仓库根目录执行：

```bash
cd 'notes/uat/AutoAD_双4090_MVTec_MPDD_真实训练验收包_2026-07-25'
python3 rebuild_release_zip.py
```

程序只在以下检查全部通过后生成 ZIP：

- `release_b64/` 中 13 个固定分片完整且名称、顺序完全匹配；
- Base64 总长度为 `53,576` bytes；
- 解码后 ZIP 大小为 `40,181` bytes；
- SHA256 为 `11a5a9848df40532387324c501e39b97eb0add38b7f0b9e9efbe66c6d6333650`；
- ZIP central directory 可读；
- 所有 ZIP 成员 CRC 校验通过。

生成文件：

```text
AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip
```

随后可执行：

```bash
sha256sum -c AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip.sha256
unzip -t AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip
unzip AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip
```

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

## GPU 拓扑

已调查的 AnomalyCLIP/FAPrompt 入口没有 DDP/FSDP/torchrun，因此 UAT 脚本只对这两个单卡入口采用两个独立 Attempt 并行；candidate 若被 preflight 证实为 DDP、FSDP、DeepSpeed 或模型并行，应改用 AutoAD 的 multi-GPU Attempt 拓扑。奇数 seed 使用 GPU A/B，偶数 seed 交换角色。

## 执行顺序

```text
0. 运行 python3 rebuild_release_zip.py 并解压生成的 ZIP
1. 复制 config.example.env 为 config.env，填写用户提供的仓库、MVTec 路径和 Python 环境；MPDD 可以留空
2. scripts/00_clone_and_pin.sh：只校验仓库，不执行 clone/download
3. scripts/01_prepare_metadata.sh：先生成 MVTec metadata；MPDD 缺失只写入 awaiting_user 状态
4. scripts/02_preflight.sh：真实执行 `--help` 和 dataset mapping 检查
5. scripts/03_run_mvtec_baseline.sh 111：先完成本机 baseline 校准
6. candidate 由 AutoAD agents 调查 reference 后在隔离 worktree 生成并通过 preflight
7. MPDD 和 candidate 到位后运行 scripts/03_run_pair.sh 111 b_dev
8. B_dev 通过且经 AutoAD UI 批准后运行 seeds 111、222、333 的 B_test
9. scripts/04_summarize.sh
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

## 用户需要提供的内容

- baseline/reference 仓库的本地路径和提交；
- MVTec AD 本地目录和可用 Python 环境；
- MPDD 本地目录，只有在需要 B_dev/B_test 时才必须补充；
- 公开 CLIP 权重自动校验失败时，已有权重路径或文件。

仓库 CLI、dataset 参数、checkpoint 和指标实现由 agents 读取和 preflight 确认，不要求用户手填内部参数。

## 仓库内容

- 可浏览的评价合同、类别划分、Agent 任务提示和真人执行清单；
- `release_b64/`：正确 ZIP 的 13 个 Git-safe 编码分片；
- `rebuild_release_zip.py`：重建、SHA256、central directory 与 CRC 校验；
- `AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip.sha256`：预期校验值。

仓库不包含 MVTec AD、MPDD 数据、论文 PDF、checkpoint 或训练结果；当前执行者必须自行提供数据。
