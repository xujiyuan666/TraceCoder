"""第 3 步：工具错误也是观察；循环边界由程序决定。"""
import argparse
import json
from support import FakeProvider, Trace, answer, call, create_registry
from step02_agent_loop import run_agent


SCENARIOS = ["unknown", "bad_json", "missing_arg", "wrong_type", "exception", "limit", "provider_error"]


def build_provider(scenario):
    if scenario == "limit":
        return FakeProvider([call("add", {"a": 2, "b": 3})])
    if scenario == "provider_error":
        return FakeProvider([call("add", {"a": 2, "b": 3})])  # 后续响应缺失触发故障
    first = {
        "unknown": call("no_such_tool", {}),
        "bad_json": call("add", "{坏 JSON"),
        "missing_arg": call("add", {"a": 2}),
        "wrong_type": call("add", {"a": "2", "b": 3}),
        "exception": call("broken", {}),
    }[scenario]

    def repair(request):
        observation = json.loads(request.messages[-1].content)
        if observation["ok"]:
            raise AssertionError("本实验第一轮应当失败")
        return call("add", {"a": 2, "b": 3}, "call_repaired")

    def finish(request):
        observation = json.loads(request.messages[-1].content)
        return answer(f"看到错误后修正调用，得到 {observation['content']}。")

    return FakeProvider([first, repair, finish])


def run_scenario(scenario, verbose=True):
    return run_agent("计算 2 + 3", build_provider(scenario), create_registry(),
                     max_tool_rounds=1 if scenario == "limit" else 8, trace=Trace(verbose))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=SCENARIOS, nargs="?", default="unknown")
    args = parser.parse_args()
    run_scenario(args.scenario)
