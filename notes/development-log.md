# AutoAD-Researcher 开发日志

> 每一次操作都记录：做什么、为什么、结果、遗留问题。

---

## 2026-06-15

### Session 1: DeepAgentsHarness Spike 01 — 文件白名单 + Schema 落盘验证

**目标**: 验证 Deep Agents 能否在 `runs/run_demo/**` 路径白名单内读写，生成符合 AutoAD schema 的 `experiment_plan.json` 和 `patch_plan.json`，不验证智能效果。

**操作**:

1. Clone `langchain-ai/deepagents` v0.6.10 到 `/root/autodl-tmp/repos/deepagents`
   - 创建 symlink `third_party/deepagents -> /root/autodl-tmp/repos/deepagents`
   - 不改源码

2. 建 spike 目录 `spikes/deepagents_harness/`
   - `schema.py` — ExperimentPlan + PatchPlan (Pydantic, extra="allow")
   - `task.md` — 给 Deep Agents 的系统提示词（路径白名单、无 shell）
   - `task_security_test.md` — 安全负用例（尝试越界写入）
   - `run_spike.py` — 主入口: create_deep_agent + FilesystemBackend + FilesystemPermission
   - `runs/run_demo/input_task.yaml` — 示例任务
   - `runs/run_demo/paper_summary.json` — 示例论文摘要

3. 添加并收紧依赖 `deepagents>=0.6.10,<0.7` 到 pyproject.toml
   - `requires-python` 为 `>=3.11`
   - 使用 uv 生成并提交 `uv.lock`

4. 运行 spike
   - **正向用例**: Agent 成功读取输入文件，生成 experiment_plan.json (11 keys) 和 patch_plan.json (7 keys)，Pydantic 校验通过
   - API 走 DeepSeek Anthropic-compatible endpoint (ANTHROPIC_BASE_URL)
   - 模型自动解析为 deepseek-v4-flash
   - 运行过程中产生过 token 统计，但不纳入仓库证据

5. 安全负用例
   - 修改 read_task() 指向 task_security_test.md（要求写入 should_not_exist.txt）
   - FilesystemPermission deny 规则成功拦截，文件未出现在 runs/run_demo/ 之外

6. 建立验证门禁 (Step 0)
   - `scripts/verify.sh` — 检查项目结构、spike 文件、Python 语法、schema 导入、fixture JSON
   - `scripts/verify_and_push.sh` — verify → git commit → git push
   - `.github/workflows/verify.yml` — GitHub Actions，使用 uv + Python 3.11
   - `.gitignore` — 含 runs/ 忽略，spike fixtures 例外

7. 推送到 GitHub
   - `git push --force origin main` (覆盖远程自动生成的空 README)
   - 仓库: https://github.com/BTGly/AutoAD-Researcher

**关键发现**:
- `FilesystemBackend` 不实现 `SandboxBackendProtocol`，`execute` 工具自动屏蔽 — 不需要额外配置即实现"无 shell"
- `FilesystemPermission` 路径必须以 `/` 开头（virtual mode）
- Agent 天然产出嵌套结构，schema 需要 `ConfigDict(extra="allow")` 容错
- 严格 `str` 类型 vs 实际 dict 输出的矛盾留待后续 Prompt Engineering 解决

**遗留问题**:
- Push 后 token 已从 local remote URL 清除，后续 push 需配置 `gh auth login` 或 SSH
- GitHub Actions 已确认通过
- Schema 严格类型校验（`control_group: str` 等）目前只验证 inline 测试数据，真实 Agent 输出会失败 — 需要更严格的 task.md 约束或 post-processing

**下一步**: Step 1 — 连接 AgentHarness 抽象接口，抽取 `src/autoad_researcher/harness/base.py` + `deepagents_backend.py`

---

### Session 2: 项目基础设施完善 — 日志 + 推送机制

**目标**: 确保每次操作都有日志记录和版本回溯能力。

**操作**:

1. 更新 `CLAUDE.md`
   - 新增 MANDATORY WORKFLOW 节：每次改动 → log → verify → commit → push
   - 明确日志格式（date heading，每项包含 what/why/result/leftovers）
   - 明确 push 使用 token 认证（不受交互式限制）

2. 创建 `.env` 文件
   - 含 GITHUB_TOKEN / GITHUB_USER / GITHUB_REPO
   - 已在 .gitignore 中，不提交

3. 更新 `scripts/verify_and_push.sh`
   - 自动从 .env 加载 token
   - push 时可以 token-embedded URL 认证
   - push 完成后立即清除 remote URL 中的 token
   - 保留 fallback 默认 push（无 token 时）

4. 更新 `notes/development-log.md`
   - 补充 Session 1 的最终依赖信息（deepagents>=0.6.10,<0.7, requires-python>=3.11, uv.lock）
   - 追加 Session 2 记录

5. 验证门禁增强
   - verify.sh 新增 `test -f notes/development-log.md` 检查

**关键发现**:
- HTTPS remote 在非交互式 shell 中 `fatal: could not read Username for 'https://github.com'`，必须用 token-embedded URL
- `gh auth setup-git` 设置的 credential helper 在此环境中不生效
- 方案：push 前临时替换 remote URL（含 token），push 后立刻替换回去

**遗留问题**:
- 无

**下一步**: Step 1 — 连接 AgentHarness 抽象接口，抽取 `src/autoad_researcher/harness/base.py` + `deepagents_backend.py`
