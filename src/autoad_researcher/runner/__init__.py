"""
实验运行模块 (Runner Agent)。

在受控环境中运行实验。原则：
- 只运行白名单命令
- 不覆盖旧实验结果
- 每次运行生成独立实验目录
- 保存 stdout / stderr / config / metrics
"""
