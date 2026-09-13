# TraceCoder 学习手册：从能运行到能讲清楚

这份手册不是 TraceCoder 的 API 参考，而是一条面向源码和面试的学习路线。目标是让你最后能够回答四个问题：

1. 用户输入后，系统到底经过了哪些模块？
2. 模型返回 tool call 后，为什么不会直接执行？
3. 长会话压缩后，为什么还能恢复，而且不会破坏 tool call/result 顺序？
4. 这个项目的工程价值如何被测试和 benchmark 证明？

你不需要一开始读完所有代码。每一节都给出“先看什么、应该理解什么、如何验证”。

---

## 0. 先建立一张总图

TraceCoder 的核心不是“调用模型”，而是把模型建议的动作放进一个受控运行时：

```text
用户输入
  ↓
app.factory 负责装配
  ↓
SessionBootstrap 创建/恢复 AgentSession
  ↓
AgentLoop 协调一轮任务
  ├─ ContextBuilder：把持久事实投影成模型消息
  ├─ Provider：把统一请求翻译成 OpenAI/Anthropic 协议
  ├─ ToolExecutor：处理 tool call、并发、取消和结果记录
  ├─ PermissionManager：决定 ALLOW / ASK / DENY
  └─ JsonlSessionStore：追加保存事件，支持 replay/resume
  ↓
模型最终回答 / 等待用户确认 / 受护栏限制停止
```

最重要的三个心智模型：

| 心智模型 | 在本项目中的含义 |
| --- | --- |
| 事实和视图分离 | JSONL event 是持久事实；`SessionView` 是 replay 后的有效视图；`ChatMessage` 是本次 provider 请求的投影。 |
| 协调和执行分离 | `AgentLoop` 决定何时调用模型、何时进入下一轮；工具和 provider 负责具体执行。 |
| 策略和提示词分离 | prompt 可以引导模型，但真正的权限、安全检查和循环上限由 Python 代码强制。 |

第一遍建议先读仓库已经提供的[架构说明](ARCHITECTURE.zh-CN.md)和[代码阅读指南](CODEBASE_READING_GUIDE.zh-CN.md)，然后回到本手册按顺序读源码。

---

## 1. 学习前准备：先把项目跑起来

### 1.1 环境和最小验证

项目要求 Python 3.11+，开发依赖和测试命令见 `pyproject.toml`。先完成：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest tests\test_agent_loop_limits.py -q
```

如果暂时没有模型 API key，也不影响前几章学习。大部分核心测试使用 fake provider 和临时目录，不依赖真实网络。

### 1.2 先看哪些入口

按下面顺序打开文件，只看类和函数签名，不要马上钻进每一个 helper：

1. `tracecoder/cli.py`：命令行入口。
2. `tracecoder/app/factory.py`：组合根，负责把 provider、store、tools、session、context manager 和 runner 接起来。
3. `tracecoder/session/bootstrap.py`：统一创建、恢复和项目会话。
4. `tracecoder/agent/session.py`：会话对象以及工具、权限、事件写入的边界。
5. `tracecoder/agent/loop.py`：一轮任务的主协调器。

你第一遍读完应该能画出：`create_tracecoder_app()` 如何最终得到一个 `AgentLoop`。其中最值得记住的是：创建、resume、fork 的装配不应该散落在 UI 命令里，而是集中经过 `SessionBootstrap`。

---

## 2. 第一阶段：读懂 Agent Loop

### 2.1 先理解“单轮事务”

重点看 `tracecoder/agent/loop.py` 中这些函数：

- `AgentLoop.run_user_turn()`：对外的统一异步入口；
- `_run_user_turn_sync_impl()`：普通模式下，一轮输入如何开始；
- `_complete_once()`：准备一次 provider 请求并调用模型；
- `_run_tool_loop_interactive()`：核心工具循环；
- `_continue_tool_loop_from_response()`：处理一组 tool call、回填结果并再次请求模型；
- `_complete_turn()`：把最终响应持久化并生成 `AgentTurnResult`。

把它简化成伪代码：

```python
append_user_message()
repair_interrupted_tool_calls()
compact_if_needed()

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

