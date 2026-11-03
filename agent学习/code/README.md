# 第 1-2 周代码：手写 Agent Loop + Function Calling（已接阿里云百炼，实测可跑）

## 环境准备

```powershell
cd C:\Users\EDY\Desktop\TraceCoder
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

代码会读取 `DASHSCOPE_API_KEY`（百炼）。你可以在当前终端设置它，或把 `.env.example` 复制为同目录的 `.env` 后填写自己的 key；不要把 key 提交到 Git。
接入点：`https://dashscope.aliyuncs.com/compatible-mode/v1`，默认模型 `qwen-turbo`（最便宜，实测支持工具调用且闭环正确）。
想升级：设环境变量 `DASHSCOPE_MODEL=qwen-plus`（平衡）或 `qwen-max`（最强）。

## 第 1 周：week1_agent_loop.py

```powershell
# 启动 FastAPI 服务
.\.venv\Scripts\python.exe week1_agent_loop.py
# 另开终端测试：
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d "{\"query\":\"现在几点？北京天气怎么样？\"}"
```

## 第 2 周：week2_function_calling.py

```powershell
# 自检：不调用大模型，验证 5 个工具 + 三类异常（已通过）
.\.venv\Scripts\python.exe week2_function_calling.py selftest

# 对话 demo：
.\.venv\Scripts\python.exe week2_function_calling.py
```

## 实测记录（2026-09-21）

- 第 1 周：问"现在几点？北京天气？" → 模型自主调用 `get_time` + `get_weather`，回答正确
- 第 2 周：问"加待办 + 查汇率" → 模型自主调用 `add_todo` + `exchange_rate`，回答正确

## 两个核心概念（面试必背）

1. **Agent Loop 闭环**：模型每轮要么直接回答（结束），要么输出工具调用；你执行工具后把结果塞回对话历史，让模型"再思考"。Chatbot 是单次问答，Agent 是自主闭环。
2. **异常即观察**：工具报错不要崩进程，而是把错误文本喂回模型——模型看到"没有 call_mom 这个工具"就会自己换一个，这就是它能自我纠正的原因。
