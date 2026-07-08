"""Compatibility imports for task workspace profile helpers.

The legacy UI entrypoints were removed, but older tests and internal helpers still import
this module path. The implementation now lives under
``autoad_researcher.task_workspace``.
"""

from autoad_researcher.task_workspace.task_profile import *  # noqa: F401,F403