注意：真实代码还包含 streaming、取消、重试、task plan reconciliation、completion gate 和 benchmark 能力开关，但主骨架就是这段。

### 2.2 你必须讲清楚的顺序

模型返回 tool call 后，系统先追加 assistant 的 tool-call part，再执行工具，最后追加匹配的 tool result。原因不是代码风格，而是 provider 的消息协议要求：

```text
assistant(tool_call: call_123)
tool(result, tool_call_id=call_123)
```

如果只追加 tool result、不追加前面的 assistant tool call，或者 `tool_call_id` 对不上，下一次 provider 请求可能直接被拒绝。对应测试在 `tests/test_agent_tool_flow.py`，尤其是：

- `test_assistant_tool_calls_are_persisted_as_tool_call_parts`
- `test_tool_results_are_persisted_with_matching_tool_call_id`
- `test_context_builder_rejects_orphan_tool_result`
- `test_context_builder_rejects_assistant_tool_call_without_tool_result`

### 2.3 循环为什么能停

读 `tracecoder/agent/loop_limits.py` 和 `tests/test_agent_loop_limits.py`。至少要掌握这些停止原因：

- 没有 tool call：模型给出最终回答；
- `max_tool_rounds`：防止模型陷入无限工具循环；
- provider call / turn timeout：防止一次任务无限占用资源；
- cancellation：用户主动中断；
- 用户确认：不是结束，而是保存 pending 状态后暂停；
- provider 错误：按错误类别决定重试、压缩后重试或直接失败。

面试回答可以这样组织：

> 我把 Agent Loop 看成一个有边界的状态机。正常路径是 model → tool → model，结束条件是无 tool call；异常路径通过轮次、调用次数、超时和取消控制上界；需要权限时不执行副作用，而是保存 pending tool call，等待用户输入后恢复同一笔事务。

### 2.4 第一组练习

1. 不看答案，在纸上画出一次 `read` 工具调用的完整事件顺序。
2. 找到一个 unknown tool 的测试，说明为什么它返回结构化 `ToolResult`，而不是直接抛异常。
3. 给 fake provider 设计三次响应：第一次返回 tool call，第二次返回 tool result 之后的最终文本。说明 provider 被调用几次、JSONL 中会追加哪些事实。
4. 阅读 `tests/test_agent_loop_limits.py`，说出 default、SWE-lite、summary 三种 profile 的差异。

---

## 3. 第二阶段：工具系统和权限系统

### 3.1 ToolDefinition 不等于 executor

从 `tracecoder/tools/types.py` 和 `tracecoder/tools/registry.py` 开始，再看 `tracecoder/tools/builtin.py`。

一个工具至少有两面：

- `ToolDefinition`：给模型看的名称、描述和 JSON Schema；
- executor：Python 真正执行的函数。

`ToolRegistry.definitions()` 只返回 schema；`ToolRegistry.execute()` 根据名称找到 executor，并把未知工具、参数错误、执行异常转成结构化失败结果。这样模型可以看到失败证据并修正，而不是因为一个工具错误让整个 loop 崩掉。

内置工具默认只注册只读工具。写入、shell、Python 执行和网络工具需要显式启用，这是一个很适合面试讲的默认安全策略：能力不是默认全部暴露，而是由装配阶段决定。

### 3.2 权限判断的真实链路

重点看：

- `tracecoder/permissions/types.py`：动作、模式、决策和 grant 的词汇表；
- `tracecoder/permissions/policy.py`：默认策略；
- `tracecoder/permissions/manager.py`：grant 匹配、确认结构构造和 resume 前重验；
- `tracecoder/permissions/grants.py`：长期授权持久化；
- `tracecoder/tools/permission_registry.py`：把工具调用接到权限边界；
- `tracecoder/agent/tool_execution.py`：把 preflight、pending、执行和结果写入串起来。

真实流程是：

