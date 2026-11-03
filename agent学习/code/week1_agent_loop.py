"""
第 1 周：手写最小 Agent Loop（不许用 LangChain）
================================================
核心就是一个闭环：
    思考 Think  ->  行动 Act  ->  观察 Observe  ->  再思考 ...
直到模型觉得"不用再调工具了"，输出最终答案。

生活化类比：像查字典写作业——
    思考 = 读题决定要查哪个词
    行动 = 真的去翻字典
    观察 = 把查到的释义抄下来
    再思考 = 看完释义，决定继续查还是直接写答案

运行：
    1) 设置环境变量（或复制 .env.example 为 .env）：
         ARK_API_KEY=你的key
         ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
         ARK_MODEL=你的模型接入点ID
    2) python week1_agent_loop.py            启动 FastAPI 服务
    3) 另开终端测试：
         curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d "{\"query\":\"现在几点？北京天气怎么样？\"}"
"""
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

import uvicorn
import httpx
from fastapi import FastAPI
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

# ---------- 1. 配置 LLM 客户端（阿里云百炼 DashScope，OpenAI 兼容接口） ----------
# 默认读系统环境变量 DASHSCOPE_API_KEY；也可用 DASHSCOPE_MODEL 切换模型
client = AsyncOpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY", "demo-key"),
    base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
)
MODEL = os.getenv("DASHSCOPE_MODEL", "qwen-turbo")  # 便宜够用；想更强改 qwen-plus / qwen-max


# ---------- 2. 两个最小工具：实现函数 + 给模型看的 JSON schema ----------
def get_time() -> str:
    """工具①：获取当前时间（同步函数，瞬间完成）"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 常用城市中英映射：Open-Meteo 地理编码只认英文名
CITY_MAP = {
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou", "深圳": "Shenzhen",
    "杭州": "Hangzhou", "成都": "Chengdu", "南京": "Nanjing", "武汉": "Wuhan",
    "西安": "Xi'an", "重庆": "Chongqing", "长沙": "Changsha",
}


async def get_weather(city: str) -> str:
    """工具②：查城市天气（Open-Meteo 免费、无需 key）"""
    city_en = CITY_MAP.get(city, city)
    async with httpx.AsyncClient(timeout=15) as http:
        geo = await http.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city_en, "count": 1},
        )
        results = geo.json().get("results")
        if not results:
            return f"找不到城市：{city}"
        lat, lon = results[0]["latitude"], results[0]["longitude"]
        wx = await http.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current": "temperature_2m"},
        )
        return f"{city} 当前气温 {wx.json()['current']['temperature_2m']}°C"


# 工具注册表：名字 -> {"fn": 实现, "schema": 给模型看的说明书}
TOOLS = {
    "get_time": {
        "fn": get_time,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "获取当前日期和时间",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    },
    "get_weather": {
        "fn": get_weather,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "查询某个城市当前的天气/气温",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "城市名，例如 北京、上海"}
                    },
                    "required": ["city"],
                },
            },
        },
    },
}


# ---------- 3. 统一工具执行入口 ----------
async def call_tool(name: str, args: dict) -> str:
    """同步工具直接跑，异步工具 await；出错不崩进程，返回错误文本当观察"""
    tool = TOOLS.get(name)
    if tool is None:
        return f"错误：没有叫 {name} 的工具"
    try:
        result = tool["fn"](**args)
        if asyncio.iscoroutine(result):  # 异步工具（如查天气）
            result = await result
        return str(result)
    except Exception as e:
        return f"工具 {name} 执行失败：{type(e).__name__}: {e}"


# ---------- 4. 核心：Agent Loop（整个文件就这一个循环） ----------
async def agent_loop(query: str, max_steps: int = 8) -> str:
    messages = [
        {"role": "system", "content": "你是一个可以调用工具的助手。需要查时间或天气时就调用工具，拿到结果后再用中文回答。"},
        {"role": "user", "content": query},
    ]
    schemas = [t["schema"] for t in TOOLS.values()]

    for step in range(max_steps):
        # (思考) 模型看着对话历史，决定：直接回答，还是再调工具？
        resp = await client.chat.completions.create(
            model=MODEL, messages=messages, tools=schemas
        )
        msg = resp.choices[0].message

        # 模型没提出任何工具调用 = 闭环完成，输出最终答案
        if not msg.tool_calls:
            return msg.content or ""

        # 把模型这一轮（含它想调的工具）写回历史，这是闭环的关键
        messages.append(msg.model_dump(exclude_none=True))

        # (行动 + 观察) 逐个执行工具，把结果喂回给模型
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            observation = await call_tool(tc.function.name, args)
            print(f"[step {step}] 调用 {tc.function.name}({args}) -> {observation}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": observation,
            })

    return "（达到最大步数，提前结束）"


# ---------- 5. FastAPI 接口 ----------
app = FastAPI(title="Week1 Minimal Agent", version="1.0")


class ChatReq(BaseModel):
    query: str = Field(..., description="用户问题", examples=["现在几点？北京天气怎么样？"])


@app.post("/chat")
async def chat(req: ChatReq):
    return {"answer": await agent_loop(req.query)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
