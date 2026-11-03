"""第 1 步：一次 tool call。还没有循环，也没有最终模型回答。"""
from dataclasses import asdict
from support import ChatRequest, FakeProvider, MemorySession, Trace, call, create_registry


def main():
    trace, session, registry = Trace(), MemorySession(), create_registry()
    session.append_user_message("计算 2 + 3")
    provider = FakeProvider([call("add", {"a": 2, "b": 3})])
    request = ChatRequest(messages=session.build_messages(), tools=registry.definitions())
    trace.emit("CALL_MODEL", request=asdict(request))
    response = provider.complete(request)
    trace.emit("MODEL_RESPONSE", response=asdict(response))
    # 先记模型提出的动作，再执行；结果通过同一个 call.id 配对。
    session.append_assistant_response(response)
    for tool_call in response.tool_calls:
        trace.emit("EXECUTE_TOOL", tool_call=asdict(tool_call))
        result = registry.execute(tool_call.name, tool_call.arguments)
        session.append_tool_result(tool_call=tool_call, result=result)
        trace.emit("TOOL_RESULT", result=asdict(result))
    trace.emit("OBSERVATION_ONLY", provider_calls=len(provider.requests),
               messages=[asdict(m) for m in session.build_messages()])
    print("工具已经算出 5；还要把这些历史再交给模型，才形成完整闭环。")


if __name__ == "__main__":
    main()
