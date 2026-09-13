# TraceCoder AI Agent 学习教材

## 应届实习生版：从软件基础到能读懂、能修改、能讲清楚

这不是一份术语表，也不是把仓库所有文件罗列一遍。它是一条带着你读真实项目的学习路线。

你最终要达到的目标不是背住“Agent、MCP、RAG、checkpoint”这些词，而是能够回答：

> 用户说“请修改这个项目并运行测试”之后，TraceCoder 内部到底发生了什么？

以及：

- 模型想调用工具时，谁决定是否允许？
- 工具执行结果怎样回到模型？
- 会话怎样保存，进程退出后怎样恢复？
- 上下文太长时，什么能压缩，什么不能压缩？
- Provider、Textual、AnyIO、MCP 各自处于哪一层？
- 简历上的每个技术点，如何用源码、测试和 benchmark 证明？

---

## 0. 先知道这份教材怎么用

### 0.1 不要从头到尾只读文字

每一节都按下面顺序学习：

```text
先读本节问题
  ↓
看项目中的源码入口
  ↓
跟踪一个对象或一组字段
  ↓
阅读对应测试
  ↓
做小实验
  ↓
用自己的话复述
```

如果只看文字，你会产生“好像懂了”的感觉，但一到面试追问就散掉。真正的学习结果应该是：你能打开代码，指出入口，说明数据怎么流动，并解释一个失败场景。

### 0.2 每个主题使用四遍阅读法

这是仓库官方[代码阅读指南](CODEBASE_READING_GUIDE.zh-CN.md)里最值得照做的方法。

#### 第一遍：给行为命名

先回答“这个模块负责什么”，不要一开始钻实现细节。

例如：

- `AgentLoop`：协调一轮用户任务；
- `ContextBuilder`：把有效会话投影成模型消息；
- `ToolExecutor`：处理工具调用、权限暂停、执行和结果；
- `PermissionManager`：对具体动作做 allow/ask/deny 决策；
- `JsonlSessionStore`：追加事件并从事件重建会话视图。

#### 第二遍：找模块之间的缝

“缝”是可以替换、测试或接入新实现的边界：

- `ChatProvider`：模型协议适配边界；
- `ToolRegistry`：工具 schema 和 executor 的边界；
- `app.ports`：TUI/CLI 和 runner 的边界；
- `SessionBootstrap`：会话装配边界；
- `PermissionRequest`：工具动作和安全策略的边界；
- `ContextBuilder`：持久事实和模型投影的边界。

#### 第三遍：跟数据，不只跟控制流

控制流回答“先调用谁”；数据流回答“对象变成什么、字段有没有丢”。本项目最重要的两条数据流是：

```text
用户文本
  → SessionEvent
  → AgentMessage / MessagePart
  → SessionView
  → ChatMessage
  → ChatRequest
  → provider wire payload
```

```text
ToolCall(id, name, arguments)
  → PermissionRequest(action, target, cwd)
  → PermissionDecision
  → ToolResult
  → role=tool MessagePart(tool_call_id=id)
```

#### 第四遍：马上读测试

每读一个测试，写下四行：

```text
输入是什么？
调用了哪个公开入口？
输出和副作用是什么？
它防止哪一种回归？
```

### 0.3 三种学习节奏

| 时间 | 目标 | 内容 |
| --- | --- | --- |
| 30 分钟 | 建立地图 | 看入口、装配和核心类名 |
| 60–90 分钟 | 吃透一个能力 | 源码 + 调用方 + 测试 + 小实验 |
| 2–3 小时 | 具备改动能力 | 完成一个小功能并补测试 |

每次只选择一个目标。不要今天同时学习 Agent Loop、数据库、MCP 和前端。

---

## 1. TraceCoder 先用一张图看懂

### 1.1 项目解决什么问题

TraceCoder 是一个本地 Python Coding Agent。模型负责提出下一步动作，Python 运行时负责：

- 读取和组织项目上下文；
- 把工具定义发送给模型；
- 检查模型想做的动作；
- 在权限允许时执行本地工具；
- 把工具结果回传给模型；
- 保存事件，支持恢复和压缩；
- 在达到边界、失败或完成时停止。

它的核心不是“聊天”，而是“模型建议 + 程序控制 + 环境反馈”。

### 1.2 四层心智模型

```text
表现层：app/
  Textual TUI、命令、选择器、流式展示

装配层：app/factory.py、session/bootstrap.py、app/ports.py
  把 provider、tools、permissions、session、context 接起来

编排层：agent/
  AgentLoop、AgentSession、ToolExecutor、停止和恢复策略

事实与副作用层：context/、session/、tools/、permissions/、providers/
  事件持久化、上下文投影、工具执行、权限裁决、协议适配
```

记住三条设计原则：

1. JSONL 事件是持久事实，模型消息只是投影；
2. AgentLoop 决定何时做什么，工具和 provider 负责具体做什么；
3. prompt 可以引导模型，但不能代替权限和安全代码。

### 1.3 先看这些文件

第一次建立地图时，按这个顺序打开：

1. `README.zh-CN.md`
2. `pyproject.toml`
3. `tracecoder/cli.py`
4. `tracecoder/app/factory.py`
5. `tracecoder/session/bootstrap.py`
6. `tracecoder/agent/session.py`
7. `tracecoder/agent/loop.py`
8. `tests/test_agent_loop_limits.py`

重点不是记住文件名，而是回答：谁创建谁、谁调用谁、谁保存事实、谁拥有最终裁决权。

---

## 2. 预备知识：只补项目真正用到的基础

这一部分应该快速学完，不要让基础知识把你挡在 Agent 之外。

### 2.1 Python：函数、对象、dataclass

你需要能看懂这种结构：

```python
@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]
```

理解四点：

- 函数把一项责任命名出来；
- 类把数据和行为放在一个边界内；
- `dataclass` 减少结构化数据的样板代码；
- `frozen=True` 适合不希望在传递途中被悄悄修改的请求或快照。

TraceCoder 中可以读：

- `tracecoder/providers/types.py`
- `tracecoder/permissions/types.py`
- `tracecoder/context/models.py`

