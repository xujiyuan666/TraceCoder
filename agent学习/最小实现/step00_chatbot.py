"""第 0 步：一次请求、一次响应。先读 main，再看 support.FakeProvider。"""
from dataclasses import asdict
from support import ChatMessage, ChatRequest, FakeProvider, Trace, answer


def main():
    trace = Trace()
    provider = FakeProvider([answer("这是模拟模型的回答。普通问答没有工具反馈循环。")])
    messages = [ChatMessage("user", "什么是 Agent？")]
    trace.emit("USER_INPUT", messages=[asdict(m) for m in messages])
    request = ChatRequest(messages=messages)
    trace.emit("CALL_MODEL", request=asdict(request), provider_calls=1)
    response = provider.complete(request)
    trace.emit("MODEL_RESPONSE", response=asdict(response))
    messages.append(ChatMessage("assistant", response.content))
    trace.emit("COMPLETED", answer=response.content, messages=[asdict(m) for m in messages])


if __name__ == "__main__":
    main()
