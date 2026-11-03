"""公共教具：直接复用项目类型；Trace 是教学用状态快照，非模型思维。"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
from typing import Callable

from tracecoder.providers.base import ChatProvider
from tracecoder.providers.types import ChatMessage, ChatRequest, ChatResponse, ToolCall, ToolDefinition
from tracecoder.tools.registry import ToolRegistry
from tracecoder.tools.types import Tool, ToolResult, make_text_result


@dataclass
class Trace:
    # 保存深拷贝：后续修改 messages 不会改掉早先的快照。
    verbose: bool = True
    events: list[dict] = field(default_factory=list)

    def emit(self, state: str, **details) -> None:
        event = deepcopy({"seq": len(self.events) + 1, "state": state, **details})
        self.events.append(event)
        if self.verbose:
            print(json.dumps(event, ensure_ascii=False, indent=2, default=_json_default))


def _json_default(value):
    """Provider 的 raw SDK 响应不属于教学状态，输出类型摘要避免 Trace 崩溃。"""
    return f"<{type(value).__name__}>"


class FakeProvider(ChatProvider):
    """沿用 tests/test_app_runtime.py 的注入方式。脚本模拟响应，不调用 LLM。"""
    def __init__(self, responses: list[ChatResponse | Callable[[ChatRequest], ChatResponse]]):
        self.responses = list(responses)
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "learning-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(deepcopy(request))
        if not self.responses:
            raise RuntimeError("模拟响应已用完；检查循环和终止条件")
        response = self.responses.pop(0)
        return response(request) if callable(response) else deepcopy(response)


def answer(text: str) -> ChatResponse:
    return ChatResponse(provider="fake", model="learning-model", content=text, finish_reason="stop")


def call(name: str, arguments, call_id: str = "call_1") -> ChatResponse:
    return ChatResponse(provider="fake", model="learning-model", content="",
                        tool_calls=[ToolCall(call_id, name, arguments)], finish_reason="tool_calls")


def create_registry() -> ToolRegistry:
    # 纯计算工具：无网络、无文件修改，第一课专注闭环。
    def add(a: int, b: int) -> ToolResult:
        if type(a) is not int or type(b) is not int:
            raise TypeError("a 和 b 必须是整数")
        return make_text_result("add", str(a + b), value=a + b)

    def multiply(value: int, factor: int) -> ToolResult:
        if type(value) is not int or type(factor) is not int:
            raise TypeError("value 和 factor 必须是整数")
        return make_text_result("multiply", str(value * factor), value=value * factor)

    def broken() -> ToolResult:
        raise RuntimeError("演示：工具内部故障")

    def tool(name, description, names, executor):
        definition = ToolDefinition(name, description, {
            "type": "object", "properties": {n: {"type": "integer"} for n in names},
            "required": names, "additionalProperties": False,
        })
        return Tool(definition=definition, executor=executor)

    return ToolRegistry([
        tool("add", "两个整数相加", ["a", "b"], add),
        tool("multiply", "两个整数相乘", ["value", "factor"], multiply),
        tool("broken", "故障演示工具", [], broken),
    ])


def calculate_provider() -> FakeProvider:
    # 第二个动作必须读取第一个工具的真实输出，而非把 5 写死。
    def next_action(request):
        value = int(json.loads(request.messages[-1].content)["content"])
        return call("multiply", {"value": value, "factor": 4}, "call_2")

    def finish(request):
        value = json.loads(request.messages[-1].content)["content"]
        return answer(f"(2 + 3) × 4 = {value}")

    return FakeProvider([call("add", {"a": 2, "b": 3}), next_action, finish])


class MemorySession:
    """把 AgentSession 的消息操作裁剪成内存实现，暂不学习落盘。"""
    def __init__(self):
        self.messages = [ChatMessage("system", "使用工具计算，依据结果回答。")]

    def build_messages(self) -> list[ChatMessage]:
        return deepcopy(self.messages)

    def append_user_message(self, content: str):
        self.messages.append(ChatMessage("user", content))

    def append_assistant_response(self, response: ChatResponse):
        self.messages.append(ChatMessage("assistant", response.content, tool_calls=deepcopy(response.tool_calls)))

    def append_tool_result(self, *, tool_call: ToolCall, result: ToolResult):
        self.messages.append(ChatMessage("tool", json.dumps(asdict(result), ensure_ascii=False),
                                         name=tool_call.name, tool_call_id=tool_call.id))
