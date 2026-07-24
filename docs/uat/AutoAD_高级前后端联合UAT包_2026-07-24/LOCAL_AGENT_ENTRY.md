# 本地 AI 入口

本目录是当前高级前后端联合 UAT 的正式、AI 可读资料包。

## 必须先做

1. 核对当前 `main`、标签、工作树和正在运行的其他测试。
2. 读取仓库当前 API、Schema、测试脚本和前端组件，禁止根据旧字段猜测。
3. 使用同一个独立修复分支，并为本轮测试使用独立的 run root、端口、浏览器 profile、worktree 和数据目录。
4. 依次读取：
   - `01_高级联合UAT总方案.md`
   - `02_修复Agent提示词.md`
   - `PACKAGE_CORE.md`
   - `08_不可代下载清单.md`
5. 需要可执行 CPU 夹具时运行：

```bash
python docs/uat/AutoAD_高级前后端联合UAT包_2026-07-24/materialize_fixture.py
```

生成目录：

`docs/uat/AutoAD_高级前后端联合UAT包_2026-07-24/generated/01_spike_ad_two_stage/`

## 关键边界

- 不停止或修改其他测试的服务、Worker、共享 runs、GPU 工作区和浏览器 profile。
- 不直接修改 `main`，不创建、移动或覆盖 `v0.9.0-rc1`。
- 不按 fixture 名称、固定指标、固定路径、固定中文文案或单一异常字符串写特殊分支。
- 不为通过测试而放宽 EvaluationContract、protected paths、B_dev/B_test、确认、Promotion 或证据要求。
- 修复必须落在结构化合同、状态、权限、幂等、artifact hash、可比性和恢复不变量上。