### 2.2 异常、结构化错误和可恢复性

不是所有错误都应采用同一种方式：

| 错误 | 处理思路 |
| --- | --- |
| unknown tool、坏参数 | 生成结构化 `ToolResult`，让模型看到失败证据 |
| 网络抖动、限流 | 按 retry policy 和次数退避重试 |
| prompt 太长 | 触发上下文压缩，再有界重试 |
| 非法事件链、核心不变量破坏 | 明确抛异常，阻止产生不可信状态 |

你要理解“错误处理”不是到处写 `try/except`，而是判断错误能否恢复、谁能理解它、继续执行是否会扩大损失。

### 2.3 async、线程和 AnyIO

```python
async def run() -> ChatResponse:
    return await provider.acomplete(request)
```

`async def` 定义异步函数，`await` 等待异步结果；异步的价值主要是等待网络或其他 I/O 时不阻塞当前事件循环。

TraceCoder 的实际组合是：

- `AgentLoop.run_user_turn()` 提供统一的异步接口；
- 同步实现可以通过 `anyio.to_thread.run_sync()` 放到线程；
- Textual 自己运行在事件循环中，不能被同步阻塞调用卡住；
- `CancellationToken` 在 runner、loop 和工具边界传播；
- streaming provider 通过异步事件逐步把结果交给 UI。

不要混淆：异步不等于并行；线程不等于更快；是否能并发执行要看工具有没有副作用和共享资源。

### 2.4 HTTP/API

模型 API 本质上也是 HTTP：请求包含 URL、headers、body；响应包含状态码和 body。

你至少要知道：

- 4xx 往往表示请求、参数或权限问题；
- 429 可能是限流；
- 5xx 和连接错误可能暂时可重试；
- 收到 HTTP 200 不代表任务完成，工具执行和 verifier 仍可能失败。

### 2.5 Git 和 pytest

最低要求：

```text
git status：我改了什么？
git diff：具体差异是什么？
git log：这个设计为什么出现？
pytest：行为是否仍然成立？
```

测试不是证明代码永远正确，而是把重要行为和边界固定下来。

---

## 3. 主线案例：用户说“修改项目并运行测试”

这一章先不深入每个细节，只完整走一遍。

### 3.1 应用如何被装配

从 `tracecoder/app/factory.py:create_tracecoder_app()` 开始：

```text
加载配置和 model profile
  ↓
创建 JsonlSessionStore
  ↓
创建 SandboxAccess 和 builtin tools
  ↓
连接 MCP，合并 MCP tools
  ↓
创建 ChatProvider
  ↓
通过 SessionBootstrap 创建 AgentSession
  ↓
创建 ContextWindowManager
  ↓
创建 AgentChatRunner
  ↓
创建 TraceCoderApp
```

为什么要集中装配？因为 session 需要 tools、permissions 和 skills；runner 需要 session、provider 和 context manager；如果这些对象在各处随意创建，create/resume/fork 很容易出现不同的行为。

### 3.2 用户输入怎样进入 runner

Textual 界面和 CLI 最终都要进入 `AgentChatRunner`。读 `tracecoder/app/runtime.py`：

- `run_user_turn()` 是同步外层入口；
- `arun_user_turn()` 是异步入口；
- `_start_turn()` 创建取消 token 和 AgentLoop；
- `_create_loop()` 注入当前 session、provider、tools、context manager、limits 等；
- `_finish_agent_result()` 把 AgentLoop 的结果转成 UI 可使用的 response、pending input 和显示状态。

### 3.3 一轮请求的核心路径

简化后的真实流程：

```text
用户文本
  ↓
append_user_message
  ↓
rebuild SessionView
  ↓
ContextBuilder 构造 ChatMessage
  ↓
ChatProvider.complete / astream
  ↓
ChatResponse
  ├─ 无 tool_calls：最终回答
  └─ 有 tool_calls：进入 ToolExecutor
          ↓
       权限检查
          ↓
       工具执行或暂停
          ↓
       append tool result
          ↓
       再次请求模型
```

### 3.4 你第一次学习后的通关标准

不看资料，你应该能回答：

1. `factory` 为什么不是业务逻辑，而是组合根？
2. `AgentChatRunner` 为什么不直接调用具体工具？
3. `AgentLoop` 为什么需要 `ContextBuilder`？
4. 模型返回 tool call 后，为什么还要再次请求模型？
5. 哪个对象决定工具是否允许执行？

---

## 4. Agent Loop：从“模型回答”到“任务执行”

### 4.1 Agent 是什么

普通聊天：

```text
用户问题 → 模型回答
```

Agent：

```text
用户目标
  → 模型提出行动
  → 程序验证并执行
  → 环境返回观察
  → 模型根据观察继续行动
  → 完成或触发边界
```

所以 Agent 的关键不是“模型会思考”，而是有状态、有工具、有反馈、有控制。

### 4.2 `AgentLoop` 的职责

读 `tracecoder/agent/loop.py` 的类说明和这些函数：

- `run_user_turn()`：统一异步入口；
- `_run_user_turn_sync_impl()`：普通 turn 开始；
- `_prepare_main_provider_request()`：准备一次模型请求；
- `_complete_once()`：问模型一次；
- `_run_tool_loop_interactive()`：核心循环；
- `_continue_tool_loop_from_response()`：处理 tool call 并继续；
- `_complete_turn()`：持久化最终 response；
- `_complete_once_with_recovery()`：provider 错误、重试和 prompt-too-long 恢复。

它是协调器，不应该塞入具体 shell、HTTP 或 Textual widget 的实现。

### 4.3 Agent Loop 状态机

```text
IDLE
  ↓ 用户提交
PERSIST_USER_INPUT
  ↓
PREPARE_CONTEXT
  ├─ 超预算 → COMPACT → PREPARE_CONTEXT
  └─ 正常
       ↓
CALL_MODEL
  ├─ 普通文本 → COMPLETE
  ├─ tool call → VALIDATE/EXECUTE
  └─ provider error → RETRY / COMPACT_AND_RETRY / ERROR

EXECUTE_TOOL
  ├─ ALLOW → 执行
  ├─ ASK → 保存 pending，等待用户
  └─ DENY → 追加拒绝结果
       ↓
APPEND_TOOL_RESULT
       ↓
CALL_MODEL
```

