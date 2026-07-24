# 资料包内容与验证

完整重建后的 ZIP 内含 37 个文件、43 个 ZIP 成员（含目录项）：

- `README.md`、执行检查清单、结果模板；
- `config.example.env`；
- 冻结仓库提交、来源清单、评价合同、MPDD 类别划分、Idea 与 Agent 提示；
- 双 GPU baseline/candidate 并行训练脚本；
- MPDD B_dev/B_test 类别视图生成器；
- preflight、protected hashes、人工批准、指标解析与三 seed 汇总工具；
- 论文下载链接和本地 PDF SHA256，但不重分发 PDF；
- 数据集许可边界。

## GitHub 发布修复

提交 `f657a08e5286e384532ac5643e55b81c2765aade` 中的二进制 ZIP 被截断，已经从分支删除，不得继续使用。

当前分支通过 `release_b64/` 中的 13 个文本分片和 `rebuild_release_zip.py` 确定性重建 ZIP：

```bash
python3 rebuild_release_zip.py
```

重建结果必须同时满足：

- 大小：`34,648` bytes；
- SHA256：`95a3b970b5f29d52026aba178e3cca9ae667159e8e520a650db22349cb239077`；
- central directory 可读；
- 43 个 ZIP 成员可枚举；
- 所有成员 CRC 检查通过。

逐分片 Git blob SHA 见 `RELEASE_INTEGRITY.md`。

## 已完成的本地验证

- 所有 Shell 脚本通过 `bash -n`；
- 所有 Python 工具通过 `py_compile`；
- synthetic MPDD view test 通过；
- metric parser synthetic test 通过；
- 包内 `SHA256SUMS` 全部通过；
- 按 GitHub 当前分片布局重建后，SHA256、central directory 和全部 CRC 校验通过。

这些验证不等于真实 GPU 训练已经执行。真实结果必须来自用户 GPU 主机上的 checkpoint、日志和 metrics artifacts。
