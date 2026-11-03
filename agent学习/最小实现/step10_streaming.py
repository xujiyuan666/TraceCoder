"""第 10 步：流式事件是输出协议，不是另一套 Agent Loop。"""
import asyncio
from dataclasses import asdict

from tracecoder.providers.types import ChatResponse, ChatStreamEvent
from support import Trace


async def fake_stream():
    yield ChatStreamEvent(kind="message_started")
    for text in ["Agent ", "已经", "完成。"]:
        await asyncio.sleep(5)
        yield ChatStreamEvent(kind="text_delta", text=text)
    yield ChatStreamEvent(
        kind="message_completed",
        response=ChatResponse(provider="fake", model="stream-model", content="Agent 已经完成。", finish_reason="stop"),
    )


async def consume(trace: Trace):
    parts = []
    completed = None
    async for event in fake_stream():
        trace.emit("STREAM_EVENT", event=asdict(event))
        if event.kind == "text_delta":
            parts.append(event.text)
            trace.emit("VISIBLE_DELTA", text=event.text, visible_text="".join(parts))
        elif event.kind == "message_completed":
            completed = event.response
    if completed is None:
        raise RuntimeError("stream 没有完成事件，不能提交最终回答")
    trace.emit("COMMIT_FINAL_RESPONSE", content=completed.content)
    return completed


if __name__ == "__main__":
    asyncio.run(consume(Trace(True)))