### 4.4 为什么 tool call/result 是协议事务

模型返回的工具调用不是普通文本：

```text
assistant:
  tool_call id=call_123 name=read arguments={...}
tool:
  tool_call_id=call_123 result={...}
```

执行顺序必须是：

1. 先追加 assistant tool call；
2. 再执行工具；
3. 再追加匹配的 tool result；
4. 再把合法历史交给 provider。

如果缺少 assistant tool call、tool result ID 不匹配，或者压缩时切断这对消息，provider 可能拒绝整个请求。

重点测试：

- `tests/test_agent_tool_flow.py::test_assistant_tool_calls_are_persisted_as_tool_call_parts`
- `tests/test_agent_tool_flow.py::test_tool_results_are_persisted_with_matching_tool_call_id`
- `tests/test_agent_tool_flow.py::test_context_builder_rejects_orphan_tool_result`
- `tests/test_agent_tool_flow.py::test_context_builder_rejects_assistant_tool_call_without_tool_result`

### 4.5 Loop 为什么必须有护栏

模型可能反复调用工具、卡在错误循环或长时间不返回。读 `tracecoder/agent/loop_limits.py`：

- `max_tool_rounds`：工具轮次上限；
- provider call limit：模型调用次数上限；
- turn timeout：一轮任务的时间上限；
- cancellation：用户中断；
- stagnation guard：检测重复、无进展行为。

默认、SWE-lite 和 summary profile 的参数不同，但关闭某一个上限，不等于关闭权限和协议校验。

### 4.6 Provider retry 和失败恢复

读 `tracecoder/agent/provider_retry.py`、`providers/errors.py` 和 loop 中的 recovery 函数。

判断逻辑大致是：

```text
ProviderError
  ├─ retryable 且未超次数 → backoff 后重试
  ├─ requires_compaction 且尚未压缩 → 压缩后重试
  └─ 其他 → 抛出或结束本轮
```

streaming 失败时，已经收到的局部 delta 不能当成最终回答，否则 UI 会显示一段实际上没有成功的半截结果。

### 4.7 Agent Loop 实验

先不改产品代码，读 fake provider 测试并画出：

```text
provider response 1：read tool call
tool execution：成功/失败
provider response 2：最终文本
```

然后回答：

- provider 调用几次？
- assistant tool call 什么时候落库？
- tool result 由谁生成？
- 如果第二次 provider 调用失败，之前的工具结果是否还存在？
- 如果达到轮次上限，最终 response 的 finish reason 是什么？

---

## 5. Tools：模型如何影响真实环境

### 5.1 Tool 的四个部分

读 `tracecoder/tools/types.py` 和 `registry.py`。一个工具通常包含：

```text
name
definition/schema
executor
permission spec
```

其中：

- `definition` 是给模型看的能力声明；
- `executor` 是程序真实执行的函数；
- `permission spec` 描述如何把参数转换成权限请求；
- `ToolResult` 是统一的成功或失败结果。

schema 不是安全边界。模型看得到工具，不代表它自动拥有执行权；模型看不到工具，也不代表程序可以省略执行侧检查。

### 5.2 Registry 的作用

`ToolRegistry` 做三件事：

1. 按工具名保存工具；
2. 向 provider 返回工具 definitions；
3. 根据模型返回的 name 找到 executor。

未知工具、参数不是 dict、参数错误和 executor 异常都会规范化成 `ToolResult`，而不是把整个 Agent Loop 直接打断。

### 5.3 内置工具为什么分组

读 `tracecoder/tools/builtin.py`。内置工具按能力分类：

- inspection：`ls`、`view`、`grep`、`glob`、`tree`；
- git：status、diff、log；
- mutation：write、edit、delete、apply_patch；
- execution：shell、python、process；
- network：fetch、web search；
- interaction：ask user、think。

默认只读工具更容易安全启动；写入、命令执行和网络能力在装配时显式启用。

### 5.4 Tool 与 Agent 的依赖方向

工具不应该 import `AgentLoop`。工具只返回结构化结果，由 loop 决定：

- 是否再次请求模型；
- 是否停止；
- 是否触发压缩；
- 是否等待用户；
- 是否记录 telemetry。

这样工具可以独立测试，也不会让叶子层和编排层形成环依赖。

### 5.5 Tool 学习实验

做一个最小只读工具时，按顺序完成：

1. 定义清晰的参数 schema；
2. executor 返回 `ToolResult`；
3. 如果访问本地资源，增加 permission spec；
4. 把它放进正确的 builtin 分类；
5. 测试成功、坏参数、权限 ask/deny；
6. 测试 tool result 的 `tool_call_id` 配对。

不要直接在 loop 里增加 `if tool_name == "my_tool"`。如果一个工具必须依赖 session 状态，应通过 session registry 注入。

---

## 6. 权限和执行安全：真正的安全边界在哪里

### 6.1 三个概念先分开

- 认证：你是谁；
- 授权：你能做什么；
- 审计：你做过什么。

在 TraceCoder 中：

- provider API key 解决 provider 侧身份；
- permission policy 解决本地动作授权；
- JSONL、grant、tool result 和 telemetry 提供执行轨迹。

### 6.2 权限链路

```text
ToolCall
  ↓ tool permission spec
PermissionRequest(action, target, cwd, metadata)
  ↓
PermissionManager.preflight()
  ├─ 命中 grant → 使用 grant
  └─ 未命中 → DefaultPermissionPolicy
                ↓
             ALLOW / ASK / DENY
```

对应源码：

- `tracecoder/permissions/types.py`
- `tracecoder/permissions/policy.py`
- `tracecoder/permissions/manager.py`
- `tracecoder/permissions/grants.py`
- `tracecoder/tools/permission_registry.py`
- `tracecoder/agent/tool_execution.py`

### 6.3 三种决策分别发生什么

