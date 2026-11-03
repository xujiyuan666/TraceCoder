# 从 TraceCoder 提取的 Agent 代码学习单元

先运行、观察状态，再看代码和动手修改。本单元覆盖学习路线的 Day 1，以及 Day 2 的工具调用基础。先掌握闭环，再进入 MCP、记忆、RAG 和多 Agent。

FakeProvider 模拟模型的请求/响应接口；动作由教学脚本预设，不能理解任意自然语言，也不展示模型内部思维。工具计算、消息追加、HTTP 校验和会话落盘实际执行。默认不用 API Key。

## 环境与入口

在项目根目录运行所有命令。当前根目录 .venv 已配置好；不要使用 agent学习/code 中指向旧机器的虚拟环境。

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step00_chatbot.py
~~~

需要重建时：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" fastapi uvicorn httpx
~~~

## 每一课怎么学

一次只完成一课：先预测输出 -> 运行 -> 对照状态和消息 -> 单步调试 -> 完成一个修改练习。首课只读脚本 main；support.py 是公共教具，按课逐步打开相应部分。

先打开 [第 0 课代码](step00_chatbot.py)，完成 0–2 课建议安排 60–90 分钟。错误处理、异步和后端各自留出一节练习时间，不需要一次学完。输出包含完整 JSON 快照，第一次只跟踪 state 和调用次数，再展开 request.messages 与工具结果。

| 课 | 入口与新增概念 | 预期结果 | 修改练习 |
| --- | --- | --- | --- |
| 0 | step00_chatbot.py：请求、响应、dataclass、消息列表 | 1 次模型调用，0 次工具执行 | 修改问题和预设回答，观察两者分别存在哪里 |
| 1 | step01_one_tool.py：schema、名称分发、关键字参数、工具结果 | add 返回 5，状态为 OBSERVATION_ONLY | 把参数改成 a=10、b=7；结果是 17，还没有模型最终回答 |
| 2 | step02_agent_loop.py：循环、历史反馈、终止条件 | 2 轮工具、3 次模型调用，结果 20 | 在 support.calculate_provider 中改第一轮为 10+7；后续根据观察算出 68 |
| 3 | step03_errors_and_limits.py：结构化错误、恢复、上限 | 错误被回传，修正后得到 5 | 删除修正响应，观察 provider_error 和保留的工具历史 |
| 4 | step04_async_io.py：async/await、gather、超时 | 快查询成功，慢查询超时 | 改慢查询 timeout 到 0.5；两项都成功 |
| 5 | step05_backend.py：HTTP、Pydantic、请求隔离、线程 | 合法请求 200，非法请求 422 | 增加未知字段或负数 max_tool_rounds，查看校验详情 |
| 6 | step06_session.py：真实 JSONL、重放、继续会话 | 恢复 4 条消息，继续后 6 条 | 在磁盘日志中找 call_1 和匹配的 tool_call_id |
| 7 | step07_real_llm.py：真实 provider、API key、模型配置 | 同一个 run_agent 接入真实模型 | 先 dry-run，再让模型完成一次计算 |
| 8 | step08_validation.py：Pydantic 参数校验和 schema 边界 | 合法参数执行，坏参数拒绝 | 分别运行 json、missing、extra、type |
| 9 | step09_retry.py：错误分类、指数退避、重试上限 | 两次超时后第三次成功 | failures 改成 3，观察停止 |
| 10 | step10_streaming.py：流式事件、增量显示、最终提交 | 只在完成事件后提交回答 | 删除完成事件，观察不能提交 |
| 11 | step11_permission.py：ASK、用户确认、受控副作用 | 确认后才写入临时文件 | 把答案改成 deny，确认文件不生成 |
| 12 | step12_context_projection.py：事实、视图、provider 投影 | JSONL -> SessionView -> ChatMessage | 找出 tool_call/result 的配对 ID |

### 第 0–2 课：抓住循环

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step00_chatbot.py
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step01_one_tool.py
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step02_agent_loop.py
~~~

每条输出是一个独立 JSON 快照：

~~~text
USER_INPUT
CALL_MODEL          request.messages = 当前历史，request.tools = 工具说明
MODEL_RESPONSE      content 或 tool_calls
APPEND_TOOL_CALLS   历史加入 assistant 的工具请求
EXECUTE_TOOL        工具名称、参数、调用 ID
TOOL_RESULT         ok、content、error
ROUND_FINISHED      完成的工具轮数
CALL_MODEL          这一次请求已包含上一次工具结果
...
COMPLETED           最终回答、调用次数、完整历史
~~~

seq 表示记录顺序；state 表示程序执行阶段。Trace 保存深拷贝，后续追加消息不会改变此前快照。只有工具反馈进入下一次模型请求后，才形成这里学习的 Agent 闭环。普通聊天应用也可以多轮对话；这里的区别是是否根据环境反馈继续选择动作。