```text
ToolCall
  ↓
PermissionRequest(action, target, cwd, metadata)
  ↓
已有 grant 匹配
  ↓ 未匹配
DefaultPermissionPolicy
  ↓
ALLOW / ASK / DENY
  ├─ ALLOW：才允许进入 executor
  ├─ ASK：保存 pending，返回结构化 UserInputRequest
  └─ DENY：追加失败 ToolResult，不执行副作用
```

`PermissionMode` 有 `standard`、`aggressive`、`bypass` 三种模式，但 bypass 仍然经过统一的请求规范化、registry 和结果记录；它不是让工具绕过所有代码路径。

### 3.3 为什么写文件还有第二道 review

直接文件修改工具 `write`、`edit`、`apply_patch`、`delete` 的风险比读取高。权限允许只回答“这类动作能不能执行”，写前 review 还要回答“这一次具体会改什么”。

重点看 `tracecoder/tools/review.py`、`tracecoder/agent/tool_execution.py` 和 `tests/test_prewrite_review.py`。实现要点：

1. 根据原始 tool call 生成可信 unified diff；
2. 记录文件快照；
3. 给用户展示有界 diff；
4. 用户确认后再次校验快照；
5. 快照过期则拒绝应用，避免覆盖外部并发修改。

这不是文件系统级事务，但属于应用层的乐观并发保护。`shell` 没有同样的任意文件影响预计算，因为任意 shell 命令的效果无法安全提前推导。

### 3.4 第二组练习

逐个预测 `tests/test_permissions_policy.py` 中这些请求的决策，再运行测试核对：

- 项目根目录普通读取；
- 读取 `.env`；
- 执行 `pytest tests`；
- 含控制操作符的 shell；
- 删除项目外文件；
- 读取 `OPENAI_API_KEY`；
- MCP 工具调用。

然后回答：为什么“allow always”不能简单保存成一句“以后允许所有写操作”？答案应包含 scope 类型，例如 exact path、command prefix、host、env key 和 MCP tool。

---

## 4. 第三阶段：JSONL、Replay 和会话恢复

### 4.1 三层数据结构

重点看：

- `tracecoder/context/events.py`：事件形状；
- `tracecoder/context/writer.py`：如何追加事件；
- `tracecoder/context/store.py`：如何按顺序 replay；
- `tracecoder/context/models.py`：`SessionView`、`AgentMessage`、`MessagePart`；
- `tracecoder/agent/session.py`：上层如何使用 store。

核心链路：

```text
append-only JSONL event
  ↓ JsonlSessionStore.rebuild_session_view()
SessionView：当前有效事实
  ↓ ContextBuilder.build_provider_messages()
ChatMessage[]：本次请求投影
```

不要把 `ChatMessage[]` 当成完整历史。它只是某次请求所需的窗口，可能经过 trim、tool result 压缩、archive placeholder 或 checkpoint 投影；JSONL 才是可审计、可恢复的持久事实。

### 4.2 为什么不直接改写 JSONL

上下文压缩不是删除历史，而是追加 replacement、checkpoint 和 archive 相关事件。好处是：

- 可以审计发生过什么；
- 崩溃后可以从头 replay；
- resume 不依赖进程内某个“神秘 transcript”；
- 压缩操作尽量可幂等，重复 replay 不会把 archive 展回去或重复替换。

读 `tests/test_context_store.py` 和 `tests/test_context_runtime_replay.py`，重点关注事件顺序、非法 task plan、active task hash、consumed tool result part ids 和 latest checkpoint 的恢复。

### 4.3 权限暂停为什么必须保存 pending

权限确认发生在 assistant tool call 已经进入历史之后。如果把用户的“允许”当作普通新用户消息追加，历史就可能变成：

```text
assistant(tool_call)
user("允许")
```

而不是合法的 tool result。项目通过 `PendingPermissionExecution` 保存原始调用，恢复时先补齐同一条 tool-call transaction 的 result，再继续请求模型。这是“暂停并恢复”最值得你掌握的状态设计。

### 4.4 第三组练习

