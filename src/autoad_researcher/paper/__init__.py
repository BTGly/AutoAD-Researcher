"""
论文理解模块 (Paper Reader)。

高精度从 PDF 中提取结构化信息，为后续实验迁移服务。
主力引擎：MinerU（公式→LaTeX、表格→HTML、109语言 OCR）。
辅助引擎：MarkItDown（非 PDF 格式快速转换）。

输入：PDF / 摘要 / arXiv 链接 / GitHub 链接
输出：结构化论文信息（task_type, core_idea, model_components, metrics 等）
"""
