# 当前高级联合 UAT 资料包

当前正式资料目录：

`docs/uat/AutoAD_高级前后端联合UAT包_2026-07-24/`

## 本地 AI 的读取顺序

本地 AI 不会自动知道聊天附件或外部下载文件。启动测试或修复任务时，必须明确要求它先读取本文件，然后依次读取：

1. `LOCAL_AGENT_ENTRY.md`
2. `01_高级联合UAT总方案.md`
3. `02_修复Agent提示词.md`
4. `PACKAGE_CORE.md`
5. `08_不可代下载清单.md`

需要 CPU 测试仓库时，在仓库根目录运行：

```bash
python docs/uat/AutoAD_高级前后端联合UAT包_2026-07-24/materialize_fixture.py
```

脚本会在资料目录下生成 `generated/01_spike_ad_two_stage/`，不会修改产品源码、共享 runs 或其他测试分支。

## 启动提示词最小写法

```text
先读取 docs/uat/CURRENT_ADVANCED_UAT.md，并严格按照其中的顺序读取当前高级联合 UAT 资料。使用独立分支、run root、端口、浏览器 profile 和 worktree，不得影响其他测试。完成前不得合并 main 或移动发布标签。
```

不要只依据聊天记录、旧问题清单、文件名或历史字段猜测当前实现。先读取仓库当前 Schema、测试脚本和资料包，再执行验收。