1. 找一个临时 session 目录，观察一次对话会产生哪些 JSONL event type。
2. 手动解释 `rebuild_session_view()` 为什么必须按 append 顺序执行。
3. 结合 `tests/test_agent_tool_flow.py` 的 permissioned write 测试，说明“暂停时文件为什么还没有改变”。
4. 设计一个中断场景：assistant tool call 已写入但进程在 tool result 前退出。找出恢复前修复未完成 tool call 的代码。

---

## 5. 第四阶段：上下文预算和 L1–L4 压缩

这是你简历里最容易被深挖、也最能体现工程思考的一部分。不要只说“做了上下文压缩”，要说清楚“什么能丢、什么不能丢、何时压缩、原文放在哪里”。

### 5.1 ContextBuilder 的职责

先看 `tracecoder/context/context_builder.py`。它负责将 `SessionView` 投影成 provider messages，并检查：

- tail 是否从合法边界开始；
- tool result 是否有对应 tool call；
- tool call/result 的 ID 是否匹配；
- checkpoint 和未压缩 tail 拼接后是否仍然是合法消息序列。

因此，ContextBuilder 不是简单的 `messages[-N:]`。它必须尊重对话协议边界。

### 5.2 L1–L4 逐层理解

对应实现主要在 `tracecoder/context/compaction.py`、`manager.py`、`archive.py`、`llm_compact.py` 和 `provider_summarizer.py`。

| 层级 | 做什么 | 不能做什么 |
| --- | --- | --- |
| L1 | 裁掉确认属于旧任务的普通文本 | 不能丢最新用户意图、tool transaction 或 fresh 源码读取 |
| L2 | 按类型压缩 log、search、diff、JSON 等 derived tool output | 候选必须确实更短，并保留错误、路径、行号和 diff 形状等关键证据 |
| L3 | 用 bounded placeholder 替换可恢复的旧材料 | 不能移除整个 tool transaction；原文必须先 archive |
| L4 | 用模型生成 coding-handoff checkpoint | 不能把摘要当成原始证据；写入前要验证边界和协议合法性 |

归档后的 placeholder 通过 session-scoped `retrieve_archive` 恢复，不应变成任意文件系统读取，也不能跨 session 访问。

### 5.3 触发和预算

`ContextWindowManager` 负责何时升级压缩层级。常见 trigger 有：

- `AUTO`：主 provider 请求前，根据模型窗口和输出预留检查；
- `TASK_HASH_CHANGED`：确认切换任务后清理旧 derived context；
- `MANUAL`：用户主动 compact/inspect；
- `PROMPT_TOO_LONG`：provider 拒绝后做一次阻塞压缩和有界重试。

预算逻辑在 `tracecoder/context/token_budget.py`，主循环中的请求准备和压缩入口在 `AgentLoop._prepare_main_provider_request()`、`_compact_if_needed()` 和 `_complete_once_with_recovery()`。

重点理解一个原则：确定性 L1–L3 先做，仍然不够才调用昂贵的 L4 摘要模型。并且压缩后必须同时满足“更短”和“仍然合法”。

### 5.4 第四组练习

建议重点跑这些测试：

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\test_context_builder_new.py `
  tests\test_context_store.py `
  tests\test_context_compaction_pipeline.py `
  tests\test_context_window_manager.py `
  tests\test_context_runtime_replay.py -q
