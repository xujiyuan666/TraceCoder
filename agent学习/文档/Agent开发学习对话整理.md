# 三个月上岸 Agent 开发：学习对话整理

> 来源：聊天截图 7 张（已去重合并，按时间顺序整理）
> 对话双方：学生（学习者）与老师（指导者）



***

## 一、开场与摸底

**学生**：老师，在吗？我想三个月上岸 Agent 开发，求个学习路径。

**老师**：先别 "求路径"。我问你，Python 异步会吗？Pydantic 会吗？FastAPI 写过吗？Function Calling 手写过吗？

**学生**：会点 Python，调过 API

**老师**：行，有救。按我这条线走，12 周。每天 3-4 小时，周末 6 小时。总 100-120 小时。做不到就别开始。

**学生**：收到



***

## 二、第 1-3 周：打地基（不碰框架）

**老师**：前 3 周别碰框架，打地基。第 1 周，Python 异步、Pydantic v2、FastAPI，手写一个最小 Agent Loop，50-150 行，不许用 LangChain。

**学生**：为啥不用框架？

**老师**：你连 "思考→行动→观察→再思考" 的循环都没手写过，面试一问就露馅。Agent 和 Chatbot 的区别就这一句：Chatbot 单次问答，Agent 自主闭环。

**老师**：第 2 周，Function Calling。定义工具 schema，让模型自己选工具、传参数。写 5 个工具：天气、汇率、待办、日历、搜索。把超时、参数错、模型瞎编工具名全处理一遍。

**老师**：第 3 周，MCP。2026 年 Agent 岗增速最快的技能，面试必问。用 FastMCP 写个文件系统 MCP Server，能读、写、列目录、搜内容。部署到云函数。这项目直接写简历。

**学生**：MCP 和 Function Calling 啥关系？

**老师**：Function Calling 是模型能力，MCP 是工具的标准封装协议。一个管 "能调"，一个管 "怎么统一接"。面试爱问这个。



***

## 三、第 4-7 周：Agent 核心技能

**老师**：第 4 周，LangGraph。StateGraph、Node、Edge、条件分支、checkpoint。做个任务规划 Agent：比如 "规划周末旅行"，它自己搜目的地、查天气、比价、生成行程，每一步根据结果调下一步。

**老师**：第 5 周，加记忆。三层：工作记忆、会话记忆、长期记忆。长期记忆用向量库 + 结构化存储，记住用户偏好和历史决策。面试常问 "会话很长 Prompt 爆了怎么办"，你得答得出来。

**老师**：第 6 周，Agentic RAG。别搞固定管道，把 RAG 封成工具，让 Agent 自己决定啥时候检索、检索几次、结果不够咋换查询。现在面试考的是 "Agentic RAG 和 Naive RAG 区别"，不是 "RAG 怎么搭"。

**老师**：第 7 周，多 Agent。Supervisor 模式，搭个写作团队：Researcher 搜资料，Writer 写初稿，Reviewer 挑毛病，Supervisor 协调。Anthropic 都发报告了，多 Agent 复杂任务更强。但别滥用，通信成本很高。

**学生**：多 Agent 是不是越复杂越牛逼？

**老师**：不是。简单任务用多 Agent 是找死。面试问 "啥时候不该用多 Agent"，你要能说：任务可单线完成、通信开销大于收益、错误定位困难时不用。



***

## 四、第 8-10 周：工程化与项目打磨

**老师**：第 8 周，评估和可观测性。Langfuse 追调用链，RAGAS 评检索和生成。写一份可靠性报告：正常指标、对抗测试、失败模式。没 eval 的系统就是 demo，面试官一听就知道。

**老师**：第 9 周，部署和安全。Docker 打包，FastAPI 暴露接口，流式输出。Guardrails 防 Prompt 注入，看 OWASP LLM Top 10。成本控制也要会：简单任务便宜模型，复杂任务再上强的。

**老师**：第 10 周，打磨项目。别堆数量，精选 2-3 个：一个 Agentic RAG，一个多 Agent 协作，一个 MCP Server。每个 README 带架构图，代码有类型注解、错误处理、测试。

**学生**：简历怎么写？

**老师**：STAR，量化。别写 "使用了 LangGraph 和 MCP"，太虚。写 "把工具调用准确率从 70% 提到 95%，通过优化工具描述和失败重试"。面试官只看技术决策和结果。



***

## 五、第 11-12 周：面试冲刺

**老师**：第 11-12 周，面试冲刺。高频题：Agent 定义、和 Chatbot/Prompt Chain 区别、Memory 设计、多 Agent 拓扑、LangGraph、MCP 和 Function Calling 关系、Agentic RAG、评估、Prompt 注入。

**老师**：项目话术准备三层：What、How、Why。为什么选 A 不选 B，遇到啥坑，重做咋改。模拟面试至少两次。



***

## 六、收尾问答

**学生**：大佬，工具栈用啥？

**老师**：一以贯之：Python + LangGraph + FastMCP + ChromaDB/Milvus + FastAPI + Docker。别今天 CrewAI 明天 AutoGen，分散精力。

**学生**：三个月真能上岸吗？

**老师**：能上初级 / 中级，中小厂、AI 创业、传统企业 AI 部门有机会。大厂高 P 别想。面试官筛的不是知道最多名词的人，是能把 Agent 做成可靠系统的人。

**老师**：最后一句，少废话，去写代码。有问题随时问，别问百度能搜到的 🤔

**学生**：谢谢老师 🤝



***

## 附：12 周学习路径一览



| 周次        | 主题               | 关键内容                               | 产出 / 考点                        |
| --------- | ---------------- | ---------------------------------- | ------------------------------ |
| 第 1 周     | Python 地基        | 异步、Pydantic v2、FastAPI             | 手写最小 Agent Loop（50-150 行，不用框架） |
| 第 2 周     | Function Calling | 工具 schema、模型选工具                    | 5 个工具 + 异常处理                   |
| 第 3 周     | MCP              | FastMCP、文件系统 Server                | 部署到云函数，写进简历                    |
| 第 4 周     | LangGraph        | StateGraph、Node/Edge、checkpoint    | 任务规划 Agent（周末旅行）               |
| 第 5 周     | Memory           | 工作 / 会话 / 长期记忆三层                   | 答出 "长会话 Prompt 爆了怎么办"          |
| 第 6 周     | Agentic RAG      | RAG 封成工具，自主检索                      | 说清与 Naive RAG 的区别              |
| 第 7 周     | 多 Agent          | Supervisor 模式                      | 知道何时不该用多 Agent                 |
| 第 8 周     | 评估与可观测性          | Langfuse、RAGAS                     | 可靠性报告（指标 / 对抗 / 失败模式）          |
| 第 9 周     | 部署与安全            | Docker、Guardrails、OWASP LLM Top 10 | 成本控制策略                         |
| 第 10 周    | 项目打磨             | 精选 2-3 个项目                         | README 架构图、类型注解、测试             |
| 第 11-12 周 | 面试冲刺             | 高频题 + 项目话术（What/How/Why）           | 模拟面试至少 2 次                     |