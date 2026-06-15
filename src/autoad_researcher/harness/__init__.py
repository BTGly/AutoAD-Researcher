"""Agent 执行内核抽象层。

提供统一的 AgentHarness 接口，支持多种后端实现：
- SimplePipelineHarness：稳定、可测试、无 Agent 依赖的确定性执行
- DeepAgentsHarness：基于 Deep Agents 的长程 Agent 执行
"""

from autoad_researcher.harness.base import AgentHarness
from autoad_researcher.harness.simple_pipeline import SimplePipelineHarness
from autoad_researcher.harness.deepagents_backend import DeepAgentsHarness

__all__ = ["AgentHarness", "SimplePipelineHarness", "DeepAgentsHarness"]
