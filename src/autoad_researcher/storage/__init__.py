"""
状态管理模块。

系统状态不能只保存在对话里，必须落盘。
- SQLite: 实验元数据、运行记录
- JSONL: 结构化中间产物（迁移判断、实验计划、分析报告）
- 文件系统: 实验目录（config / patch / stdout / stderr / metrics）
"""