第二个工具依赖第一个结果，所以不能并发。两个独立查询则可以并发，第 4 课演示这种区别。

### 第 3 课：不要让一个工具错误毁掉整轮任务

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step03_errors_and_limits.py unknown
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step03_errors_and_limits.py bad_json
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step03_errors_and_limits.py missing_arg
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step03_errors_and_limits.py wrong_type
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step03_errors_and_limits.py exception
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step03_errors_and_limits.py limit
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step03_errors_and_limits.py provider_error
~~~

工具异常由真实 ToolRegistry 转成 ToolResult，模型故障则结束本实验。没有“保证自我纠正”：修正是模拟器预设的，用于验证程序确实回传错误。

bad_json 演示 provider 解析失败后保留的原始字符串，registry 会拒绝非 dict 参数。它没有在这里进行 JSON 解码。schema 也不是参数验证器；add/multiply 在执行侧检查整数类型。

轮数与模型调用次数不同：本例 2 轮工具需要 3 次模型调用。沿用项目的上限语义，max_tool_rounds=1 执行一次工具后立即停止，连最终总结请求也不再发出。0 则不请求模型。

### 第 4–5 课：把 Agent 放到后端里

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step04_async_io.py
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step05_backend.py --selftest
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step05_backend.py
~~~

最后一条启动服务，打开 http://127.0.0.1:8000/docs，用 POST /chat 的 Try it out 提交：

~~~json
{"query":"计算 (2 + 3) × 4","scenario":"calculate","max_tool_rounds":8}
~~~

另开 PowerShell 可以使用 ASCII 请求，避免终端中文编码干扰：

~~~powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/chat -Method Post -ContentType 'application/json' -Body '{"query":"calculate","scenario":"calculate"}' | ConvertTo-Json -Depth 30
~~~

端口占用时，在脚本末尾把 8000 改成 8001；地址也随之修改。Ctrl+C 停止。

数据流：HTTP JSON -> ChatInput 校验 -> execute_request -> run_agent -> ChatOutput -> HTTP JSON。response_model 定义返回合同，非法输入返回 422。结果 status=tool_round_limit 表示 Agent 业务停止，不等于 HTTP 传输错误。

query 会写入历史，scenario 决定固定演示任务；本接口还没有真实 LLM。limit 场景固定使用 1 轮上限，其他场景使用传入的 max_tool_rounds。

每个请求独立创建 provider、session、trace，避免不同用户串历史。响应包含 messages/events，属于学习调试数据；目前一次返回完整结果，不是实时流式状态接口。线程包装让同步调用不阻塞事件循环，但线程中任务仍需专门的取消机制。第 4 课是独立异步 I/O 实验，还没有加入同步 Agent 循环。

### 第 6 课：真实会话恢复

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step06_session.py
~~~

这次直接用项目的 AgentSession、JsonlSessionStore 和 ContextBuilder：

~~~text
append_user/assistant/tool
  -> SessionEventWriter
  -> JSONL 事件日志
  -> rebuild_session_view
  -> ContextBuilder 校验配对并投影
  -> 下一次模型请求的 ChatMessage
~~~

JSONL_SAVED 会打印磁盘路径和完整日志。为了不在仓库堆数据，演示结束自动清理临时目录；想检查实物，可在 SESSION_RESTORED 行设置断点。恢复后新建一轮请求，provider_calls=1、tool_rounds=0；历史工具不会重跑。模拟最终回答预设为 5，恢复是否正确由打印的历史和测试验证。

## 对照项目：哪些复用，哪些裁剪

| 学习代码 | 来源与对应实现 |
| --- | --- |
| support 中的消息、请求、响应、ToolCall、ToolDefinition | 直接 import tracecoder/providers/types.py |
| 工具注册与执行 | 直接复用 tracecoder/tools/registry.py 和 tools/types.py |
| FakeProvider | 参照 tests/test_app_runtime.py；增加请求快照和读观察的模拟响应 |
| run_agent | 裁剪 agent/loop.py 的模型 -> 保存调用 -> 执行 -> 保存结果 -> 模型顺序 |
| MemorySession | agent/session.py 的三个 append 操作的简化内存版 |
| StoredSession | 适配真实 AgentSession 和 ContextBuilder |
| HTTP 与状态输出 | 新增教学外壳；TraceCoder 本身主要使用 CLI/TUI |

循环约 70 行，没有复制生产版的权限、重试、压缩、后台任务和完成门槛。真实 ContextBuilder 会拒绝孤立 tool result 和未闭合调用；内存版只演示正确顺序，第 6 课才启用真实校验。

## 第 7 课：接入真实大模型

FakeProvider 是前 0–6 课的可重复实验装置。第 7 课只替换 provider，run_agent、工具注册、消息历史和 Trace 都不换：

~~~text
FakeProvider.complete()
        ↓ 替换为
