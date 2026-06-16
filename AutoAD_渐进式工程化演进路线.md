# AutoAD-Researcher 渐进式工程化演进路线

> 文档用途：指导 AutoAD-Researcher 在跑通最小真实闭环之后，逐步从本地文件系统原型演进为可并发、可恢复、可扩展的科研 Agent 产品。  
> 核心原则：**先跑通真实纵向闭环，再按真实瓶颈逐步迁移；不做一次性“大重构”。**

---

## 1. 背景与当前判断

AutoAD-Researcher 当前采用：

```text
Python
+ runs/{run_id}/ 文件工作区
+ Pydantic schema
+ ArtifactStore
+ EventStore
+ PipelineController
+ 可替换 Harness Backend
```

这种方式非常适合当前阶段：

- 结构清晰；
- 容易调试；
- 便于测试；
- 容易审计；
- 不依赖复杂基础设施；
- 适合比赛 MVP 和单机研发。

但文件系统方案不应无限延伸。随着系统进入真实实验、并发运行、多人协作和长任务阶段，会逐渐出现：

```text
大量 run 难以查询
并发写入难控制
跨机器访问困难
事务和一致性不足
artifact 占用本地磁盘
长实验中断后难恢复
用户审批等待期间进程不能长期保持
多分支实验调度困难
失败重试、超时、取消不统一
```

因此，推荐的长期演进方向是：

```text
文件工作区原型
→ 元数据进入数据库
→ 大型 artifact 进入对象存储
→ 长任务进入 Workflow Runtime
→ 可观测性、权限和多用户能力逐步补齐
```


### 1.1 当前产品范围：单一既定 Idea 优先

在第一条真实纵向闭环中，AutoAD **不负责多 Agent 发散式选题**，也不同时维护多条候选路线。

只支持两种输入模式：

```text
模式 A：用户已给定明确实现方案
- 指定修改目标
- 指定模块或插入位置
- 指定需要保持不变的 baseline/evaluation 逻辑
- 系统负责校验、规划、生成 patch、执行和分析

模式 B：用户提供一篇固定论文 PDF
- 系统解析该论文
- 围绕该论文提取一个主要可迁移 Idea
- 由用户确认这个单一 Idea
- 再进入迁移判断、实验规划和代码修改
```

第一阶段明确不做：

```text
多 Agent 自由讨论
同时生成 1–3 个候选 Idea
多候选排序与投票
并行实验分支
自动扩展研究方向
跨 run 的 Agent 团队记忆
```

近期目标应收缩为：

```text
一个 run
→ 一个已确认的 Idea
→ 一个 baseline
→ 一个最小 patch
→ 一个受控实验
→ 一个真实结果
→ 一份可追溯报告
```

这样可以减少 AI 生成代码时的分支数量和隐藏状态，便于逐步检查每个 commit，防止系统在尚未具备可靠状态管理时跑飞。

多 Agent Ideation 延后到数据库元数据层稳定之后。最低前置条件是：

```text
SQLite 或 PostgreSQL 元数据仓储已投入使用
run / stage / artifact / event 可查询
Idea 与实验结果可以建立稳定关联
历史失败和用户选择可以被检索
具备候选 Idea 的去重、追踪和状态记录能力
```

---

## 2. 不做“大爆炸重构”

后续迁移必须遵循以下原则：

### 2.1 先保留现有行为

任何新基础设施都不能立即删除现有：

```text
ArtifactStore
EventStore
runs/{run_id}
SimplePipelineHarness
CLI smoke
现有 pytest / verify.sh
```

新实现应先通过抽象接口接入，再逐步替换底层。

### 2.2 每次只迁移一个责任

错误做法：

```text
一次性引入 PostgreSQL
+ S3/MinIO
+ Temporal
+ Redis
+ FastAPI
+ 多用户系统
+ Kubernetes
```

推荐顺序：

```text
1. 跑通真实纵向闭环
2. 抽象元数据仓储接口
3. SQLite 接管元数据
4. 对象存储接管大型 artifact
5. PostgreSQL 替换 SQLite
6. Workflow Runtime 接管长任务
7. 再增加多用户、权限、监控和分布式执行
```

