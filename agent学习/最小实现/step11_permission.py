"""第 11 步：副作用工具先 ASK，用户回答后才执行。"""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from tracecoder.permissions.manager import PermissionManager
from tracecoder.permissions.policy import DefaultPermissionPolicy
from tracecoder.permissions.types import PermissionAction, PermissionMode
from tracecoder.providers.types import ToolDefinition
from tracecoder.tools.permission_registry import PermissionAwareToolRegistry
from tracecoder.tools.registry import ToolRegistry
from tracecoder.tools.types import Tool, ToolPermissionSpec, ToolResult
from support import Trace


def make_write_tool(root: Path):
    def write(path: str, content: str) -> ToolResult:
        target = root / path
        target.write_text(content, encoding="utf-8")
        return ToolResult(name="write_file", ok=True, content=f"已写入 {path}")

    return Tool(
        definition=ToolDefinition(
            name="write_file",
            description="写入项目文件",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        ),
        executor=write,
        permission=ToolPermissionSpec(action=PermissionAction.WRITE_PATH, target_arg="path"),
    )


def main():
    with TemporaryDirectory(prefix="tracecoder-permission-") as directory:
        root = Path(directory)
        manager = PermissionManager(
            policy=DefaultPermissionPolicy(project_root=root),
            mode=PermissionMode.STANDARD,
        )
        registry = PermissionAwareToolRegistry(
            ToolRegistry([make_write_tool(root)]),
            manager,
        )
        trace = Trace(True)
        arguments = {"path": "notes.txt", "content": "hello"}
        trace.emit("TOOL_CALL_RECEIVED", name="write_file", arguments=arguments)
        preflight = registry.preflight("write_file", arguments)
        assert preflight is not None
        tool, normalized, request, decision = preflight
        trace.emit("PERMISSION_DECISION", decision=decision.kind.value, reason=decision.reason,
                   request={"id": request.id, "action": request.action.value, "target": request.target})
        if decision.kind.value == "ask":
            confirmation = manager.build_confirmation(request)
            trace.emit("WAITING_FOR_USER", request=asdict(confirmation))
            answer = "allow_once"
            resolved = manager.resolve_confirmation(request, answer)
            trace.emit("USER_INPUT", answer=answer, resolved=resolved.kind.value)
            if resolved.kind.value != "allow":
                trace.emit("DENIED", reason=resolved.reason)
                return
        result = registry.execute_without_permission_check(tool.name, normalized)
        trace.emit("TOOL_RESULT", result=asdict(result), file_exists=(root / "notes.txt").exists())


if __name__ == "__main__":
    main()