| 决策 | 行为 |
| --- | --- |
| `ALLOW` | 才允许进入 executor |
| `ASK` | 不执行；构造 `UserInputRequest` 并保存 pending |
| `DENY` | 不执行；追加结构化拒绝结果，让模型看到原因 |

`ASK` 不是“先执行再问用户”，而是严格的暂停状态。

### 6.4 mode 和 grant

`PermissionMode` 有：


- `standard`：常规保守模式；
- `aggressive`：对部分标记为可自动执行的动作更宽松；
- `bypass`：更宽松的工作模式，但仍经过统一 dispatch、记录和硬检查。

长期授权不是一句自然语言，而是带范围的 `PermissionGrant`。常见 scope：

- exact path；
- path tree；
- command prefix；
- host；
- environment key；
- MCP tool。

`FilePermissionGrantStore` 将 grant 保存到数据目录的 `permissions.json`；deny grant 优先于 allow grant。

### 6.5 写文件为什么需要 prewrite review

权限允许回答的是：

> 这个动作类型和目标，现在是否可以执行？

写前 review 还要回答：

> 这一次具体准备修改哪些内容？

直接文件修改流程：

```text
write/edit/apply_patch/delete
  ↓
构造可信 unified diff
  ↓
保存 before snapshot
  ↓
展示 review
  ↓ 用户确认
再次核对 snapshot
  ↓
执行原始 ToolCall
```

如果用户确认后文件被其他进程修改，snapshot recheck 会阻止过期 preview 覆盖新内容。这是应用层的乐观并发保护，不是文件系统级原子事务。

`shell` 刻意没有同样的任意文件预览，因为任意 shell 命令的完整影响无法安全提前计算。

### 6.6 权限暂停为什么不能当成普通聊天

如果 assistant tool call 已经落库，用户回答“允许”不能简单追加成普通 `user` 消息：

```text
错误：assistant(tool_call) → user("允许")
```

正确做法是：

```text
assistant(tool_call)
  ↓ PendingPermissionExecution
等待用户
  ↓
tool(result, tool_call_id=原始 ID)
  ↓
继续请求模型
```

重点读 `AgentLoop.resume_with_user_input()` 和 `AgentSession` 中的 pending 相关代码。

### 6.7 权限学习实验

预测并运行 `tests/test_permissions_policy.py`：

1. 项目内普通读取；
2. 读取 `.env`；
3. 项目外读取；
4. aggressive 模式下普通项目写入；
5. 删除项目外文件；
6. 读取 `OPENAI_API_KEY`；
7. 带 shell 控制操作符的命令；
8. MCP tool。

然后读 `tests/test_prewrite_review.py`，证明：

- review 生成时不会真正写文件；
- 删除和移动可以被预览；
- 重复 patch path 会失败；
- 外部修改会让 preview 失效。

---

## 7. 会话、事件和恢复：Agent 怎样记住过去

### 7.1 为什么需要持久化

进程内的 `AgentSession`、pending 对象和 UI 状态会随进程退出消失。想要 resume，就必须保存事实：

```text
<data-root>/sessions/<session-id>.jsonl
<data-root>/permissions.json
<data-root>/archives/<session-id>/
```

### 7.2 JSONL 不是普通日志

普通日志主要给人看；TraceCoder 的 JSONL 还承担事实来源的角色。它需要支持：

- 按追加顺序保存事件；
- 从头 replay；
- 重建 `SessionView`；
- 追加 compaction/checkpoint 事件；
- resume/fork；
- 发现非法事件链时明确失败。

重点读：

- `tracecoder/context/events.py`
- `tracecoder/context/writer.py`
- `tracecoder/context/store.py`
- `tracecoder/context/models.py`
- `tracecoder/agent/session.py`

### 7.3 三层视图

```text
append-only event
  ↓ replay
SessionView
  ↓ ContextBuilder
provider-facing ChatMessage[]
```

三者不能混为“聊天记录”：

| 层 | 用途 |
| --- | --- |
| event | 事实、审计、恢复 |
| `SessionView` | 当前有效状态 |
| provider messages | 某一次请求所需的窗口和投影 |

provider messages 可以因为压缩而变化，但不应该反过来成为唯一事实来源。

### 7.4 一条消息的形态变化

```text
"请查看 README.md"
  ↓ writer.append_user_message()
user_message event
  ↓ store.rebuild_session_view()
AgentMessage(role=user)
  ↓ ContextBuilder.build_provider_messages()
ChatMessage(role=user)
  ↓ provider adapter
厂商 wire message
```

tool call 和 tool result 也要经历同样转换，并且保留 `tool_call_id`。

### 7.5 resume 的准确说法

不要说“resume 从 checkpoint 后恢复”。更准确的说法是：

> resume 重新读取并 replay 完整 JSONL 事实，恢复当前 session view；checkpoint 决定 provider 后续如何投影旧历史和 tail，archive 原文仍然存在。

对应测试：

- `tests/test_context_store.py`
- `tests/test_context_runtime_replay.py`
- `tests/test_context_resume.py`

### 7.6 会话学习实验

构造一个包含以下内容的 session：

```text
user message
assistant tool call
tool result
assistant final response
```

然后逐个回答：

- 哪些是 event？
- 哪些是 message part？
- `rebuild_session_view()` 做了什么？
- 如果只剩 tool result，会发生什么？
- 如果进程在 assistant tool call 后退出，resume 如何处理？

---

## 8. Context Engineering：不是把历史全部塞给模型

### 8.1 Prompt、Context、Memory 的区别

- Prompt：给模型的指令和输入；
- Context：这一次请求实际发送给模型的内容；
- Memory：跨请求保留的事实、状态或摘要。

在 TraceCoder 中：

```text
JSONL facts → SessionView → ContextBuilder → provider messages
```

这比“维护一个巨大字符串”更可靠，因为可以分别控制事实、有效状态和本次投影。

### 8.2 ContextBuilder 的职责

读 `tracecoder/context/context_builder.py`。它要做的不只是截取最后几条消息：