```

从 `tests/test_context_compaction_pipeline.py` 任选三项，分别回答：

1. 这个测试为了节省什么 token？
2. 哪些内容被明确保护？
3. 原始内容是否还能恢复？通过哪一个 ID 或工具恢复？
4. 重复执行压缩是否会产生重复副作用？

面试中可以用这句话总结：

> 我把持久事实和模型工作窗口分开。压缩只改变 provider 投影，通过 L1–L3 的确定性裁剪/压缩/归档优先降低成本，L4 才生成 checkpoint；所有有损操作都要保护 tool transaction、fresh source 和最新任务意图，并保证 replay/resume 幂等。

---

## 6. 第五阶段：Provider、Streaming 和 MCP

### 6.1 Provider 抽象的目标

先读 `tracecoder/providers/types.py`、`base.py`、`factory.py`，再读：

- `openai_compatible.py`：OpenAI Chat Completions 兼容路径；
- `anthropic_provider.py`：Anthropic Messages API；
- `streaming.py`：流式事件的内部表示；
- `errors.py`：统一错误分类。

核心不是把两个 SDK 的字段机械改名，而是让上层只依赖统一的 `ChatRequest`、`ChatResponse`、`ToolCall`、`ChatStreamEvent` 和 usage/finish reason 契约。厂商专属字段留在 adapter。

### 6.2 Streaming 最容易错在哪里

流式文本可以边到边展示，但 tool call 的参数可能分散在多个 chunk 中。因此真实实现要等 stream 结束并拿到完整 `message_completed` 后，再执行工具。失败重试时还要清理已经收到的局部 delta，避免 UI 显示一段实际上没有成功的半截回答。

重点看 `tests/test_providers.py` 中：

- streaming text delta；
- 多个 tool call 按 index 累积；
- 截断 stream 不完成 tool call；
- 非法 JSON 参数；
- provider capability 禁止 tools 或 streaming；
- Anthropic 连续 tool result 合并。

### 6.3 MCP 如何接入

从 `tracecoder/mcp/manager.py`、`adapter.py` 和 `app/factory.py` 的 `McpToolProvider` 开始。理解三点：

1. MCP server 的工具被适配成 TraceCoder 的统一 `Tool`；
2. MCP 工具名称需要避免和内置工具冲突；
3. MCP 工具仍然属于权限控制范围，不能因为来自外部 server 就自动可信。

---

## 7. 第六阶段：测试驱动地证明你真的读懂了

不要把测试只当作“最后跑一下”。它们是最具体的行为说明。推荐按下面顺序读：

| 学习目标 | 先读的测试 |
| --- | --- |
| Agent 工具事务 | `tests/test_agent_tool_flow.py` |
| 循环上限 | `tests/test_agent_loop_limits.py` |
| JSONL replay | `tests/test_context_store.py`、`tests/test_context_runtime_replay.py` |
| 压缩保护 | `tests/test_context_compaction_pipeline.py` |
| 权限决策 | `tests/test_permissions_policy.py`、`tests/test_permission_registry.py` |
| 写前 review | `tests/test_prewrite_review.py`、`tests/test_review_view.py` |
| Provider 协议 | `tests/test_providers.py` |
| 端到端 agent | `tests/test_agent_e2e.py`、`tests/test_agent_context_loop.py` |
| benchmark 统计 | `tests/test_harbor_tooling.py` |

每读一个测试，强迫自己写出四行笔记：

```text
输入是什么？
系统经过了哪几个边界？
可观察输出是什么？
这个测试防止哪一种回归？
```

完整测试命令：

```powershell
.venv\Scripts\python.exe -m pytest
```

---

## 8. 两周学习计划

### 第 1–3 天：跑通主链路

- 熟悉目录和启动入口；
- 读 `app/factory.py`、`session/bootstrap.py`、`agent/session.py`；
- 画出 `AgentLoop` 的同步路径；
- 跑 Agent loop 和 tool flow 测试；
- 能用 60 秒讲完一次普通只读请求。

### 第 4–6 天：工具、权限和写前 review

- 读 registry、permission policy、permission manager；
- 用测试预测 allow/ask/deny；
- 讲清 pending permission 的暂停/恢复；
- 讲清为什么 write 还有可信 diff 和快照校验；
- 完成一个小改动：为一个测试工具增加权限 spec，并补测试。

### 第 7–10 天：会话、上下文和压缩

- 读 JSONL writer/store/replay；
- 观察 event、view、provider message 三层差异；
- 读 L1–L4 压缩和 archive；
- 画出 prompt-too-long 的恢复路径；
- 能解释一项 fresh source protection 测试。

### 第 11–12 天：Provider、streaming、MCP

- 读统一类型和两个 provider adapter；
- 解释 tool call 参数为什么要等 stream 完成；
- 了解 MCP tool 如何进入 registry 和权限边界；
- 选一个 provider 测试，手动跟踪输入输出字段。

### 第 13–14 天：面试模拟和证据整理

- 准备项目 60 秒、3 分钟、10 分钟三个版本；
- 准备一个失败案例和一个设计取舍；
- 用测试名称证明每个关键结论；
- 复核 Harbor 指标的分母、指标含义和运行条件；
- 不把“设计文档里计划的能力”说成“代码已经实现的能力”。

---

## 9. 面试答题模板

### “你这个项目最难的地方是什么？”

推荐回答结构：问题 → 约束 → 设计 → 验证 → 局限。

示例：

> 最难的是上下文压缩不能只追求 token 数下降。Coding Agent 需要保留精确源码、tool-call transaction 和错误证据，否则模型可能基于陈旧上下文生成错误 patch。我把 JSONL 持久事实、SessionView 和 provider 投影分开，先用确定性 L1–L3 处理安全候选，最后才用 L4 生成 checkpoint；测试覆盖了 fresh source protection、archive recovery、tool sequence 合法性和 replay 幂等。它仍不是完整数据库事务，也没有 provider tokenizer，这是当前实现边界。

### “你的权限系统安全吗？”

不要回答“很安全”。应该说：

> 它把安全边界放在程序侧。工具调用先变成带 action、target、cwd 的 PermissionRequest，由 grant 和 policy 返回 ALLOW/ASK/DENY；ASK 时 executor 不会运行。直接文件修改还会生成可信 diff，并在真正应用前重新核对文件快照。它能降低误执行和并发覆盖风险，但不等于操作系统级沙箱，也不能把 prompt 当作安全边界。

### “benchmark 的 96.38% 代表什么？”

要严格区分：

- 这是特定 Aider Polyglot 运行条件下的结果；
- 96.38% 是 reward-only 的有效样本通过率，简历中的 213/221 要把分母讲清楚；
- Harbor 端到端 Mean 是另一项指标；
- provider/network/Docker/verifier timeout 等基础设施问题不能和真实 reward=0 混为一谈；
- 该结果不能泛化成所有任务、所有模型和所有配置下的成功率。

详细边界看 `benchmark/harbor/README.md` 和仓库维护的 benchmark 结果包说明。面试官如果追问，优先讲可复现配置、trial artifact、verifier output 和失败分类。

---

## 10. 最终自测清单

在你开始投递前，应该可以不看源码回答：

- `AgentLoop` 和 `ToolExecutor` 的职责边界是什么？
- 为什么 assistant tool call 必须先落库？
- 为什么未知工具返回 `ToolResult` 而不是异常？
- `ALLOW`、`ASK`、`DENY` 分别会走哪条路径？
- `allow always` 为什么需要 scope？
- 写前 diff 和权限确认解决的是同一个问题吗？
- JSONL、SessionView、ChatMessage 三者是什么关系？
- L1、L2、L3、L4 各自牺牲什么、保护什么？
- 为什么 streaming tool call 不能收到一半就执行？
- prompt-too-long 后为什么可以压缩并重试？
- `SessionBootstrap` 为什么是统一装配入口？
- benchmark 中 reward-only 和端到端指标有什么区别？

如果这些问题你都能结合一个具体源码文件和一个具体测试回答，你就不只是“简历上写过 Agent”，而是已经具备把这个 Agent 的设计讲清楚、改动它并验证改动的能力。

---

## 关联资料

- [架构说明](ARCHITECTURE.zh-CN.md)
- [代码阅读指南](CODEBASE_READING_GUIDE.zh-CN.md)
- [Agent 主循环护栏](AGENT_LOOP_GUARDRAILS.zh-CN.md)
- [上下文管理设计](CONTEXT_MANAGEMENT_DESIGN.zh-CN.md)
- [权限设计](PERMISSIONS_DESIGN.zh-CN.md)
- [工具设计](TOOLS_DESIGN.zh-CN.md)
- [Provider 设计](PROVIDERS_DESIGN.zh-CN.md)
- [MCP 客户端](MCP.zh-CN.md)
- [Harbor 评测指南](../benchmark/harbor/README.md)