### 2.3 每一步必须可回退

每次迁移都应满足：

```text
旧实现仍可运行
新实现有独立测试
读写结果可对比
支持数据回填
出现问题可以切回旧 backend
```

---

## 3. 推荐长期架构

```text
┌─────────────────────────────────────────────────────────┐
│ 用户入口                                                 │
│ CLI / FastAPI / Web UI                                  │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│ AutoAD Application / Domain Layer                       │
│ Input Intake / Reader / Clarifier / Idea / Planner      │
│ Approval / Runner / Metrics / Validity / Report         │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│ AutoAD Core                                              │
│ run lifecycle / schema / rules / approvals / events     │
│ stopping conditions / scientific validity               │
└───────────────┬───────────────────┬─────────────────────┘
                │                   │
       ┌────────▼────────┐ ┌────────▼────────┐
       │ Metadata Store  │ │ Artifact Store  │
       │ SQLite/Postgres │ │ Local/S3/MinIO  │
       └────────┬────────┘ └────────┬────────┘
                │                   │
       ┌────────▼───────────────────▼────────┐
       │ Workflow Runtime                    │
       │ Python Controller → Temporal 等     │
       └────────┬────────────────────────────┘
                │
       ┌────────▼────────────────────────────┐
       │ Harness / Workers                   │
       │ Deep Agents / Reader / Runner / GPU │
       └─────────────────────────────────────┘
```

核心边界：

```text
数据库保存“状态和索引”
对象存储保存“文件和大对象”
Workflow Runtime 保存“执行进度和恢复语义”
AutoAD Core 保存“领域规则和流程语义”
Harness 保存“具体智能执行能力”
```

---

## 4. 何时开始迁移

不要仅因为“文件系统不够高级”就迁移。

建议在完成以下真实闭环后启动工程化迁移：

```text
真实论文/材料输入
→ 真实 Paper/Repository Reader
→ 基于证据的 Intent Clarifier
→ 至少一个真实 idea
→ 迁移判断
→ 实验计划
→ patch plan 或半自动 patch
→ 固定 benchmark/smoke
→ 真实日志和指标
→ 报告
```

至少需要有 3–5 个可重复 demo run，才能知道：

- 哪些元数据最常查询；
- 哪些 artifact 最大；
- 哪些任务耗时最长；
- 哪些步骤最容易失败；
- 哪些状态需要恢复；
- 哪些字段值得进入数据库。

### 4.1 启动数据库迁移的触发条件

满足任意两项即可启动：

```text
run 数量超过 100
需要按状态、时间、baseline、dataset 查询 run
需要统计失败率和阶段耗时
多个进程同时读写运行状态
UI 需要快速分页展示历史任务
events.jsonl 读取开始成为性能瓶颈
```

### 4.2 启动对象存储迁移的触发条件

满足任意一项即可启动：

```text
artifact 总量超过本地磁盘可接受范围
需要跨机器共享论文、日志、checkpoint、图像和模型
Runner 与 Web/API 不在同一台机器
需要生命周期管理、版本化或归档
需要支持大文件断点上传
```

### 4.3 启动 Workflow Runtime 的触发条件

满足任意两项即可启动：

```text
单次实验持续数十分钟或数小时
需要等待人工审批后继续
进程重启后需要从原 stage 恢复
需要自动 retry、timeout、cancel
需要并行运行多个实验分支
需要跨机器或 GPU worker 调度
```

---

## 5. 元数据迁移到数据库

## 5.1 哪些数据属于元数据

建议进入数据库：

```text
Run 基本信息
当前状态
当前 stage
创建/更新时间
用户/项目归属
任务类型
baseline / dataset / category
审批状态
停止原因
失败类型
artifact 索引
stage 执行记录
事件索引
模型调用统计
资源和成本统计
实验指标摘要
```

不建议直接存入关系数据库：

```text
大型 PDF
原始 repo
checkpoint
完整 stdout/stderr
图片和可视化
大段模型上下文
大型 JSON 结果
```

