# AutoAD 高级联合 UAT 测试修复 Agent 提示词

请在 AutoAD-Researcher 本地仓库中创建并始终使用同一个独立 GitHub 修复分支，持续执行 `docs/uat/AutoAD_高级前后端联合UAT包_2026-07-24/`。完整复现、记录、修复并复验发现的问题，直到正式验收包的 P0/P1 全部关闭、关键 P2 有明确结论，且真实产品行为符合验收预期。

必须遵守：

1. 先读取仓库当前 `main`、现有标签、测试脚本、API/Schema、前端组件和 UAT 记录，确定唯一验收基线；不要根据旧问题清单或字段名猜测当前实现。
2. 所有问题记录、产品修复、回归测试、必要的正式夹具修正和验收结论都提交到同一修复分支。不要直接修改 `main`，不要创建、移动或覆盖 `v0.9.0-rc1`。
3. 有其他测试也在进行，不要相互影响。使用独立 run root、端口、浏览器 profile、worktree 和测试数据目录；不得停止他人的服务或 Worker、修改共享 runs、污染共享 GPU 或仓库工作区。
4. 多复用参考成熟项目的设计，`/root/autodl-tmp/AI4S/references/coding-agents`；`/root/autodl-tmp/AI4S/references/research-automation`；不要死板不要过度工程化，不要死关键词死规则；有其他测试也在进行，不要相互影响。
5. 参考成熟项目时先读取精确源码、固定版本和许可证，只吸收适合当前项目的机制。优先复用现有 Store、Artifact、Job、Worker、Projection、权限和测试工具，不新增第二套状态源、通用调度框架或无必要抽象。
6. 修复必须落在结构化合同和系统不变量上：类型、状态、权限、持久化事实、幂等键、artifact hash、可比性和恢复。禁止按 fixture 名称、固定指标、固定路径、固定中文文案或单个异常字符串写特殊分支。
7. 对 Adapter 绑定、指标读取、材料解析、失败恢复和领域边界，优先增加变形测试、属性测试、反例测试和近邻合法用例。不同语义应由显式 typed contract 表达，不得通过 argv 内容或自然语言片段猜测。
8. 不得为了通过测试放宽 protected paths、EvaluationContract、B_dev/B_test、用户确认、Promotion 或证据要求；不得伪造 Idea、Attempt、OutcomeCard、ScientificAssessment、Report、Champion 或运行结果。
9. 每个修复必须有稳定回归测试，并覆盖重复请求、同键不同内容、服务重启、Worker 丢失、超时、指标缺失、部分 artifact 缺失、旧响应覆盖新 run、双标签页和网络响应丢失。
10. 前端验收不能只跑 build。逐流程检查用户是否理解状态和下一步、错误是否保留输入、文件和证据是否可读，以及键盘、移动端、200% 缩放、焦点、status/alert 是否正确；普通用户不得只看到 raw enum、内部路径或堆栈。
11. 每修复一组相关问题后，先跑专项测试，再运行项目规定的全量后端门禁、前端 lint/build、普通 Playwright、隔离 full-stack Playwright 和对应真人 UAT。部分通过不得宣布完成。
12. 保持工作树干净，按逻辑拆分提交。每轮推送到同一远程分支并核对 GitHub Actions；CI 失败需记录真实根因，不得只重跑掩盖 flaky。
13. 修复 Agent 不得看到最终 unseen holdout。最终由独立测试者生成不同工程形态的仓库；若只能通过新增 fixture 特例，验收失败。
14. 只有正式验收包不依赖临时派生事实、关键 artifact 完整、报告证据可追溯、真实用户流程能走到收尾，且 P0/P1 清零时才宣布完成。

每个问题至少记录：

- 精确基线 commit、分支和环境；
- 复现步骤；
- 预期与实际；
- UI 截图、DOM、API、artifact 和 log 证据；
- 根因；
- 修复范围；
- 新增回归测试；
- 专项、全量、前端和真人复验结果；
- 泛化说明：修复保护了哪个系统不变量，覆盖哪些变形，哪些边界仍不支持。

最终输出：

- 修复分支名和最终 commit SHA；
- 问题列表、严重度与根因；
- 主要代码、测试和正式夹具变更；
- 每轮测试和 GitHub Actions 结果；
- 五条 UAT 与 unseen holdout 的最终结论及证据路径；
- 尚存限制或未执行项目；
- 是否具备创建新 RC 标签的条件。

未经用户明确要求，不要合并 `main`，不要创建或移动发布标签。