- 投影 system、user、assistant、tool 消息；
- 组合 checkpoint summary 和未压缩 tail；
- 检查 tail 边界；
- 检查 tool call/result 配对；
- 防止 provider 收到孤立 tool result；
- 处理持久化附件的 provider-neutral 内容。

### 8.3 token budget 的基本公式

TraceCoder 当前使用字符数近似 token，不绑定具体 provider tokenizer。预算大致是：

```text
usable window = context window × 95%
input capacity = usable window - output reserve
high watermark = input capacity × 90%
low watermark = input capacity × 72%
```

固定成本包括 system prompt 和 tool schema；历史成本包括有效消息。工具 schema 也会消耗上下文，不应该重复粘贴进 prompt 正文。

重点读 `tracecoder/context/token_budget.py` 和 `context/manager.py`。

### 8.4 L1–L4 压缩路线

```text
输入接近高水位
  ↓
L1：裁掉旧任务普通文本
  ↓ 不够
L2：压缩 derived tool output
  ↓ 不够
L3：archive 原文并替换成 placeholder
  ↓ 不够
L4：生成 coding-handoff checkpoint
```

### 8.5 每层到底保护什么

| 层级 | 可以处理 | 必须保护 |
| --- | --- | --- |
| L1 | 已确认属于旧任务的普通文本 | 最新需求、tool transaction、fresh source |
| L2 | log/search/diff/JSON 等 derived output | 失败块、路径、行号、diff 形状；候选必须更短 |
| L3 | stale、superseded、duplicate、旧 derived result | 先保存 archive 原文，不切断整个 transaction |
| L4 | 旧历史的语义 handoff | summary、tail 边界、source fingerprint、协议合法性 |

### 8.6 生命周期：fresh、stale、derived

压缩不能只看字符串长度，还要判断内容生命周期：

- `fresh`：仍然对应当前文件版本的源码读取；
- `stale`：之后已成功修改过同一路径；
- `superseded`：被后来的读取覆盖；
- `derived`：日志、搜索、diff、列表等派生结果；
- `duplicate`：和后续派生结果重复。

未知工具、模糊 metadata 和 shell 输出要保守处理，宁可少压缩，也不要猜错源码证据。

### 8.7 archive 和 retrieve

L3 不等于删除原文：

```text
原始 tool result
  ↓ archive
archives/<session>/<archive-id>.txt + metadata
  ↓
当前上下文中的 bounded placeholder
```

模型需要时通过 session-scoped `retrieve_archive`，按 archive id、有界 query 和 max chars 取回原文。它不能变成任意文件读取或跨 session 读取。

### 8.8 checkpoint handoff 的真实含义

`Checkpoint` 不是一段随便生成的摘要。它包含：

- `summary`：旧历史工作摘要；
- `tail_start_message_id`：未压缩 tail 的起点；
- `covered_until_message_id`：摘要覆盖边界；
- `source_fingerprint`：摘要基于哪一版输入；
- `strategy_version`：策略版本。

`LlmCompactService` 的安全流程是：

```text
构造 L4 source
  ↓
生成 candidate
  ↓
校验摘要结构和 tool 边界
  ↓
只有成功 candidate 才 commit checkpoint_created event
```

因此，模型摘要失败或边界非法时，不应该直接污染持久状态。

### 8.9 压缩实验

重点读 `tests/test_context_compaction_pipeline.py` 中这些类型的测试：

- fresh source 不被压缩；
- L2 derived result 被压缩；
- L3 archive 后仍可恢复；
- L4 不切断 tool sequence；
- task switch 触发旧任务整理；
- 重复压缩是幂等的。

每个测试必须回答：

```text
省了什么 token？
保护了什么证据？
原文在哪里？
重复执行会不会产生重复副作用？
```

---

## 9. Provider、Streaming、Textual、AnyIO 和 MCP

这一章专门覆盖你简历中的技术栈。

### 9.1 Provider adapter

上层只依赖内部类型：

```text
ChatRequest
ChatResponse
ChatMessage
ToolDefinition
ToolCall
ChatStreamEvent
ProviderError
```

Provider adapter 负责：

- 内部消息转厂商消息；
- 内部 tool schema 转厂商 tools；
- 解析厂商响应和流式 chunk；
- 统一 usage、finish reason、capability 和错误分类。

重点读：

- `tracecoder/providers/base.py`
- `tracecoder/providers/types.py`
- `tracecoder/providers/openai_compatible.py`
- `tracecoder/providers/anthropic_provider.py`
- `tracecoder/providers/errors.py`

不要让 OpenAI/Anthropic 的专属字段散落进 `AgentSession` 或 `AgentLoop`。

### 9.2 OpenAI-compatible 与 Anthropic Messages

两条路径共享同一套内部契约，但 wire format 不同：

```text
OpenAI-compatible：tools → function schema
Anthropic：tools → input_schema，system 独立字段
```

需要对齐的行为包括：

- 普通 complete；
- streaming；
- tool choice；
- tool call/result；
- usage；
- finish reason；
- 错误分类；
- capability 限制。

### 9.3 Streaming 为什么不能边收到边执行工具

tool arguments 可能分散在多个 chunk：

```text
chunk 1：tool name
chunk 2：参数前半段
chunk 3：参数后半段
chunk 4：tool call 完成
chunk 5：message_completed
```

文本 delta 可以立即给 UI；工具必须等完整消息完成后再交给 `ToolExecutor`。否则可能执行半个 JSON 或错误参数。

### 9.4 Textual 的真实职责

`tracecoder/app/tui.py` 中的 `TraceCoderApp` 继承 Textual `App`，通过 `compose()` 组合：

- topbar；
- transcript/output；
- task plan panel；
- activity 状态；
- composer 输入框。

UI 只负责输入和展示：

```text
Textual widget / event
  ↓
ChatRunnerLike / CommandHandlerLike
  ↓
AgentLoop / Session / Provider / Tools
```

`app/runtime.py` 中的 `AgentChatRunner` 负责把当前 session、stream event、tool event、取消和暂停恢复接到 AgentLoop。UI 不应该自己改 JSONL、自己执行工具或自己做权限裁决。