AnthropicProvider.complete()
或 OpenAICompatibleProvider.complete()
        ↓
同一套 run_agent()
~~~

默认使用 DashScope 的 qwen-flash，并读取 DASHSCOPE_API_KEY。Windows 当前终端未继承用户变量时，脚本会直接读取 Windows 用户变量。先检查配置，不发送请求：

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step07_real_llm.py --dry-run
~~~

再实际调用：

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step07_real_llm.py
~~~

支持的 provider 和环境变量：

| provider | API key | 模型 | 可选 base URL |
| --- | --- | --- | --- |
| anthropic | ANTHROPIC_API_KEY | ANTHROPIC_MODEL 或 TRACE_LEARNING_MODEL | ANTHROPIC_BASE_URL |
| qwen（默认） | DASHSCOPE_API_KEY | 默认 qwen-flash；可用 --model、TRACE_LEARNING_MODEL、QWEN_MODEL 或 DASHSCOPE_MODEL 覆盖 | DASHSCOPE_BASE_URL |
| openai | OPENAI_API_KEY | OPENAI_MODEL 或 TRACE_LEARNING_MODEL | OPENAI_BASE_URL |
| deepseek | DEEPSEEK_API_KEY | DEEPSEEK_MODEL 或 TRACE_LEARNING_MODEL | DEEPSEEK_BASE_URL |
| moonshot / zhipu / openrouter / ollama | 对应 provider 的 key | 对应的模型环境变量 | 对应的 base URL 环境变量 |

当前 shell 中如果已经有 ANTHROPIC_API_KEY，只需确认模型名：

~~~powershell
$env:TRACE_LEARNING_PROVIDER = "anthropic"
$env:TRACE_LEARNING_MODEL = "claude-sonnet-4-5"
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step07_real_llm.py --dry-run
~~~

不要把 key 写进代码或提交到 Git。真实调用可能产生费用；第一次建议把 query 限定为一次简单计算，并观察 CALL_MODEL -> MODEL_RESPONSE -> EXECUTE_TOOL -> TOOL_RESULT -> ...。如果模型不调用工具，说明模型没有遵守工具提示或当前模型工具能力不兼容，先查看 provider/model 配置，不要修改 Agent 循环。

## 第二层：从能跑到可靠

第 8–12 课开始加入生产系统必须有的边界：

### 第 8 课：参数验证不是 schema

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step08_validation.py ok
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step08_validation.py missing
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step08_validation.py extra
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step08_validation.py type
~~~

ToolDefinition 让模型知道参数形状；Pydantic 在执行前拒绝缺字段、额外字段和错误类型。TraceCoder 的 ToolRegistry 还要处理 JSON 字符串、未知工具和 executor 异常。

### 第 9 课：provider 失败要分类

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step09_retry.py
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step09_retry.py --failures 3
~~~

只重试 timeout、rate limit、network 和 server error；认证失败、配置错误和不支持能力不能盲目重试。对照 tracecoder/providers/errors.py 和 tracecoder/agent/provider_retry.py。

### 第 10 课：streaming 只改变输出方式

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step10_streaming.py
~~~

增量文本可以展示给用户，但只有 message_completed 才能作为最终回答写入会话。真实 provider 的原始 chunk 由 provider 转成 ChatStreamEvent；AgentLoop 不应该解析厂商 chunk。

### 第 11 课：权限是程序侧硬边界

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step11_permission.py
~~~

执行顺序是 TOOL_CALL -> preflight -> ASK -> 用户回答 -> execute。模型可以提出写文件，但不能绕过 PermissionManager。把 answer 改成 deny，工具 executor 不会运行。

这对应 tracecoder/tools/permission_registry.py、tracecoder/permissions/manager.py 和现有 learn_chain1_demo.py。

### 第 12 课：同一份历史有三种形态

~~~powershell
.\.venv\Scripts\python.exe -X utf8 agent学习\最小实现\step12_context_projection.py
~~~

学习这条后端数据流：

~~~text
JSONL 事件事实
  -> SessionView 重放视图
  -> ContextBuilder 校验并投影
  -> ChatMessage provider 请求
~~~

不要把 SessionView 直接当成 provider 消息。ContextBuilder 会校验 assistant tool call 和 tool result 是否成对，这也是长会话压缩仍能工作的基础。

## 断点与测试

编辑器选择项目根目录 .venv/Scripts/python.exe。第一次给 step02_agent_loop.py 的 provider.complete、registry.execute、session.append_tool_result 设置断点，检查 request.messages、tool_call.arguments 和 result。

~~~powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest agent学习\最小实现\test_learning.py -q
~~~

最后用自己的话回答四个问题：闭环比普通问答多了什么？工具结果为何需要再交给模型？call ID 为什么必须配对？什么决定循环停止？回答时指向刚跑过的变量和状态。
