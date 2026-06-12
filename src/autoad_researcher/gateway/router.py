"""模型网关路由——根据任务类型选择轻量/强模型。"""


class ModelRouter:
    """模型路由器（占位，MVP 阶段实现）。"""

    def route(self, task_type: str) -> str:
        """根据任务类型返回模型 ID。"""
        # TODO: 从 config 读取路由表
        return "default"