这些应保存在对象存储，只在数据库保存 URI、hash、size 和类型。

---

## 5.2 推荐最小数据模型

### `runs`

```text
id
run_id
project_id
status
current_stage
task_type
created_at
updated_at
started_at
finished_at
stop_reason
error_type
error_message
metadata_json
```

### `stages`

```text
id
run_id
stage_name
status
attempt
backend
started_at
finished_at
error_type
error_message
metadata_json
```

### `artifacts`

```text
id
run_id
stage_name
artifact_name
artifact_type
storage_backend
storage_uri
sha256
size_bytes
schema_version
created_at
metadata_json
```

### `events`

```text
id
run_id
event_type
stage_name
occurred_at
payload_json
```

### `approvals`

```text
id
run_id
approval_type
status
requested_at
decided_at
decided_by
comment
payload_json
```

### `model_calls`

```text
id
run_id
stage_name
backend
model
latency_ms
input_tokens
output_tokens
cache_hit_tokens
estimated_cost
created_at
metadata_json
```

第一版数据库不必一次实现全部表。推荐从：

```text
runs
stages
artifacts
events
```

四张表开始。

---

## 5.3 先定义 Repository 接口

在引入数据库之前，先把业务层与具体存储隔离。

建议接口：

```python
class RunRepository(Protocol):
    def create_run(self, run: RunRecord) -> None: ...
    def get_run(self, run_id: str) -> RunRecord: ...
    def update_status(self, run_id: str, status: str) -> None: ...
    def list_runs(self, query: RunQuery) -> list[RunRecord]: ...


class StageRepository(Protocol):
    def start_stage(self, record: StageRecord) -> None: ...
    def complete_stage(self, record: StageRecord) -> None: ...
    def fail_stage(self, record: StageRecord) -> None: ...


class ArtifactRepository(Protocol):
    def register(self, artifact: ArtifactRecord) -> None: ...
    def get(self, run_id: str, artifact_name: str) -> ArtifactRecord: ...


class EventRepository(Protocol):
    def append(self, event: EventRecord) -> None: ...
    def list_events(self, run_id: str) -> list[EventRecord]: ...
```

业务模块只依赖接口：

```text
PipelineController
InputIntake
Reader
Runner
Approval
Reporter
```

不能直接依赖 SQLAlchemy session 或具体数据库表。

---

## 5.4 SQLite 到 PostgreSQL 的顺序

### 阶段 A：SQLite

目标：

```text
验证数据模型
验证 repository interface
验证迁移脚本
支持本地开发和单机 demo
```

建议：

```text
SQLAlchemy 2.x
Alembic
SQLite
```

### 阶段 B：PostgreSQL

在以下需求出现后迁移：

```text
多人访问
并发 worker
事务要求
复杂筛选
远程部署
权限隔离
```

如果 Repository 边界稳定，迁移主要发生在 infrastructure 层，而不是业务逻辑层。

---

## 5.5 数据迁移策略

推荐六步迁移法：

### Step A：新增数据库但不改变读取

```text
文件仍是事实源
数据库只记录索引
```

### Step B：双写

```text
每次创建 run/stage/artifact/event
同时写文件和数据库
```

### Step C：一致性检查

定期验证：

```text
run 是否缺失
artifact hash 是否一致
event 数量是否一致
stage 状态是否一致
```

### Step D：历史数据回填

提供命令：

```bash
uv run autoad migrate-files-to-db --runs-root runs
```

### Step E：切换读路径

```text
默认从数据库读取元数据
artifact 内容仍从原文件路径读取
```

### Step F：停止文件元数据双写

最终保留：

```text
数据库 = 元数据事实源
对象存储 = artifact 内容事实源
本地 workspace = 执行期间临时工作目录
```

---

## 6. Artifact 迁移到对象存储

## 6.1 哪些 artifact 进入对象存储

适合对象存储：

```text
论文 PDF
解析后的 Markdown/JSON
repo archive
patch.diff
stdout/stderr
metrics.json
checkpoint
模型权重
图像和可视化
最终报告
完整运行快照
```

