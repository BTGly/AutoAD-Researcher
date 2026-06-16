# AutoAD-Researcher

> **面向异常检测的文献迁移与实验闭环智能体**
>
> 给定一篇论文、一个方法想法或一个实验目标，系统能够判断该方法是否适合迁移到异常检测任务，生成实验方案，辅助修改代码，运行小规模实验，读取日志和指标，并输出结果分析和下一轮建议。

---

## 核心闭环

```text
论文 / 方法想法 / 实验目标
→ 意图澄清
→ 论文理解 (MinerU + MarkItDown)
→ 方法可迁移性判断
→ 实验方案生成
→ 代码修改计划 / patch
→ 人工确认
→ 实验运行 (Anomalib + MVTec AD)
→ 日志与指标读取
→ baseline 对比
→ 失败原因分析
→ 下一轮实验建议
→ Markdown 实验报告
```

## 关键文档

| 文档 | 说明 |
|------|------|
| [技术路线草案](docs/AutoAD_Researcher_技术路线草案.md) | 完整系统设计：13个模块、MVP定义、技术栈、Demo设计 |
| [参考资料汇总](docs/AutoAD_参考资料汇总.md) | 参考论文、开源仓库、工具对比、BibTeX汇总 |

## 第一版边界

- **方向：** 视觉异常检测 / 工业缺陷检测
- **数据集：** MVTec AD 的 1-2 个类别 (bottle, capsule, cable)
- **Baseline：** PatchCore / PaDiM / FastFlow
- **目标：** 跑通最小实验闭环

## CLI smoke test

运行确定性的 planning pipeline，不调用 LLM：

```bash
uv run autoad smoke --run-id run_demo
uv run autoad smoke --run-id run_demo --json
uv run python -m autoad_researcher smoke --run-id run_demo
```

> `smoke` 使用 `SimplePipelineHarness`，只验证 AutoAD Core 闭环（artifact 读写、事件日志、pipeline 编排），不代表真实 LLM 科研能力。

产物保存在：

```text
runs/run_demo/
├── experiment_plan.json
├── patch_plan.json
└── events.jsonl
```

## 输入事实层

完整的确定性链路：

```text
runs/run_demo/
├── input_task.yaml          ← 用户原始任务和已知约束
├── source_manifest.json     ← 用户提供材料的结构化索引
├── paper_summary.json       ← 论文结构化摘要
├── repo_summary.json        ← 仓库结构化摘要
├── clarified_task.json      ← 已知事实、缺口、候选值和问题
├── idea_context.json        ← 路由模式和上下文快照
├── idea_candidates.json     ← 1–3 个统一格式的候选科研方案
```

Intent Clarifier 识别缺口、提供候选值并生成问题；**baseline、dataset 和 metrics 只有在用户确认后才能成为正式任务字段**。系统可以推荐 baseline，但不能替用户决定 baseline、dataset 或实验资源。

## 不做什么

- 不做全领域 AI 科学家
- 不做自动发现 SOTA
- 不让 Agent 不经确认直接改代码
- 不依赖未授权源码
