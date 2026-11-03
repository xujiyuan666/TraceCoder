# 三个月上岸 Agent 开发 · 7 天冲刺版

> 来源：12 周学习对话整理，压缩为 7 天高强度冲刺。
> **学习内容与 12 周版完全一致，知识点、项目产出、面试考点一项不减。**
> 强度提示：原计划 100-120 小时压进 7 天，每天约 14-17 小时，属于脱产冲刺节奏；若做不到可把某天拆到两天。

---

## Day 1 · Python 地基：手写 Agent Loop（原第 1 周）

- 学：Python 异步、Pydantic v2、FastAPI
- 做：手写一个最小 Agent Loop，**50-150 行，不许用 LangChain**
- 理解：把"思考 → 行动 → 观察 → 再思考"的循环亲手写一遍
- 一句话辨析：**Agent vs Chatbot** —— Chatbot 单次问答，Agent 自主闭环

## Day 2 · Function Calling + MCP（原第 2、3 周）

- Function Calling：定义工具 schema，让模型自己选工具、传参数
- 写 5 个工具：**天气、汇率、待办、日历、搜索**
- 异常全处理一遍：**超时、参数错、模型瞎编工具名**
- MCP：用 **FastMCP** 写一个文件系统 MCP Server（能读、写、列目录、搜内容），**部署到云函数**——这项目直接写简历
- 一句话辨析：**Function Calling 是模型能力（管"能调"），MCP 是工具的标准封装协议（管"怎么统一接"）**

## Day 3 · LangGraph + Memory（原第 4、5 周）

- LangGraph：StateGraph、Node、Edge、条件分支、checkpoint
- 做一个任务规划 Agent：比如"规划周末旅行"——它自己搜目的地、查天气、比价、生成行程，每一步根据结果调下一步
- Memory 三层：**工作记忆、会话记忆、长期记忆**；长期记忆用向量库（ChromaDB/Milvus）+ 结构化存储，记住用户偏好和历史决策
- 面试题必答：**"会话很长 Prompt 爆了怎么办？"**

## Day 4 · Agentic RAG + 多 Agent（原第 6、7 周）

- Agentic RAG：别搞固定管道，把 RAG 封成工具，让 Agent 自己决定啥时候检索、检索几次、结果不够咋换查询
- 面试题必答：**"Agentic RAG 和 Naive RAG 的区别"**（不是问"RAG 怎么搭"）
- 多 Agent：**Supervisor 模式**，搭个写作团队——Researcher 搜资料、Writer 写初稿、Reviewer 挑毛病、Supervisor 协调
- 面试题必答：**"啥时候不该用多 Agent？"**——任务可单线完成、通信开销大于收益、错误定位困难时不用（通信成本高，别滥用）

## Day 5 · 评估可观测 + 部署安全（原第 8、9 周）

- 评估：**Langfuse** 追调用链，**RAGAS** 评检索和生成
- 写一份可靠性报告：**正常指标、对抗测试、失败模式**（没 eval 的系统就是 demo，面试官一听就知道）
- 部署：**Docker** 打包，**FastAPI** 暴露接口，流式输出
- 安全：**Guardrails** 防 Prompt 注入，看 **OWASP LLM Top 10**
- 成本控制：简单任务用便宜模型，复杂任务再上强模型

## Day 6 · 项目打磨（原第 10 周）

- 不堆数量，**精选 2-3 个**：一个 Agentic RAG、一个多 Agent 协作、一个 MCP Server
- 每个项目标准：**README 带架构图；代码有类型注解、错误处理、测试**

## Day 7 · 简历 + 面试冲刺（原第 11、12 周）

- 简历写法：**STAR，量化**。别写"使用了 LangGraph 和 MCP"（太虚），要写"**把工具调用准确率从 70% 提到 95%，通过优化工具描述和失败重试**"
- 高频题清单：Agent 定义；Agent 和 Chatbot/Prompt Chain 区别；Memory 设计；多 Agent 拓扑；LangGraph；MCP 和 Function Calling 关系；Agentic RAG；评估；Prompt 注入
- 项目话术三层：**What、How、Why**——为什么选 A 不选 B、遇到啥坑、重做咋改
- **模拟面试至少两次**

---

## 附：工具栈与预期

- 工具栈一以贯之：**Python + LangGraph + FastMCP + ChromaDB/Milvus + FastAPI + Docker**（别今天 CrewAI 明天 AutoGen，分散精力）
- 预期岗位：初级/中级，中小厂、AI 创业、传统企业 AI 部门；大厂高 P 别想
- 面试官筛的不是知道最多名词的人，是能把 Agent 做成可靠系统的人