小型 metadata 仍可保存在数据库。

---

## 6.2 统一 ArtifactStore 接口

建议将当前 `ArtifactStore` 演进为 backend interface：

```python
class ArtifactBackend(Protocol):
    def put_bytes(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> ArtifactLocation: ...

    def get_bytes(self, *, key: str) -> bytes: ...

    def exists(self, *, key: str) -> bool: ...

    def delete(self, *, key: str) -> None: ...

    def list_prefix(self, *, prefix: str) -> list[ArtifactLocation]: ...
```

实现：

```text
LocalFilesystemArtifactBackend
S3ArtifactBackend
MinIOArtifactBackend
```

上层继续提供：

```text
write_json
read_json
write_yaml
read_yaml
write_text
read_text
```

这样 schema 校验与存储 backend 解耦。

---

## 6.3 推荐对象 key 设计

```text
projects/{project_id}/runs/{run_id}/inputs/input_task.yaml
projects/{project_id}/runs/{run_id}/sources/paper_main.pdf
projects/{project_id}/runs/{run_id}/summaries/paper_summary.json
projects/{project_id}/runs/{run_id}/plans/experiment_plan.json
projects/{project_id}/runs/{run_id}/patches/patch.diff
projects/{project_id}/runs/{run_id}/execution/stdout.log
projects/{project_id}/runs/{run_id}/execution/stderr.log
projects/{project_id}/runs/{run_id}/metrics/metrics.json
projects/{project_id}/runs/{run_id}/reports/final_report.md
```

不要依赖用户提供的原始文件名直接组成 key，必须进行规范化。

---

## 6.4 Artifact 完整性

每个 artifact 建议记录：

```text
sha256
size_bytes
content_type
schema_version
created_at
producer_stage
storage_uri
```

读取时：

```text
校验对象存在
校验 size
必要时校验 hash
按 schema_version 解析
```

大型 artifact 可以只在关键节点校验 hash，避免频繁计算。

---

## 6.5 对象存储迁移步骤

```text
1. 抽象 ArtifactBackend
2. 保持 LocalFilesystemArtifactBackend 为默认
3. 增加 MinIO/S3 backend
4. 测试同一 artifact 在两个 backend 的一致性
5. 开启可配置双写
6. 回填历史 artifact
7. 数据库 storage_uri 指向对象存储
8. 本地文件只保留运行时缓存
```

本地开发仍可继续使用文件 backend，不强制所有开发者运行 MinIO。

---

## 7. 长任务迁移到 Workflow Runtime

## 7.1 Workflow Runtime 负责什么

Workflow Runtime 解决：

```text
任务中断恢复
自动 retry
timeout
cancel
人工审批等待
定时和延迟
并行分支
跨 worker 调度
执行历史
```

它不负责：

```text
定义科研有效性
决定什么是合法 artifact
决定异常检测实验协议
生成论文理解
生成科研 idea
```

这些仍属于 AutoAD Core 和具体 Harness。

---

## 7.2 先保持 Python PipelineController

当前阶段继续使用：

```text
Python PipelineController
```

直到出现真实长任务需求。

不要因为 Temporal/LangGraph 功能强，就提前重写现有流程。

---

## 7.3 Workflow 抽象建议

业务层定义：

```python
class WorkflowRuntime(Protocol):
    def start_run(
        self,
        *,
        run_id: str,
        workflow_name: str,
        input: dict[str, object],
    ) -> WorkflowHandle: ...

    def signal(
        self,
        *,
        run_id: str,
        signal_name: str,
        payload: dict[str, object],
    ) -> None: ...

    def cancel(self, *, run_id: str) -> None: ...

    def get_status(self, *, run_id: str) -> WorkflowStatus: ...
```

实现可以是：

```text
InProcessWorkflowRuntime
TemporalWorkflowRuntime
其他 runtime adapter
```

---

## 7.4 Temporal 接入建议

适合放入 Temporal workflow 的内容：