### 9.5 AnyIO 和两条事件线

TraceCoder 中有两条不同事件线：

```text
provider event：reasoning、text delta、tool-call delta、message completed
local event：tool started、finished、denied、permission asked、prewrite review
```

模型没有新文本时，本地工具仍可能正在运行；UI 不能把本地工具状态伪造成 assistant 文本。

### 9.6 MCP 的接入边界

```text
MCP server
  ↓ McpManager 连接和发现
McpToolDescription
  ↓ mcp/adapter.py
TraceCoder Tool
  ↓
统一 registry / permission / result 流程
```

MCP 的价值是标准化外部工具接入，不是给外部工具自动授权。MCP tool 仍要经过本地权限策略、超时和错误处理。

### 9.7 这一章的测试

重点读：

- `tests/test_providers.py`：tool parsing、streaming、截断、能力开关；
- `tests/test_provider_errors.py`：错误归类；
- `tests/test_app_runtime.py`：runner、取消、pending resume；
- `tests/test_app_tui.py`：TUI 行为；
- `tests/test_mcp_adapter.py`：MCP 工具适配。

---

## 10. 任务规划、验证和完成判断

### 10.1 为什么 Agent 需要计划

短任务可以直接执行；长任务可能包含：

```text
理解需求 → 找文件 → 修改代码 → 运行测试 → 修复失败 → 汇报结果
```

计划可以把长任务拆成可观察状态，但计划也会消耗上下文，且模型写“完成”不等于真的完成。

### 10.2 TraceCoder 中的 TaskPlan

重点看：

- `tracecoder/planning/service.py`
- `tracecoder/planning/reducer.py`
- `tracecoder/planning/projection.py`
- `tracecoder/agent/task_plan_policy.py`
- `tracecoder/tools/task_plan_support.py`

需要掌握：

- plan 有稳定任务 ID 和 revision；
- 修改计划要携带预期 revision，防止覆盖别人刚更新的状态；
- `SessionView.task_plan` 来自持久事件回放；
- TUI 面板只是 plan 的投影，不自行推导权威状态；
- 计划完成后仍可能需要 completion gate 验证执行证据。

### 10.3 “模型说完成”和“任务真的完成”

模型可能说“已完成”，但程序还需要看：

- 是否修改了预期文件；
- 测试是否通过；
- 是否有未解决错误；
- 是否有未完成后台任务；
- 是否满足 benchmark verifier；
- 是否还存在未完成的计划项。

这就是 execution evidence 和 completion gate 的意义。

### 10.4 学习实验

找一个 TaskPlan 测试，回答：

1. 谁生成权威计划？
2. revision 冲突时怎么处理？
3. 为什么任务计划要持久化？
4. TUI 为什么不直接修改任务状态？
5. completion gate 为什么不能只看 assistant 文本？

---

## 11. 评测 Agent：Harbor 和你简历上的数字

### 11.1 Coding Agent 评测什么

不是看模型回答是否流畅，而是看任务是否真正完成：

- 代码是否通过测试；
- 文件修改是否正确；
- 工具是否成功执行；
- 是否超时或陷入循环；
- provider、Docker、verifier 失败是什么原因；
- 结果是否可追溯和复现。

### 11.2 Harbor 的组成

先读 `benchmark/harbor/README.md`，再读：

- `benchmark/harbor/shared/tracecoder_agent.py`：TraceCoder 如何作为 agent 运行；
- `benchmark/harbor/shared/summarize.py`：结果如何汇总；
- `benchmark/harbor/aider_polyglot/aider_feedback_plugin.py`：允许的 feedback repair 协议。

Harbor 提供：

```text
任务集
  + 隔离环境
  + agent adapter
  + verifier
  + trial/job artifacts
```

### 11.3 你简历上的指标必须这样讲

```text
完整任务数：225
明确 reward 数：221
reward 通过数：213
reward-only pass@1：213 / 221 = 96.38%
Harbor 端到端 Mean：94.67%
```

不能把 `213/221` 写成 `213/225`。两个口径不同：

- reward-only：只看拿到明确 reward 的有效样本；
- 端到端 Mean：反映完整运行链路，可能受到环境、agent 启动、provider、verifier 和任务结果影响。

没有 reward 的任务不能直接归类为 agent 失败，要检查 `result.json`、verifier 输出、任务日志和 job lock，区分：

- reward 文件缺失；
- 网络/provider 异常；
- Docker 或依赖环境失败；
- verifier 超时；
- agent 真正没有完成任务。

### 11.4 Aider Polyglot feedback 的边界

Aider Polyglot 允许特定协议下的一次 verifier feedback repair。它不是普通的 `ask_user`，也不能默认套到 Terminal-Bench 或其他不允许反馈的 benchmark 上。

面试时要体现你知道：评测协议本身也是系统约束，不能为了提高分数偷偷给 agent 注入隐藏测试或额外信息。

---

## 12. 从学习到修改：四个递进实验

### 实验 1：只读一个调用链

目标：不改代码，解释一次只读请求。

阅读：

```text
app/runtime.py
agent/loop.py
context/context_builder.py
tools/registry.py
tests/test_agent_tool_flow.py
```

产出：画出 `user → provider → tool → result → provider`。

通关标准：能够指出 `tool_call_id` 从哪里产生、在哪里保存、在哪里再次发送。

### 实验 2：fake provider

让 fake provider 返回：

```text
第一次：一个 read/view tool call
第二次：最终文本
```

观察：

- provider 被调用几次；
- session 追加哪些 event；
- tool result 的 `ok` 和 `content`；
- 最终 response 如何持久化。

### 实验 3：权限暂停

模拟一个 write 请求：

```text
模型请求写文件
  → ASK
  → 文件不能变化
  → 保存 pending
  → 用户允许
  → snapshot recheck
  → 执行原始 call
```

通关标准：你能用测试证明“ASK 时 executor 没被调用”。

### 实验 4：添加一个安全工具

实现一个项目内只读工具，完成：

