"""
第 2 周：Function Calling —— 5 个工具 + 三类异常处理
================================================
老师要求：
  工具 = 天气、汇率、待办、日历、搜索
  异常全处理 = ① 超时  ② 参数错  ③ 模型瞎编工具名

设计要点：
  - 每个工具的参数用 Pydantic v2 定义，模型传错参数会被自动拦下来
  - 工具注册表：名字 -> (参数类, 执行函数, 给模型看的 schema)
  - 任何异常都不崩进程，而是变成一句"观察"喂回模型，让它自己修正后重试
    （就像人查字典看错页码，字典会告诉你"没这页"，而不是直接撕书）

运行：
    python week2_function_calling.py            # 启动命令行对话 demo（需要 API key）
    python week2_function_calling.py selftest # 自检：不调用 LLM，直接验证工具层和三类异常
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

# 默认读系统环境变量 DASHSCOPE_API_KEY（阿里云百炼）
client = AsyncOpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY", "demo-key"),
    base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
)
MODEL = os.getenv("DASHSCOPE_MODEL", "qwen-turbo")  # 便宜够用；想更强改 qwen-plus / qwen-max


# ---------- 1. 用 Pydantic 定义 5 个工具的参数 ----------
class WeatherArgs(BaseModel):
    city: str = Field(..., description="城市名，例如 北京")

class ExchangeArgs(BaseModel):
    base: str = Field(..., description="源货币代码，如 CNY、USD")
    target: str = Field(..., description="目标货币代码，如 CNY、USD")

class TodoArgs(BaseModel):
    task: str = Field(..., description="待办事项内容")

class CalendarArgs(BaseModel):
    title: str = Field(..., description="事件标题")
    date: str = Field(..., description="日期，格式 YYYY-MM-DD")
    time: str = Field("09:00", description="开始时间，格式 HH:MM")

class SearchArgs(BaseModel):
    query: str = Field(..., description="搜索关键词")


# ---------- 2. 5 个工具的真实实现 ----------
_todos: list[str] = []        # 待办存内存（演示用）
_events: list[dict] = []      # 日历存内存（演示用）

# 常用城市中英映射：Open-Meteo 地理编码只认英文名，中文输入先转一下
CITY_MAP = {
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou", "深圳": "Shenzhen",
    "杭州": "Hangzhou", "成都": "Chengdu", "南京": "Nanjing", "武汉": "Wuhan",
    "西安": "Xi'an", "重庆": "Chongqing", "长沙": "Changsha", "香港": "Hong Kong",
}

async def tool_weather(a: WeatherArgs) -> str:
    city_en = CITY_MAP.get(a.city, a.city)
    async with httpx.AsyncClient(timeout=8) as http:
        geo = await http.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city_en, "count": 1},
        )
        results = geo.json().get("results")
        if not results:
            return f"找不到城市：{a.city}"
        lat, lon = results[0]["latitude"], results[0]["longitude"]
        wx = await http.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current": "temperature_2m"},
        )
        return f"{a.city} 当前气温 {wx.json()['current']['temperature_2m']}°C"

async def tool_exchange(a: ExchangeArgs) -> str:
    async with httpx.AsyncClient(timeout=8) as http:
        r = await http.get(f"https://open.er-api.com/v6/latest/{a.base.upper()}")
        rate = r.json().get("rates", {}).get(a.target.upper())
        if not rate:
            return f"不支持从 {a.base} 兑换到 {a.target}"
        return f"1 {a.base.upper()} ≈ {rate} {a.target.upper()}"

def tool_todo(a: TodoArgs) -> str:
    _todos.append(a.task)
    return f"已添加待办：{a.task}（当前共 {len(_todos)} 条：{_todos}）"

def tool_calendar(a: CalendarArgs) -> str:
    _events.append({"title": a.title, "date": a.date, "time": a.time})
    return f"已加日历事件：{a.date} {a.time} {a.title}"

async def tool_search(a: SearchArgs) -> str:
    # DuckDuckGo 即时答案接口，免费无需 key；没有答案就如实返回
    async with httpx.AsyncClient(timeout=8) as http:
        r = await http.get(
            "https://api.duckduckgo.com/",
            params={"q": a.query, "format": "json", "no_html": 1},
        )
        data = r.json()
        abstract = data.get("AbstractText", "")
        if not abstract and data.get("RelatedTopics"):
            first = data["RelatedTopics"][0]
            abstract = first.get("Text", "")
        return abstract or f"（搜索「{a.query}」：没有即时答案，换个关键词试试）"


# ---------- 3. 工具注册表 ----------
def _schema(name: str, desc: str, model_cls: type[BaseModel]) -> dict:
    """从 Pydantic 类自动生成 OpenAI 需要的 JSON schema"""
    props, required = {}, []
    for fname, f in model_cls.model_fields.items():
        props[fname] = {"type": "string", "description": f.description or ""}
        required.append(fname)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }

REGISTRY: dict[str, dict[str, Any]] = {
    "get_weather":   {"args": WeatherArgs,   "fn": tool_weather,  "schema": _schema("get_weather",   "查询城市当前天气", WeatherArgs)},
    "exchange_rate":  {"args": ExchangeArgs,  "fn": tool_exchange, "schema": _schema("exchange_rate", "查询两种货币之间的汇率", ExchangeArgs)},
    "add_todo":       {"args": TodoArgs,      "fn": tool_todo,     "schema": _schema("add_todo",      "添加一条待办事项", TodoArgs)},
    "add_calendar":   {"args": CalendarArgs,  "fn": tool_calendar, "schema": _schema("add_calendar",   "添加一个日历事件", CalendarArgs)},
    "web_search":     {"args": SearchArgs,    "fn": tool_search,   "schema": _schema("web_search",     "网页搜索，获取即时答案", SearchArgs)},
}


# ---------- 4. 健壮执行入口：三类异常全拦住 ----------
async def safe_call(name: str, raw_args: str) -> str:
    # 异常①：模型瞎编了一个不存在的工具名
    entry = REGISTRY.get(name)
    if entry is None:
        return f"错误：没有工具叫 {name}。可用工具：{list(REGISTRY.keys())}"

    # 异常②：参数 JSON 坏掉，或字段不符合 Pydantic 定义（缺字段/类型错）
    try:
        args = entry["args"](**json.loads(raw_args or "{}"))
    except json.JSONDecodeError:
        return f"参数错误：JSON 解析失败，原始参数 = {raw_args!r}。请重新调用 {name}"
    except ValidationError as e:
        return f"参数错误：{e.errors()[0]['msg']}。请按要求重新调用 {name}"

    # 异常③：工具执行超时 / 内部报错 —— 不崩，把错误当观察喂回去
    try:
        result = entry["fn"](args)
        if asyncio.iscoroutine(result):
            result = await asyncio.wait_for(result, timeout=10)
        return str(result)
    except asyncio.TimeoutError:
        return f"工具 {name} 超时（超过 10 秒），请稍后重试或换个方式"
    except Exception as e:
        return f"工具 {name} 执行失败：{type(e).__name__}: {e}"


# ---------- 5. Agent Loop（和第 1 周同一个骨架，换成 safe_call） ----------
async def run(query: str, max_steps: int = 8) -> str:
    messages = [
        {"role": "system", "content": "你可以调用工具完成任务。如果工具报错，根据错误信息修正后重试，不要编造结果。"},
        {"role": "user", "content": query},
    ]
    schemas = [v["schema"] for v in REGISTRY.values()]

    for _ in range(max_steps):
        resp = await client.chat.completions.create(
            model=MODEL, messages=messages, tools=schemas
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append(msg.model_dump(exclude_none=True))
        for tc in msg.tool_calls:
            observation = await safe_call(tc.function.name, tc.function.arguments)
            print(f"[tool] {tc.function.name} -> {observation}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": observation,
            })
    return "（达到最大步数，提前结束）"


# ---------- 6. 自检：不调 LLM，直接验证工具层和三类异常 ----------
async def selftest():
    print("== 1) 正常调用 ==")
    print("add_todo   ->", await safe_call("add_todo", '{"task":"学完第2周"}'))
    print("add_cal    ->", await safe_call("add_calendar", '{"title":"面试","date":"2026-10-01"}'))
    print("weather    ->", await safe_call("get_weather", '{"city":"北京"}'))
    print("exchange   ->", await safe_call("exchange_rate", '{"base":"CNY","target":"USD"}'))

    print("\n== 2) 异常①：模型瞎编工具名 ==")
    print(await safe_call("call_mom", '{}'))

    print("\n== 3) 异常②：参数错误（缺 city） ==")
    print(await safe_call("get_weather", '{}'))

    print("\n== 4) 异常③：工具内部错误（不支持的货币） ==")
    print(await safe_call("exchange_rate", '{"base":"CNY","target":"XXX"}'))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        asyncio.run(selftest())
    else:
        q = input("你：")
        answer = asyncio.run(run(q))
        print("Agent：", answer)
