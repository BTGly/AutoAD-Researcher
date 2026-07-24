# 真人执行检查清单

- [ ] `config.env` 全部为真实绝对路径
- [ ] 两张 RTX 4090 均被 `nvidia-smi` 识别
- [ ] baseline commit 为 `3911738c0867544f545a076ad78f3f11d9ecbfdf`
- [ ] oracle commit 为 `506b6bf6355256180b47dc2b667c2a767bd0fca7`
- [ ] candidate worktree commit 已记录且工作树干净
- [ ] MVTec AD 与 MPDD 已生成 `meta.json`
- [ ] MPDD B_dev/B_test 视图已生成
- [ ] 类别交集为空且并集等于 MPDD 六类
- [ ] protected-before hashes 已冻结
- [ ] seed 111 B_dev 真实运行
- [ ] B_dev 未通过时未读取 B_test
- [ ] B_dev 通过后存在人工批准记录
- [ ] seeds 111/222/333 B_test 真实运行
- [ ] stdout/stderr、checkpoint、metrics 和 telemetry 已归档
- [ ] protected-after hashes 与 before 一致
- [ ] 未达到 +2 时报告为未达标，不生成 Champion
