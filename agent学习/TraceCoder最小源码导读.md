# TraceCoder 最小 Agent 源码导读

> **代码学习从这里开始：[最小实现课程](最小实现/README.md)。** 它包含 7 个可独立运行的 Python 实验、中间状态输出、异步与 HTTP 接口，以及真实会话恢复。本文件保留作源码对照材料。

这是一份 60–90 分钟的入门单元。目标不是读完整个 TraceCoder，而是读懂一条最小的 Agent 闭环：

```text
用户输入
  -> AgentLoop 调用模型
  -> 模型返回 tool call
  -> ToolRegistry 执行工具
  -> ToolResult 回传模型
  -> 模型继续判断或输出最终答案
```

完成后，你应该能回答：Agent 和 Chatbot 有什么区别？一次工具调用经过哪些对象？为什么工具结果必须带有匹配的 `tool_call_id`？循环怎样停止？

## 0. 学习边界

本单元只看四个角色：

| 角色 | 在 TraceCoder 中的职责 |
| --- | --- |
| `AgentLoop` | 协调用户输入、模型调用、工具执行和最终回答 |
| `ChatProvider` | 提供统一的模型调用接口，屏蔽 OpenAI/Anthropic 等协议差异 |
| `ToolRegistry` | 保存工具定义，按名称查找并执行工具 |
| `AgentSession` / `JsonlSessionStore` | 保存消息、tool call 和 tool result，使会话可以 replay/resume |

先跳过 TUI、MCP、Memory、RAG、多 Agent、评估和部署。它们都建立在本单元的闭环之上。

## 1. 环境和验证

项目要求 Python 3.11+。这次学习不需要模型 API Key，使用仓库里的 fake provider 测试。

在项目根目录运行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest tests\test_agent_loop_limits.py -q
```

如果项目依赖已经安装，可以直接运行最后一条测试命令。

## 2. 先记住最小心智模型

普通 Chatbot 通常只有一次调用：

```text
用户问题 -> 模型回答
```

Agent 会把模型的行动建议交给程序执行，再把环境观察结果交还模型：

```text
用户目标
  -> 模型决定直接回答，或提出 tool call
  -> 程序校验并执行工具
  -> 产生 ToolResult
  -> 模型根据结果继续行动
  -> 最终回答或触发边界
```

关键点不是“模型会思考”，而是模型、工具、反馈和控制循环连在一起。

## 3. 按执行顺序读源码

### 3.1 `AgentLoop`：循环的协调器

打开 [`tracecoder/agent/loop.py`](../tracecoder/agent/loop.py)，按下面顺序看函数签名和主干代码：

1. `AgentLoop.run_user_turn`
   - 对外的异步入口；普通执行会转入同步实现。
2. `AgentLoop._run_user_turn_sync_impl`
   - 保存用户消息，修复中断状态，然后启动工具循环。
3. `AgentLoop._run_tool_loop_interactive`
   - 处理一轮或多轮模型响应，决定完成、暂停还是触发限制。
4. `AgentLoop._continue_tool_loop_from_response`
   - 只要响应里还有 `tool_calls`，就保存 assistant tool call、执行工具、再次调用模型。
5. `AgentLoop._complete_turn`
   - 保存最终 assistant 响应，记录本轮状态，返回 `AgentTurnResult`。

把真实实现先压缩成下面的伪代码：

```python
append_user_message()
repair_interrupted_tool_calls()

response = provider.complete(build_request())
while response.tool_calls:
    append_assistant_tool_calls(response)
    execution = tool_executor.execute_interactive(response.tool_calls)
    if execution.waiting_for_user:
        return paused_result
    response = provider.complete(build_request())

append_assistant_response(response)
return completed_result
```

真实代码还处理 streaming、取消、重试、上下文压缩、任务计划和完成检查；第一次阅读先把这些当作外围护栏。

### 3.2 Provider 类型：模型和 Agent 之间的协议

打开 [`tracecoder/providers/types.py`](../tracecoder/providers/types.py)，重点看四个数据结构：

- `ToolDefinition`：给模型看的工具名称、描述和 JSON Schema；
- `ToolCall`：模型返回的一次工具调用请求，包含 `id`、工具名和参数；
- `ChatRequest`：发给 provider 的统一请求；
- `ChatResponse`：provider 返回的统一响应，可能包含文本和多个 `tool_calls`。

再看 [`tracecoder/providers/base.py`](../tracecoder/providers/base.py) 的 `ChatProvider`。AgentLoop 只依赖这个抽象接口，不直接依赖某家模型 SDK。

### 3.3 ToolRegistry：把名称变成执行结果

打开 [`tracecoder/tools/registry.py`](../tracecoder/tools/registry.py)：

- `definitions()` 把工具 schema 提供给 provider；
- `execute(name, arguments)` 按名称查找 executor；
- 未知工具、非对象参数、参数错误和 executor 异常都会变成结构化失败的 `ToolResult`。

这条边界很重要：工具失败是模型可观察的事实，不应该因为一个工具异常就让整个 AgentLoop 崩溃。

工具有两面：

```text
ToolDefinition  -> 模型知道“能调用什么、参数是什么”
executor        -> Python 真正执行动作
ToolResult      -> 执行成功或失败后的统一观察
```

### 3.4 Session 和 JSONL：保存可恢复事实

打开 [`tracecoder/agent/session.py`](../tracecoder/agent/session.py) 和 [`tracecoder/context/store.py`](../tracecoder/context/store.py)。

一次工具调用的持久化顺序必须是：

```text
assistant: tool_call(id=call_123, name=read_file, arguments={...})
tool:     tool_result(tool_call_id=call_123, ...)
```

下一次 provider 请求必须看到合法的配对关系。只保存 tool result、漏掉 assistant tool call，或者 `tool_call_id` 不匹配，都可能导致 provider 拒绝请求。

`JsonlSessionStore` 使用追加事件保存事实；恢复时重新读取事件并构造 `SessionView`。因此会话可以 replay，而不是依赖当前进程内存里的对象。

## 4. 配套实验

### 实验 A：正常的两次模型调用

阅读 [`tests/test_app_runtime.py`](../tests/test_app_runtime.py) 中的 `FakeProvider`，再搜索包含 `ToolCall` 和两次 `ChatResponse` 的测试。

画出这条顺序：

```text
provider response 1: read_file tool call
  -> 保存 assistant tool call
  -> 执行工具
  -> 保存 tool result
