"""入口：根据输入类型路由到对应 pipeline。"""

from autoad_researcher.pipeline.orchestrator import Orchestrator


def main():
    """CLI 入口（占位，MVP 阶段实现）。"""
    orch = Orchestrator()
    # TODO: 解析 CLI args / 启动 Gradio UI
    orch.run()


if __name__ == "__main__":
    main()
