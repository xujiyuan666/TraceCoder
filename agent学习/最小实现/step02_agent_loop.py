"""第 2 步：裁剪自 AgentLoop._continue_tool_loop_from_response 的最小循环。

直接复用项目类型和 ToolRegistry；删去权限、并发、压缩、重试等外围能力。
max_tool_rounds 的停止位置与项目一致：执行第 N 轮后即停止，不再问模型。
"""
from dataclasses import asdict, dataclass
from support import ChatRequest, FakeProvider, MemorySession, ToolRegistry, Trace, answer
from support import calculate_provider, create_registry


@dataclass
class RunResult:
    answer: str
    status: str
    provider_calls: int
    tool_rounds: int
    messages: list[dict]
    events: list[dict]


def run_agent(query: str, provider: FakeProvider, registry: ToolRegistry,
              *, max_tool_rounds: int = 8, trace: Trace | None = None, session=None) -> RunResult:
    if max_tool_rounds < 0:
        raise ValueError("max_tool_rounds 不能小于 0")
    trace = trace if trace is not None else Trace()
    session = session if session is not None else MemorySession()
    session.append_user_message(query)
    rounds, calls = 0, 0
    trace.emit("USER_INPUT", query=query)

    def finish(response, status):
        session.append_assistant_response(response)
        messages = [asdict(m) for m in session.build_messages()]
        trace.emit(status.upper(), answer=response.content, provider_calls=calls,
                   tool_rounds=rounds, messages=messages)
        return RunResult(response.content, status, calls, rounds, messages, trace.events)

    while True:
        if rounds >= max_tool_rounds:
            return finish(answer("工具轮次达到上限，停止执行。"), "tool_round_limit")
        request = ChatRequest(messages=session.build_messages(), tools=registry.definitions())
        calls += 1
        trace.emit("CALL_MODEL", provider_calls=calls, tool_rounds=rounds, request=asdict(request))
        try:
            response = provider.complete(request)
        except Exception as exc:
            # 教学版只报告失败，无重试；工具结果此前已在 session 中。
            trace.emit("PROVIDER_ERROR", error=str(exc))
            return finish(answer(f"模型调用失败：{exc}"), "provider_error")
        trace.emit("MODEL_RESPONSE", response=asdict(response))
        if not response.tool_calls:
            return finish(response, "completed")
        session.append_assistant_response(response)
        trace.emit("APPEND_TOOL_CALLS", messages=[asdict(m) for m in session.build_messages()]
                   if isinstance(session, MemorySession) else "未闭合工具事务，暂不投影")
        for tool_call in response.tool_calls:
            trace.emit("EXECUTE_TOOL", tool_call=asdict(tool_call))
            result = registry.execute(tool_call.name, tool_call.arguments)
            session.append_tool_result(tool_call=tool_call, result=result)
            trace.emit("TOOL_RESULT", tool_call_id=tool_call.id, result=asdict(result))
        rounds += 1
        trace.emit("ROUND_FINISHED", tool_rounds=rounds)


if __name__ == "__main__":
    run_agent("计算 (2 + 3) × 4", calculate_provider(), create_registry())