1. `ToolDefinition`；
2. executor；
3. 参数错误处理；
4. permission spec；
5. registry 装配；
6. 正常、失败、deny/ask 测试；
7. provider tool result 配对测试。

这一步完成后，你才算从“会读项目”进入“能扩展项目”。

---

## 13. 排障手册：遇到问题先按层查

### 13.1 模型没看到工具

检查顺序：

1. `create_builtin_registry()` 是否注册；
2. `SessionBootstrap` 是否拿到当前 tools；
3. MCP 是否连接成功；
4. session registry 是否注入；
5. provider capability 是否支持 tools；
6. `ChatRequest.tools` 是否真的包含 definition。

### 13. Tool call 后 provider 报历史非法

检查：

1. assistant tool call 是否先落库；
2. tool result 是否存在；
3. `tool_call_id` 是否匹配；
4. ContextBuilder 是否把孤立 result 投影出来；
5. 压缩或 checkpoint 是否切断了 pair。

### 13. 用户允许后没有继续

检查：

1. `UserInputRequest.id` 是否和 UI 提交一致；
2. `pending_permission_execution` 是否还存在；
3. 是否调用 `resume_with_user_input()`；
4. resume 是否复用了原始 tool call；
5. snapshot 是否已过期；
6. tool result 是否追加成功。

### 13. 模型改错文件

分开判断：

- 初始读取路径是否错；
- 上下文是否保留了过期源码；
- 权限是否允许了不该允许的路径；
- review 是否展示了正确 diff；
- 确认后文件是否被外部修改；
- executor 是否正确执行了原始 call。

不要一看到结果错就先改 prompt。

### 13.4 Resume 后上下文丢失

检查：

1. JSONL 事件是否真的追加；
2. store replay 是否成功；
3. `SessionView` 是否包含预期消息；
4. checkpoint 是否改变了 provider projection；
5. archive placeholder 是否需要 retrieve；
6. 是否误把进程内 `SessionRuntimeState` 当成持久状态。

---

## 14. 简历逐条对照：每句话如何准备

### 14.1 “设计 Agent Loop 串联用户输入、上下文构建、模型调用、工具执行与结果回传”

你要准备：

- 一张状态机图；
- `AgentLoop` 和 `ToolExecutor` 的职责边界；
- assistant tool call 和 tool result 的顺序；
- 一个 unknown tool 或坏参数的失败路径；
- `tests/test_agent_tool_flow.py` 的证据。

30 秒回答：

> 用户输入先持久化，再由 ContextBuilder 投影成 provider 请求。模型返回 tool call 后，AgentLoop 先保存 assistant call，再由 ToolExecutor 做权限和执行，结果以匹配 ID 的 tool result 回传，最后继续请求模型，直到没有 tool call 或触发轮次、调用、超时和取消边界。

### 14.2 “支持流式输出、任务取消、工具轮次限制、Provider 重试与失败恢复”

你要准备：

- `astream()` 和普通 `complete()` 的差别；
- 为什么 tool call 要等 `message_completed`；
- cancellation token 在哪里检查；
- retryable 和不可重试错误的区别；
- prompt-too-long 为什么触发 compact；
- stream 失败时为什么清理局部 delta。

### 14.3 “append-only JSONL 持久化消息、工具调用与运行事件”

你要准备：

- event、view、provider projection 三层关系；
- `JsonlSessionStore.append_event()` 和 `rebuild_session_view()`；
- 为什么不直接删 JSONL；
- resume 为什么依赖 replay；
- 非法事件链如何被发现。

### 14.4 “实现 L1–L4 上下文压缩、工具结果归档与 checkpoint handoff”

你要准备：

- L1、L2、L3、L4 的候选和保护边界；
- fresh source 为什么不能因为长就压缩；
- archive placeholder 如何恢复原文；
- checkpoint 的 tail boundary 和 source fingerprint；
- 为什么 L4 candidate 要先验证后 commit；
- 压缩如何保持 tool sequence 合法和幂等。

### 14.5 “实现 allow/ask/deny 权限策略与持久化授权规则”

你要准备：

- PermissionRequest 怎样从工具参数产生；
- grant 如何匹配；
- scope 为什么不能无限放大；
- ASK 如何暂停和 resume；
- deny/allow 的优先级；
- bypass 为什么不是彻底删除安全检查。

### 14.6 “文件修改前生成 unified diff，确认后重新校验快照”

你要准备：

- permission 和 prewrite review 的区别；
- diff 是可信预览，不是模型原文直接展示；
- 用户确认后为什么还要 snapshot recheck；
- stale preview 如何避免覆盖并发修改；
- 为什么 shell 没有同样的任意影响预览；
- 这个机制为什么不等于原子事务。

### 14.7 “统一适配 OpenAI-compatible、Anthropic Messages API 与 MCP 工具”

你要准备：

- 内部类型和 provider wire type 的关系；
- OpenAI 和 Anthropic 的 tools/system/tool result 差异；
- streaming chunk 如何归一化；
- MCP server 如何变成 TraceCoder Tool；
- MCP tool 为什么仍需权限控制；
- provider 专属字段为什么留在 adapter。

### 14.8 “基于 Harbor 评测 Aider Polyglot”

你要准备：

- Harbor 任务、环境、agent、verifier 的关系；
- Aider feedback repair 的协议限制；
- `225 / 221 / 213 / 96.38% / 94.67%` 的精确含义；
- 没有 reward 时如何分类；
- 为什么不能把基础设施问题都算成 agent 失败。

---

## 15. 面试表达：从“我写过”变成“我理解”

### 15.1 项目介绍五句话

```text
项目解决什么问题？
你的核心工作是什么？
最难的工程约束是什么？
如何验证？
当前限制是什么？
```

示例：

> 我做的是一个本地 Python Coding Agent，让模型能够在权限控制下读取和修改代码仓库。我的重点是 Agent Loop、工具调用、JSONL 会话、上下文压缩、Provider 适配和 Harbor 评测。比较难的是长任务中既要减少上下文 token，又不能丢掉 fresh source、错误证据和 tool-call transaction，所以我把持久事实、SessionView 和 provider projection 分开，并通过 replay、压缩、权限和 provider 测试验证。当前它能降低误执行和过期覆盖风险，但不是操作系统级沙箱，具体视觉、streaming 和 reasoning 能力也取决于 provider/model capability。

