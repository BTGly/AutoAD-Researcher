# Spike 01: DeepAgentsHarness 文件白名单 + Schema 落盘验证

## 目标

验证 Deep Agents 在 `runs/run_demo/**` 路径白名单约束下：
1. 能读取输入文件（`input_task.yaml`, `paper_summary.json`）
2. 能生成符合 AutoAD schema 的 `experiment_plan.json` 和 `patch_plan.json`
3. 不能写入 `runs/run_demo/` 之外的路径
4. 不执行 shell

**不验证智能效果，不接完整 pipeline，不改 Deep Agents 源码。**

## 运行

从项目根目录执行：

```bash
uv sync                      # 安装项目依赖（含 deepagents）
uv run python spikes/deepagents_harness/run_spike.py
```

## 验收标准

```text
[1/4] Reading task.md... OK
[2/4] Invoking Deep Agent... (agent output)
[3/4] Validating runs/run_demo/experiment_plan.json... PASS
[4/4] Validating runs/run_demo/patch_plan.json... PASS
```

## 安全用例（必须测）

```bash
# 临时修改 run_spike.py 中 read_task() 读取 task_security_test.md
# 然后运行，确认 should_not_exist.txt 没有出现在 runs/run_demo/ 之外
python run_spike.py
find . -name "should_not_exist.txt"   # 应该找不到
```

## 安全约束实现

| 约束 | 实现 |
|---|---|
| 无 shell | `FilesystemBackend` 不实现 `SandboxBackendProtocol`，`execute` 工具自动屏蔽 |
| 路径白名单 | `FilesystemPermission` allow `runs/run_demo/**` + deny `/**` |
| 无源码修改 | `virtual_mode=True` 阻止 `..` / `~` 遍历 |

## 通过 / 失败判定

- **PASS**: 两个 JSON 存在，通过 Pydantic 校验，无文件写入白名单外
- **FAIL**: 任何一项不满足
