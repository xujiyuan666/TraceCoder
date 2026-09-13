"""链路 1 演示脚本：一次工具调用的完整旅程（学习用，可随时删除）。

运行方式：
    .venv\\Scripts\\python learn_chain1_demo.py

它用假工具（不真的写文件、不需要 API key）把真实链路跑给你看：
    模型想调工具 -> 注册表查找 -> 权限预检 -> 三种去向 -> ToolResult
每一站的代码出处都打印在输出里，看完再对照源码就不迷路了。
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tracecoder.permissions.manager import PermissionManager
from tracecoder.permissions.policy import DefaultPermissionPolicy
from tracecoder.permissions.types import PermissionAction, PermissionMode
from tracecoder.providers.types import ToolDefinition
from tracecoder.tools.permission_registry import PermissionAwareToolRegistry, permission_request_for_tool
from tracecoder.tools.registry import ToolRegistry
from tracecoder.tools.types import Tool, ToolPermissionSpec, ToolResult


def banner(title: str) -> None:
    print()
    print("=" * 62)
    print(title)
    print("=" * 62)


def show_result(label: str, result: ToolResult) -> None:
    print(f"\n[{label}] 拿到的 ToolResult：")
    print(f"  ok      = {result.ok}")
    print(f"  content = {result.content}")
    if result.data:
        print(f"  data    = { {k: v for k, v in result.data.items() if k != 'arguments'} }")


def make_write_tool() -> Tool:
    """一个假的 write_file 工具：schema 是真的，executor 只打印不落盘。"""

    def fake_write(**kwargs: Any) -> ToolResult:
        return ToolResult(
            name="write_file",
            ok=True,
            content=f"(假装写好了 {kwargs.get('path')})",
        )

    definition = ToolDefinition(
        name="write_file",
        description="写一个文件（演示用假工具）",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    return Tool(
        definition=definition,
        executor=fake_write,
        # 权限声明：写文件 -> WRITE_PATH 动作，目标取参数里的 path
        permission=ToolPermissionSpec(action=PermissionAction.WRITE_PATH, target_arg="path"),
    )


def make_env_tool() -> Tool:
    """一个假的 read_env 工具：演示“硬拒绝”长什么样。"""

    def fake_read_env(**kwargs: Any) -> ToolResult:
        return ToolResult(name="read_env", ok=True, content="(假装读到了环境变量)")

    definition = ToolDefinition(
        name="read_env",
        description="读一个环境变量（演示用假工具）",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    )
    return Tool(
        definition=definition,
        executor=fake_read_env,
        permission=ToolPermissionSpec(action=PermissionAction.READ_ENV, target_arg="key"),
    )


def main() -> None:
    with TemporaryDirectory() as tmp:
        # 每个会话的装配：策略 + manager + 注册表（对应 session_registry.py 的工厂）
        project_root = Path(tmp)
        policy = DefaultPermissionPolicy(project_root=project_root)
        manager = PermissionManager(policy=policy, mode=PermissionMode.STANDARD)
        registry = PermissionAwareToolRegistry(
            ToolRegistry([make_write_tool(), make_env_tool()]),
            manager,
        )

        banner("第 1 幕：模型调了一个不存在的工具")
        print("模型说：请调用 delete_everything()")
        print("出处：tools/registry.py 的 execute —— 查无此工具也返回 ToolResult，不抛异常")
        show_result("不存在的工具", registry.execute("delete_everything", {}))

        banner("第 2 幕：写项目内的文件 -> 需要 ASK（暂停等用户确认）")
        print("模型说：write_file(path='notes.txt')")
        print("出处：tools/permission_registry.py 的 preflight -> permissions/policy.py 的 _decide_path")
        result = registry.execute("write_file", {"path": "notes.txt"})
        show_result("项目内写入", result)
        if result.data.get("request_type") == "permission_confirmation":
            print("\n系统弹出的确认请求（UserInputRequest）：")
            print(f"  问题：{result.content}")
            print("  选项：" + " / ".join(o["label"] for o in result.data["options"]))
            print("\n注意：此刻历史里只有 tool_call，没有 tool_result —— 这就是“暂停”。")

            banner("第 3 幕：用户点了 Allow always")
            print("出处：permissions/manager.py 的 resolve_confirmation -> 写入 grant")
            decision = manager.resolve_confirmation(
                permission_request_for_tool(registry.get("write_file"), {"path": "notes.txt"}),
                "allow_always_same_scope",
            )
            print(f"  决策 = {decision.kind.value}，是否长期 = {decision.persistence.value if decision.persistence else 'N/A'}")
            print(f"  grant 里的 scope = {decision.grant.scope_type.value}: {decision.grant.scope_value}")

            banner("第 4 幕：同样的写入再来一次 -> 不再询问，直接执行")
            print("出处：permissions/manager.py 的 preflight —— 先查 grant，命中就直接放行")
            result2 = registry.execute("write_file", {"path": "notes.txt"})
            show_result("第二次写入", result2)

        banner("第 5 幕：想读敏感环境变量 -> 直接 DENY，连问都不问")
        print("模型说：read_env(key='API_SECRET_KEY')")
        print("出处：permissions/policy.py 的 _decide_env —— 含 SECRET 的 key 硬拒绝")
        show_result("敏感 env", registry.execute("read_env", {"key": "API_SECRET_KEY"}))

        banner("第 6 幕：写 .env 敏感文件 -> ASK（敏感路径升级确认）")
        print("模型说：write_file(path='.env')")
        print("出处：permissions/policy.py 的 _is_sensitive_path")
        show_result("敏感路径写入", registry.execute("write_file", {"path": ".env"}))

        banner("复盘：整条链路")
        print("""
  模型吐出 tool_call
    -> AgentLoop 先把 tool_call 写进历史（agent/loop.py）
    -> registry.execute() 查工具（tools/registry.py）
    -> PermissionAwareToolRegistry.preflight() 构造 PermissionRequest
       （tools/permission_registry.py）
    -> PermissionManager.preflight()：先查 grant，再问 policy
       （permissions/manager.py）
    -> DefaultPermissionPolicy.decide() 给出 ALLOW / ASK / DENY
       （permissions/policy.py）
    -> ALLOW：执行 -> ToolResult 写回历史，下一轮模型看到结果
       ASK ：弹 UserInputRequest，loop 暂停，用户作答后用本地保存的
             原始 tool_call 继续执行
       DENY：把“被拒绝+原因”做成 ToolResult 喂回模型

  铁律（面试可背）：
    1. 错误即信息：所有失败都是 ToolResult，loop 永不崩
    2. tool_call 和 tool_result 严格配对；ASK 是暂停，不是伪造结果
    3. 权限是模型外的程序硬判断，prompt 只是软约束
""")


if __name__ == "__main__":
    main()