第一阶段单一 Idea 流程：

```text
创建 run
→ Input Intake
→ Reader
→ Intent Clarifier
→ 等待用户补充或确认
→ 单一 Idea 确认
→ Transfer Judge
→ Planner
→ 等待审批
→ Runner activity
→ Metrics
→ Validity
→ Report
```

数据库与多 Agent 能力成熟后，再扩展为：

```text
→ Multi-Agent Idea Generation
→ 候选 Idea 持久化
→ 等待用户选择
→ 单线或并行实验分支
→ Reflection
→ 下一轮或停止
```

需要注意：

- Workflow 必须是确定性的；
- LLM、文件、数据库、GPU、网络调用应放在 Activity 中；
- 审批通过 Signal 恢复；
- 重试策略应按 stage 区分；
- GPU 训练不能无脑自动重试；
- artifact 和数据库仍是业务事实源，Temporal history 不是领域事实源。

---

## 7.5 LangGraph 与 Temporal 的选择

### LangGraph 更适合

```text
Agent 状态图
模型驱动的条件分支
短到中等时长的智能任务
需要与 LangChain/Deep Agents 深度整合
```

### Temporal 更适合

```text
小时级或天级任务
等待人工审批
强恢复需求
跨机器 worker
可靠 retry 和 timeout
生产任务队列
```

可能的组合：

```text
Temporal
  负责整个科研 run 的长周期可靠执行

Deep Agents / LangGraph
  负责某一个智能 stage 内部的 agent loop
```

不要让两者同时控制同一层级的流程状态。

---

## 8. 事件与消息系统

## 8.1 当前阶段

继续使用：

```text
events.jsonl
```

用于本地审计和简单调试。

## 8.2 数据库阶段

`events` 表成为主要查询入口，JSONL 可继续作为：

```text
本地调试副本
导出格式
完整运行归档
```

## 8.3 是否需要消息队列

暂时不需要立即引入 Kafka/RabbitMQ。

在出现以下需求时再考虑：

```text
多个 worker 消费任务
大量实时 UI 更新
事件驱动通知
跨服务解耦
高吞吐运行
```

前期 Temporal task queue 或数据库任务表通常足够。

---

## 9. 可观测性演进

## 9.1 当前阶段

继续记录：

```text
events.jsonl
llm_calls.jsonl
stdout.log
stderr.log
```

## 9.2 下一阶段

数据库增加：

```text
stage latency
attempt count
failure type
token usage
cache hit
estimated cost
GPU time
```

## 9.3 生产阶段

再接：

```text
OpenTelemetry
Prometheus
Grafana
集中日志
错误告警
```

需要统一传播：

```text
trace_id
run_id
stage_name
attempt
worker_id
model
```

---

## 10. 安全与权限演进

### 当前

```text
路径白名单
ArtifactStore 白名单
命令白名单
人工审批
```

### 数据库阶段

增加：

```text
project_id
owner_id
created_by
approval actor
权限状态
数据保留策略
```

### 对象存储阶段

增加：

```text
私有 bucket
短期签名 URL
服务账号最小权限
artifact 生命周期规则
敏感文件加密
```

### Workflow 阶段

增加：

```text
worker 身份
任务级 secret
activity 权限
取消和审批权限
```

任何 API Key、数据库密码、对象存储凭证都不得进入 artifact 或 events。

---

## 11. Schema 版本与迁移

所有长期 artifact 和数据库记录都应逐步增加：

```text
schema_version
```

例如：

```json
{
  "schema_version": "1.0",
  "run_id": "run_demo",
  "status": "success"
}
```

迁移策略：

```text
读取端支持当前版本和最近一个旧版本
写入端只写最新版本
数据库使用 Alembic 管 schema
artifact 使用显式 converter 管版本
迁移必须有 fixture 和回归测试
```

不要依赖“字段缺失时默认补齐”长期掩盖版本问题。

---

## 12. 推荐分阶段路线

## 阶段 A：单一 Idea 的真实纵向闭环

目标：

