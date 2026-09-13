"""三个实验的答案脚本（学习用，可随时删除）。

运行方式：
    .venv\\Scripts\\python -X utf8 learn_chain1_experiments.py
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from tracecoder.permissions.manager import PermissionManager
from tracecoder.permissions.policy import DefaultPermissionPolicy
from tracecoder.permissions.types import PermissionAction, PermissionMode
from tracecoder.providers.types import ToolDefinition
from tracecoder.tools.permission_registry import PermissionAwareToolRegistry
from tracecoder.tools.registry import ToolRegistry
from tracecoder.tools.types import Tool, ToolPermissionSpec, ToolResult


def make_tool(name, action, target_arg):
    def fake_executor(**kwargs):
        return ToolResult(name=name, ok=True, content=f"(假装执行了 {kwargs.get(target_arg)})")

    return Tool(
        definition=ToolDefinition(
            name=name,
            description=f"{name} demo",
            parameters={"type": "object", "properties": {target_arg: {"type": "string"}}, "required": [target_arg]},
        ),
        executor=fake_executor,
        permission=ToolPermissionSpec(action=action, target_arg=target_arg),
    )


def build_registry(root, mode):
    manager = PermissionManager(policy=DefaultPermissionPolicy(project_root=root), mode=mode)
    return PermissionAwareToolRegistry(
        ToolRegistry([make_tool("write_file", PermissionAction.WRITE_PATH, "path"), make_tool("read_env", PermissionAction.READ_ENV, "key")]),
        manager,
    )


def outcome(result):
    if result.data.get("request_type") == "permission_confirmation":
        return "ASK（弹确认）"
    if result.data.get("request_type") == "permission_denied":
        return "DENY（拒绝）"
    return f"EXECUTED（真的执行了：{result.content}）"


def main():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        print("实验 1：read_env('API_PASSWORD') —— 你猜：还是 DENY 吗？")
        registry = build_registry(root, PermissionMode.STANDARD)
        print(f"  答案：{outcome(registry.execute('read_env', {'key': 'API_PASSWORD'}))}")
        print("  解释：policy.py 第 22 行的关键字名单是 (KEY, TOKEN, SECRET, PASSWORD, COOKIE)，")
        print("        PASSWORD 也在名单里，所以一样硬拒绝。\n")

        print("实验 2：先 Allow always notes.txt，再写 other.txt —— 你猜：不会放行？")
        registry = build_registry(root, PermissionMode.STANDARD)
        registry.execute("write_file", {"path": "notes.txt"})  # 第一次：ASK
        from tracecoder.tools.permission_registry import permission_request_for_tool

        manager = registry.permission_manager
        manager.resolve_confirmation(permission_request_for_tool(registry.get("write_file"), {"path": "notes.txt"}), "allow_always_same_scope")
        print(f"  答案：{outcome(registry.execute('write_file', {'path': 'other.txt'}))}")
        print("  解释：grant 记的是 exact_path 精确到 notes.txt 这个文件本身，")
        print("        other.txt 不命中 grant，重新走策略 -> 又是 ASK。\n")

        print("实验 3：STANDARD vs AGGRESSIVE 同一批操作对比 —— 答案在这：")
        ops = [
            ("write_file", {"path": "notes.txt"}, "写普通文件"),
            ("write_file", {"path": ".env"}, "写敏感文件 .env"),
            ("read_env", {"key": "API_SECRET_KEY"}, "读 SECRET 环境变量"),
        ]
        for mode in (PermissionMode.STANDARD, PermissionMode.AGGRESSIVE):
            registry = build_registry(root, mode)
            print(f"  [{mode.value} 模式]")
            for name, args, label in ops:
                print(f"    {label:<18} -> {outcome(registry.execute(name, args))}")
        print("  解释：AGGRESSIVE 只放宽“项目根内普通文件写入”这一档（不再 ASK 直接执行），")
        print("        敏感路径仍 ASK、SECRET 仍 DENY —— 这就是“硬边界连激进模式都不能绕过”。")
        print("        对照 policy.py 的 _decide_path：AGGRESSIVE 分支排在 sensitive 检查之后。")


if __name__ == "__main__":
    main()