provider response 2: 最终文本
  -> 保存 assistant 最终回答
```

回答：

- provider 被调用几次？
- 谁生成 `ToolResult`？
- 第二次调用模型时，模型能看到哪些历史消息？

### 实验 B：未知工具

阅读 [`tests/test_agent_tool_flow.py`](../tests/test_agent_tool_flow.py) 中 `test_unknown_tool_result_is_persisted_as_structured_error`。

模型请求一个不存在的工具时，`ToolRegistry.execute()` 不抛出异常，而是返回失败的 `ToolResult`。这个错误会被保存，并可以作为观察结果回传模型。

### 实验 C：轮次上限

阅读 [`tests/test_agent_loop_limits.py`](../tests/test_agent_loop_limits.py)，重点看 `max_tool_rounds`。

思考：如果模型一直返回 tool call，系统为什么不能无限执行？至少需要这些边界：

- 没有 `tool_calls`：模型给出最终回答；
- `max_tool_rounds`：工具轮次上限；
- provider call limit：模型调用次数上限；
- turn timeout：单轮时间上限；
- cancellation：用户主动中断。

### 实验 D：工具调用协议配对

阅读 [`tests/test_agent_tool_flow.py`](../tests/test_agent_tool_flow.py) 中这些测试：

- `test_assistant_tool_calls_are_persisted_as_tool_call_parts`
- `test_tool_results_are_persisted_with_matching_tool_call_id`
- `test_context_builder_rejects_orphan_tool_result`
- `test_context_builder_rejects_assistant_tool_call_without_tool_result`

用一句话解释：为什么“先写 assistant tool call，再写 tool result”是协议要求，而不是代码风格？

## 5. 四个通关问题

不要看答案，先自己写下回答：

1. Agent 和 Chatbot 的核心区别是什么？
2. 模型返回 tool call 后，为什么还要再次调用模型？
3. 为什么必须先保存 assistant tool call，再保存 tool result？
4. AgentLoop 如何避免无限循环？

达到通关标准：你能从 `run_user_turn` 开始，指出一次 `read` 工具调用经过的函数、对象和两次 provider 请求，并说明失败工具如何变成 `ToolResult`。

## 6. 常见误区

### 误区 1：把 Agent 等同于“更聪明的 Prompt”

Prompt 只能引导模型。Agent 的关键是程序真的执行工具，并把结果作为下一轮上下文。

### 误区 2：把工具 schema 当成安全边界

`ToolDefinition` 只告诉模型工具怎么调用。真正的执行控制在 Python 侧的 registry、权限和执行器中。

### 误区 3：工具报错就应该抛异常终止

可恢复的工具错误应该结构化为 `ToolResult`，让模型看到错误后决定重试、换工具或向用户解释。

### 误区 4：只保存最终文本就够了

如果不保存 tool call、tool result 和它们的 ID，协议校验、上下文重建和会话恢复都会失去依据。

### 误区 5：第一次就读完整个 `loop.py`

先抓住五个主函数和主闭环，再回头看 streaming、重试、压缩、权限和 benchmark 分支。外围能力应该建立在主循环已经清楚之后。

## 7. 下一步

完成本单元后，再进入独立的工具与权限单元：

- [`learn_chain1_demo.py`](../learn_chain1_demo.py)：用假工具观察未知工具、ASK、ALLOW 和 DENY；
- [`learn_chain1_experiments.py`](../learn_chain1_experiments.py)：观察权限 grant 和不同权限模式；
- [`agent学习/code/week2_function_calling.py`](code/week2_function_calling.py)：学习 schema、参数校验和工具异常。

`agent学习/code/week1_agent_loop.py` 是带真实模型、网络天气接口和 FastAPI 的概念对照示例。它适合后续练习 Function Calling，不是本单元的主入口。
