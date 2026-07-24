# 资料包内容与验证

完整 ZIP 内含 37 个文件：

- `README.md`、执行检查清单、结果模板；
- `config.example.env`；
- 冻结仓库提交、来源清单、评价合同、MPDD 类别划分、Idea 与 Agent 提示；
- 双 GPU baseline/candidate 并行训练脚本；
- MPDD B_dev/B_test 类别视图生成器；
- preflight、protected hashes、人工批准、指标解析与三 seed 汇总工具；
- 论文下载链接和本地 PDF SHA256，但不重分发 PDF；
- 数据集许可边界。

本地验证结果：

- 所有 Shell 脚本通过 `bash -n`；
- 所有 Python 工具通过 `py_compile`；
- synthetic MPDD view test 通过；
- metric parser synthetic test 通过；
- 包内 `SHA256SUMS` 全部通过。

这些验证不等于真实 GPU 训练已经执行。真实结果必须来自用户 GPU 主机上的 checkpoint、日志和 metrics artifacts。