### 15.2 被追问时的回答格式

```text
问题是什么？
为什么简单方案不够？
你的设计是什么？
关键不变量是什么？
用什么测试或指标证明？
有什么限制？
```

### 15.3 不会时怎么回答

不要编。可以说：

> 这个概念我目前掌握到 X。结合项目里的 Y，我理解它解决的是 Z。若继续实现，我会先读 A 文件，补 B 测试，再验证 C 边界。

实习面试看重的是学习和定位能力，不是每个术语都必须背到专家程度。

### 15.4 高频追问

- 为什么 AgentLoop 不直接执行工具？
- 为什么权限不能只写在 system prompt？
- 为什么 tool result 不能单独存在？
- 为什么不直接截断历史？
- 为什么 L4 摘要不能替代原始事件？
- 为什么 streaming tool call 不能边收边执行？
- 为什么 provider 失败要分类？
- 为什么 UI 不能自己改 session 状态？
- 为什么 benchmark 要区分 reward-only 和端到端？
- 这个项目最大的当前限制是什么？

---

## 16. 30 天学习计划

### 第 1–3 天：建立地图

- 读 README、pyproject、cli、factory；
- 画包结构；
- 找到 `AgentLoop`、`AgentSession`、`AgentChatRunner`；
- 运行最小 loop limits 测试；
- 写一段自己的项目介绍。

### 第 4–7 天：吃透 Agent Loop

- 读 loop 的公开入口和核心循环；
- 读 tool flow 测试；
- 画 tool call/result 事务；
- 理解轮次、调用次数、timeout、取消；
- 写 fake provider 的执行草图。

### 第 8–11 天：工具和权限

- 读 tools/types、registry、builtin；
- 读 permission policy、manager、grants；
- 读 permission registry 和 tool execution；
- 完成 allow/ask/deny 预测练习；
- 讲清 prewrite review。

### 第 12–16 天：JSONL 和恢复

- 读 events、writer、store、models；
- 跟踪 event → view → message；
- 理解 pending permission resume；
- 读 context runtime replay 测试；
- 画 resume 流程。

### 第 17–21 天：上下文压缩

- 读 token budget、ContextBuilder、manager；
- 读 L1–L4 pipeline；
- 理解 fresh/stale/derived；
- 读 archive 和 checkpoint；
- 逐个解释压缩测试保护什么。

### 第 22–24 天：Provider、TUI、AnyIO、MCP

- 对比 OpenAI-compatible 和 Anthropic adapter；
- 读 streaming tool call 测试；
- 读 Textual App 和 AgentChatRunner；
- 理解两条事件线；
- 读 MCP adapter。

### 第 25–27 天：评测和排障

- 读 Harbor README 和 adapter；
- 背清楚 225/221/213/96.38%/94.67%；
- 练习分类一个失败 trial；
- 用排障章节处理几个假设问题。

### 第 28–30 天：修改和模拟面试

- 完成一个小工具或测试改动；
- 跑相关聚焦测试；
- 准备 30 秒、2 分钟、5 分钟项目介绍；
- 模拟 10 个追问；
- 写出项目当前限制和下一步改进。

---

## 17. 每章学习卡片

每学完一个主题，填写一次：

```text
主题：
它解决的问题：
简单方案为什么不够：
核心入口：
主要源码：
输入对象：
输出对象：
持久化事实：
关键不变量：
一个失败场景：
对应测试：
我做过的实验：
当前限制：
我能否用 2 分钟讲清：能 / 不能
```

示例：

```text
主题：权限暂停与恢复
它解决的问题：阻止模型直接执行敏感副作用
简单方案为什么不够：只靠 prompt 不可靠
核心入口：PermissionAwareToolRegistry / ToolExecutor
输入对象：ToolCall
输出对象：PermissionDecision / UserInputRequest / ToolResult
持久化事实：assistant tool call、pending 元数据、tool result
关键不变量：ASK 时不执行；恢复使用原始 call；快照过期不写入
一个失败场景：用户确认后文件被外部修改
对应测试：permissioned write + prewrite review tests
当前限制：不是操作系统级原子事务
```

---

## 18. 最终通关标准

你不需要把仓库每一行都背下来，但应该做到：

1. 能从 `app.factory` 追到 `AgentLoop`；
2. 能从 `ToolCall` 追到权限和 executor；
3. 能解释 `tool_call_id` 为什么必须保持配对；
4. 能解释 JSONL、SessionView、provider projection 的差别；
5. 能解释 L1–L4 每层的取舍；
6. 能解释 checkpoint handoff 和 archive recovery；
7. 能解释 Provider、Textual、AnyIO、MCP 的边界；
8. 能说清 Harbor 指标口径；
9. 能读一个测试并说出它保护的不变量；
10. 能做一个小修改并补测试；
11. 能诚实说出项目当前限制。

如果你能做到这些，你就不是只在简历上写“AI Agent”，而是真的具备了阅读、修改、验证和讲解一个 Agent runtime 的能力。

## 官方资料索引

- [代码阅读指南](CODEBASE_READING_GUIDE.zh-CN.md)
- [架构说明](ARCHITECTURE.zh-CN.md)
- [Agent 主循环护栏](AGENT_LOOP_GUARDRAILS.zh-CN.md)
- [CLI / TUI 设计](CLI_TUI_DESIGN.zh-CN.md)
- [工具设计](TOOLS_DESIGN.zh-CN.md)
- [权限设计](PERMISSIONS_DESIGN.zh-CN.md)
- [上下文管理设计](CONTEXT_MANAGEMENT_DESIGN.zh-CN.md)
- [Provider 设计](PROVIDERS_DESIGN.zh-CN.md)
- [MCP 客户端](MCP.zh-CN.md)
- [Harbor 评测指南](../benchmark/harbor/README.md)
- [项目面试笔记](interview/TRACECODER_INTERVIEW_NOTES.zh-CN.md)
