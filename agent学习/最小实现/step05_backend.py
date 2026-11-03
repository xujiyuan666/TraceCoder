"""第 5 步：HTTP -> Pydantic -> 应用函数 -> Agent -> JSON。

运行 --selftest 无需启动端口；正常运行提供 /docs 文档和 /chat 接口。
"""
import asyncio
from dataclasses import asdict
from typing import Literal
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from support import Trace, calculate_provider, create_registry
from step02_agent_loop import run_agent
from step03_errors_and_limits import build_provider

app = FastAPI(title="TraceCoder 第一学习单元", description="本地模拟模型；trace 为执行状态，不是模型内部思维。")


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    scenario: Literal["calculate", "unknown", "limit"] = "calculate"
    max_tool_rounds: int = Field(default=8, ge=0, le=10)


class ChatOutput(BaseModel):
    answer: str
    status: str
    provider_calls: int
    tool_rounds: int
    messages: list[dict]
    events: list[dict]


def execute_request(data: ChatInput) -> dict:
    # 每个请求独立创建 provider/session/trace，防止不同用户的数据互相混入。
    # query 被放入历史，但模拟器只按 scenario 演示固定任务，不理解任意自然语言。
    provider = calculate_provider() if data.scenario == "calculate" else build_provider(data.scenario)
    limit = 1 if data.scenario == "limit" else data.max_tool_rounds
    result = run_agent(data.query, provider, create_registry(), max_tool_rounds=limit, trace=Trace(False))
    return asdict(result)


@app.get("/health")
async def health():
    return {"status": "ok", "provider": "fake"}


@app.post("/chat", response_model=ChatOutput)
async def chat(data: ChatInput):
    # 同步 provider/loop 放到线程，避免阻塞事件循环。这不等于异步工具调度。
    # 线程任务不能靠协程取消强制终止；真正的取消需项目的 CancellationToken。
    return await asyncio.to_thread(execute_request, data)


def selftest():
    with TestClient(app) as client:
        for payload in [{"query": "计算 (2 + 3) × 4"}, {"query": ""}]:
            response = client.post("/chat", json=payload)
            Trace().emit("HTTP_RESPONSE", status_code=response.status_code, body=response.json())
        assert client.get("/health").status_code == 200


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8000)
