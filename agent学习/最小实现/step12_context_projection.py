"""第 12 步：持久事实、SessionView、provider 消息是三层不同结构。"""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from tracecoder.agent.session import AgentSession
from tracecoder.context.context_builder import ContextBuilder
from tracecoder.context.store import JsonlSessionStore
from tracecoder.providers.types import ChatResponse, ToolCall
from tracecoder.tools.types import ToolResult
from support import Trace


def main():
    with TemporaryDirectory(prefix="tracecoder-context-") as directory:
        store = JsonlSessionStore(directory)
        session = AgentSession.create(store=store, session_id="context-lesson")
        trace = Trace(True)
        session.append_user_message("读取配置")
        call = ToolCall("call_read", "missing_tool", {"path": "config.toml"})
        response = ChatResponse(provider="fake", model="fake", content="", tool_calls=[call], finish_reason="tool_calls")
        session.append_assistant_response(response)
        result = ToolResult(name="missing_tool", ok=False, content="未知工具：missing_tool", error="未知工具：missing_tool")
        session.append_tool_result(tool_call=call, result=result)
        trace.emit("PERSISTED_FACTS", events=len(store.list_events("context-lesson")),
                   jsonl_path=str(Path(directory) / "sessions" / "context-lesson.jsonl"))
        view = session.rebuild_view()
        trace.emit("SESSION_VIEW", messages=[asdict(message) for message in view.messages])
        projected = ContextBuilder().build_provider_messages(view)
        trace.emit("PROVIDER_PROJECTION", messages=[asdict(message) for message in projected])
        print("对照：JSONL 事件是事实，SessionView 是重放视图，ChatMessage 是本次 provider 请求投影。")


if __name__ == "__main__":
    main()