```text
输入路径 A：
用户给定明确代码修改方案

或输入路径 B：
一篇固定论文 PDF
→ 提取并确认一个主要迁移 Idea
```

随后固定为：

```text
一个 repo
+ 一个 PatchCore baseline
+ 一个 MVTec AD 类别
+ 一个已确认 Idea
+ 一个真实实验结果
```

当前基础设施保持不变。

第一阶段不得自动扩展成多个候选 Idea，也不得启动多 Agent 讨论。

验收：

```text
完整 artifact 链存在
一个 run 只推进一个已确认 Idea
用户可以看到并确认 Idea 与修改边界
实验可重复
指标来自真实执行
报告可追溯
```

---

## 阶段 B：存储接口抽象

新增：

```text
RunRepository
StageRepository
ArtifactRepository
EventRepository
ArtifactBackend
```

默认实现仍为本地文件。

验收：

```text
现有测试不变
业务层不直接访问文件路径
Local backend 通过全部测试
```

---

## 阶段 C：SQLite 元数据

新增：

```text
SQLAlchemy
Alembic
runs/stages/artifacts/events 表
```

运行模式：

```text
文件内容保留
数据库记录元数据
双写并检查一致性
```

验收：

```text
可查询 run 列表
可按 status/stage 筛选
可从 files 回填 DB
双写一致
```

---

## 阶段 D：对象存储

新增：

```text
MinIO 或 S3 backend
artifact URI
hash/size/content_type
```

验收：

```text
同一 artifact 可在 local/S3 backend 互换
worker 可跨机器读取
对象丢失能被检测
```

---

## 阶段 E：PostgreSQL

将 SQLite 替换为 PostgreSQL。

验收：

```text
并发 worker 正常
事务一致
迁移脚本可重复
本地仍支持 SQLite 开发模式
```

---

## 阶段 F：Workflow Runtime

先实现：

```text
InProcessWorkflowRuntime
```

再增加：

```text
TemporalWorkflowRuntime
```

验收：

```text
流程中断可恢复
审批后可继续
stage retry/timeout/cancel 可配置
Runner 可由独立 worker 执行
```

---

## 阶段 G：生产能力

按真实需求增加：

```text
FastAPI
Web UI
用户和项目权限
任务队列
OpenTelemetry
告警
多租户
数据生命周期
成本控制
```

---

## 13. 建议的后续 Step 编号

在当前 Step 2.x 地基完成后，不要立刻全部实施。建议先完成单一 Idea 的真实闭环，再做数据库，最后才加入多 Agent。

```text
Step 2.12：Paper / Repository Reader contracts
Step 2.13：Evidence-based Intent Clarifier
Step 2.14：SingleIdea schema + DirectIdea path
Step 2.15：单一 Idea 最小控制流
  用户明确实现方案
  或固定论文提取一个主要 Idea
  用户确认后进入 Planner

Step 3.x：单一 Idea 真实纵向闭环
  真实 Reader
  真实 Clarifier
  真实 Transfer/Planner
  半自动 patch
  Runner
  Metrics
  Validity
  Report

Step 4.1：Storage interfaces
Step 4.2：SQLite metadata repository
Step 4.3：Files → DB backfill and dual-write
Step 4.4：Run / Stage / Artifact / Event 查询接口

Step 5.1：Idea metadata tables
Step 5.2：Multi-Agent Ideation protocol
Step 5.3：AutoGen / CrewAI / Deep Agents 对照 spike
Step 5.4：候选 Idea 去重、排序和用户选择
Step 5.5：多分支低成本 smoke 实验

Step 6.1：Artifact backend abstraction
Step 6.2：MinIO/S3 artifact backend
Step 6.3：PostgreSQL migration（出现并发需求时）
Step 6.4：In-process workflow abstraction
Step 6.5：Temporal spike
Step 6.6：Approval/resume workflow
Step 6.7：Observability and production hardening
```

其中数据库后的多 Agent 阶段，至少需要以下数据结构：

```text
ideas
idea_evidence
idea_discussions
idea_selections
idea_experiment_links
```

这样每个候选 Idea 才能关联：

