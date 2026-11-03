"""第 7 步：把 FakeProvider 换成真实模型。

直接运行默认使用 DashScope 的 qwen-flash。也可以通过 --provider/--model 指定其他模型。
若当前终端没有继承 DASHSCOPE_API_KEY，会尝试读取 Windows 用户变量。

脚本不会打印 key，只打印 provider/model 名称和 Agent 的公开运行状态。
"""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "code" / ".env")

from tracecoder.providers.anthropic_provider import AnthropicProvider
from tracecoder.providers.openai_compatible import OpenAICompatibleProvider

from step02_agent_loop import run_agent
from support import Trace, create_registry


PRESETS = {
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", None),
    "qwen": ("DASHSCOPE_API_KEY", "QWEN_MODEL", "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    "moonshot": ("MOONSHOT_API_KEY", "MOONSHOT_MODEL", "MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"),
    "zhipu": ("ZHIPUAI_API_KEY", "ZHIPU_MODEL", "ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    "ollama": ("OLLAMA_API_KEY", "OLLAMA_MODEL", "OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL", None),
}


def _windows_user_variable(name: str) -> str | None:
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if isinstance(value, str) and value.strip() else None


def make_provider(provider_name: str, model_override: str | None = None):
    if provider_name not in PRESETS:
        raise ValueError(f"不支持的 provider：{provider_name}；可选：{', '.join(PRESETS)}")
    key_env, model_env, base_env, default_base = PRESETS[provider_name]
    api_key = os.getenv(key_env) or _windows_user_variable(key_env)
    if provider_name == "ollama" and not api_key:
        api_key = "ollama"
    if not api_key:
        raise RuntimeError(f"缺少 {key_env}；请先设置 API key 环境变量")
    # 只读取脚本明确约定的变量；运行平台注入的同名自定义变量不自动采用。
    configured_model = os.getenv(model_env)
    # agent学习/code 的旧示例使用 DASHSCOPE_MODEL，这里兼容同一份配置。
    if provider_name == "qwen":
        configured_model = configured_model or os.getenv("DASHSCOPE_MODEL")
    model = model_override or os.getenv("TRACE_LEARNING_MODEL") or configured_model
    if provider_name == "qwen":
        model = model or "qwen-flash"
    if not model:
        raise RuntimeError(f"缺少模型名；请设置 TRACE_LEARNING_MODEL 或 {model_env}")
    base_url = os.getenv(base_env) or default_base
    if provider_name == "anthropic":
        return AnthropicProvider(name=provider_name, model=model, api_key=api_key, base_url=base_url)
    return OpenAICompatibleProvider(name=provider_name, model=model, api_key=api_key, base_url=base_url)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default=os.getenv("TRACE_LEARNING_PROVIDER", "qwen"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--query", default="请计算 (2 + 3) × 4。必须使用工具完成计算，然后说明工具结果。")
    parser.add_argument("--max-tool-rounds", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="只检查配置，不调用模型")
    args = parser.parse_args()

    provider = make_provider(args.provider, args.model)
    print(f"provider={provider.name} model={provider.model} base_url={provider.base_url or '<default>'}")
    if args.dry_run:
        print("配置检查通过；未调用模型。")
        return
    result = run_agent(
        args.query,
        provider,
        create_provider_tools(provider),
        max_tool_rounds=args.max_tool_rounds,
        trace=Trace(True),
    )
    print(f"最终状态：{result.status}，模型调用={result.provider_calls}，工具轮次={result.tool_rounds}")
    print(f"最终回答：{result.answer}")


def create_provider_tools(provider):
    # 真实 provider 只负责模型协议；Agent 仍使用相同的工具 registry。
    return create_registry()


if __name__ == "__main__":
    main()
