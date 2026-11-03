"""第 6 步：用真实 AgentSession/JSONL 替换内存。重放和继续会话。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import asdict

from tracecoder.agent.session import AgentSession
from tracecoder.context.context_builder import ContextBuilder
from tracecoder.context.store import JsonlSessionStore
from support import FakeProvider, Trace, answer, call, create_registry
from step02_agent_loop import run_agent


class StoredSession:
    """适配真实 session，loop 的三种追加操作保持相同语义。"""
    def __init__(self, session: AgentSession):
        self.session = session

    def append_user_message(self, content):
        return self.session.append_user_message(content)

    def append_assistant_response(self, response):
        return self.session.append_assistant_response(response)

    def append_tool_result(self, *, tool_call, result):
        return self.session.append_tool_result(tool_call=tool_call, result=result)

    def build_messages(self):
        # JSONL 事实 -> SessionView -> ChatMessage；真实 builder 校验调用和结果配对。
        return ContextBuilder().build_provider_messages(self.session.rebuild_view())


def main(verbose=True):
    trace, registry = Trace(verbose), create_registry()
    with TemporaryDirectory(prefix="tracecoder-learning-") as directory:
        store = JsonlSessionStore(directory)
        session = AgentSession.create(store=store, session_id="lesson", tools=registry.tools())
        result = run_agent("计算 2 + 3", FakeProvider([call("add", {"a": 2, "b": 3}), answer("结果是 5")]),
                           registry, session=StoredSession(session), trace=trace)
        path = Path(directory) / "sessions" / "lesson.jsonl"
        trace.emit("JSONL_SAVED", path=str(path), jsonl=path.read_text(encoding="utf-8"))
        # 从磁盘新建 store/session；恢复历史，不自动重新执行过去的工具。
        restored = AgentSession.resume(store=JsonlSessionStore(directory), session_id="lesson", tools=registry.tools())
        messages = StoredSession(restored).build_messages()
        trace.emit("SESSION_RESTORED", messages=[asdict(m) for m in messages])
        provider = FakeProvider([answer("会话已恢复；历史中的计算结果是 5。")])
        continued = run_agent("继续", provider, registry, session=StoredSession(restored), trace=trace)
        trace.emit("RESUME_REQUEST", messages=[asdict(m) for m in provider.requests[0].messages])
        # 临时目录退出即清理。学习生产存储时再换成项目 .tracecoder 数据目录。
        return result, messages, continued


if __name__ == "__main__":
    main()
