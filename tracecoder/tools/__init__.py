"""工具定义、注册和执行入口。"""

from tracecoder.tools.builtin import create_builtin_registry
from tracecoder.tools.apply_patch import create_apply_patch_tool
from tracecoder.tools.ask_user import create_ask_user_tool
from tracecoder.tools.delete import create_delete_tool
from tracecoder.tools.diagnostics import create_diagnostics_tool
from tracecoder.tools.edit import create_edit_tool
from tracecoder.tools.fetch import create_fetch_tool
from tracecoder.tools.git_diff import create_git_diff_tool
from tracecoder.tools.git_log import create_git_log_tool
from tracecoder.tools.git_status import create_git_status_tool
from tracecoder.tools.glob import create_glob_tool
from tracecoder.tools.grep import create_grep_tool
from tracecoder.tools.ls import create_ls_tool
from tracecoder.tools.python_exec import create_python_exec_tool
from tracecoder.tools.processes import create_process_tools
from tracecoder.tools.read_multi import create_read_multi_tool
from tracecoder.tools.shell import create_shell_tool
from tracecoder.tools.task_boundary import create_task_boundary_tool
from tracecoder.tools.registry import ToolRegistry
from tracecoder.tools.think import create_think_tool
from tracecoder.tools.tree import create_tree_tool
from tracecoder.tools.types import Tool, ToolExecutor, ToolResult
from tracecoder.tools.view import create_view_tool
from tracecoder.tools.web_search import create_web_search_tool
from tracecoder.tools.write import create_write_tool

__all__ = [
    "Tool",
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
    "create_apply_patch_tool",
    "create_ask_user_tool",
    "create_builtin_registry",
    "create_delete_tool",
    "create_delegate_tool",
    "create_diagnostics_tool",
    "create_edit_tool",
    "create_fetch_tool",
    "create_git_diff_tool",
    "create_git_log_tool",
    "create_git_status_tool",
    "create_glob_tool",
    "create_grep_tool",
    "create_ls_tool",
    "create_python_exec_tool",
    "create_process_tools",
    "create_read_multi_tool",
    "create_shell_tool",
    "create_task_boundary_tool",
    "create_think_tool",
    "create_task_create_tool",
    "create_task_update_tool",
    "create_task_revise_tool",
    "create_task_list_tool",
    "create_tree_tool",
    "create_view_tool",
    "create_web_search_tool",
    "create_write_tool",
]


def __getattr__(name: str):
    """Lazily expose tools that depend on agent-layer runtime objects.

    Importing ``tracecoder.tools`` is part of low-level context/writer startup.
    ``delegate`` depends on ``tracecoder.agent.subagent`` and therefore cannot be
    imported eagerly here without creating a package-level cycle.
    """

    if name == "create_delegate_tool":
        from tracecoder.tools.delegate import create_delegate_tool

        return create_delegate_tool
    task_factories = {
        "create_task_create_tool": ("tracecoder.tools.task_create", "create_task_create_tool"),
        "create_task_update_tool": ("tracecoder.tools.task_update", "create_task_update_tool"),
        "create_task_revise_tool": ("tracecoder.tools.task_revise", "create_task_revise_tool"),
        "create_task_list_tool": ("tracecoder.tools.task_list", "create_task_list_tool"),
    }
    target = task_factories.get(name)
    if target is not None:
        from importlib import import_module

        return getattr(import_module(target[0]), target[1])
    raise AttributeError(name)
