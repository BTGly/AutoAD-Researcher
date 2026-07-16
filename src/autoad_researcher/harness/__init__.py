"""Agent 执行内核抽象层。

提供统一的 AgentHarness 接口，供 DeepAgents 原型后端使用。
"""

from autoad_researcher.harness.base import AgentHarness
from autoad_researcher.harness.deepagents_backend import DeepAgentsHarness

__all__ = ["AgentHarness", "DeepAgentsHarness"]