```text
来源证据
生成后端
讨论记录
用户选择
对应实验
最终指标
失败原因
```

编号可以调整，但“单一 Idea 闭环 → 数据库 → 多 Agent”的顺序不建议反转。

---

## 14. 每次改造的统一验收清单

每个工程化步骤都必须回答：

```text
1. 解决了哪个已经出现的真实瓶颈？
2. 是否保留旧 backend？
3. 是否有迁移和回滚方案？
4. 是否有 fixture 和回归测试？
5. 是否破坏现有 CLI / API？
6. 是否影响 artifact 可追溯性？
7. 是否引入新的单点故障？
8. 是否增加了不必要的运维成本？
9. 是否有数据一致性检查？
10. GitHub Actions 是否通过？
```

如果不能明确回答第 1 项，则暂缓实现。

---

## 15. 当前最重要的约束

接下来仍应坚持：

```text
先完成 Step 2.12–2.15
但只实现单一 Idea 路线
→ 停止继续扩充通用地基
→ 跑通一个真实纵向闭环
→ 再迁移元数据到数据库
→ 数据库稳定后才实现多 Agent Ideation
→ 根据真实瓶颈继续对象存储和 Workflow Runtime
```

当前只允许：

```text
用户给定明确实现方案
或
固定论文 PDF → 提取一个主要 Idea → 用户确认
```

当前禁止提前实现：

```text
多 Agent 讨论
候选 Idea 投票
多分支并行
自动研究方向扩展
```

也不要现在立即引入：

```text
PostgreSQL
MinIO
Temporal
Redis
Kafka
Kubernetes
复杂微服务
```

当前要做的是为未来迁移保留清晰边界，而不是提前承担全部生产基础设施。

---

## 16. 多 Agent 的延后策略

多 Agent 不是当前主流程的前置条件。

### 16.1 延后的原因

在只有文件目录、缺少统一元数据查询时，多 Agent 容易产生：

```text
候选 Idea 无法稳定编号
同一 Idea 被重复生成
讨论过程和最终实验脱节
无法查询某个候选对应的实验结果
用户选择难以形成可靠状态
并行分支容易覆盖 artifact
失败经验不能被复用
```

因此，先完成数据库不是为了追求“更企业级”，而是为了给多 Agent 提供可靠的共享状态和历史证据。

### 16.2 数据库前的替代方案

数据库接入前，只实现：

```text
SingleIdea
DirectIdeaBackend
FixedPaperSingleIdeaBackend
UserIdeaConfirmation
```

建议 artifact：

```text
single_idea.json
idea_confirmation.json
```

`single_idea.json` 至少包含：

```text
idea_id
source
title
description
insertion_point
implementation_constraints
scientific_risks
minimum_experiment
evidence
```

但一个 run 只允许一个 active Idea。

### 16.3 数据库后的多 Agent

数据库稳定后，才增加：

```text
MultiAgentIdeaBackend
多个 IdeaCandidate
讨论与反驳
候选去重
用户选择
Idea → Experiment 的关联
历史结果反馈
```

多 Agent 的验收重点不是“讨论看起来热闹”，而是：

```text
是否产生非重复候选
是否引用真实证据
是否能被用户理解
是否能关联后续实验
是否能从失败实验中学习
是否比单 Agent/单 Idea 路线带来可测收益
```

---

## 17. 最终架构原则

> **AutoAD Core 定义科研流程和规则，数据库保存可查询的运行元数据，对象存储保存可复用的 artifact，Workflow Runtime 负责可靠执行和恢复，Harness Backend 负责智能任务，Runner Worker 负责受控实验。**

最终应实现：

```text
领域逻辑不依赖具体数据库
artifact 逻辑不依赖本地文件系统
流程语义不依赖具体 workflow runtime
智能 stage 不依赖单一 Agent 框架
实验结论必须依赖真实 artifact 和指标
```

这条路线的目的不是追求“技术栈先进”，而是确保 AutoAD 从比赛 MVP 演进为长期可维护产品时，不需要推倒重来。
