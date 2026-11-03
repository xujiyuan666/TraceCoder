import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from step02_agent_loop import run_agent
from step03_errors_and_limits import run_scenario
from step04_async_io import main as async_main
from step05_backend import app
from step06_session import main as session_main
import step07_real_llm
from step08_validation import schema_and_execute
from step09_retry import FlakyCall, complete_with_retry
from step10_streaming import consume
from tracecoder.agent.provider_retry import ProviderRetryPolicy
from tracecoder.providers.errors import ProviderError, ProviderErrorKind
from support import FakeProvider, Trace, answer, calculate_provider, call, create_registry


def test_observation_drives_next_action_and_history_pairs():
    provider = calculate_provider()
    result = run_agent("计算", provider, create_registry(), trace=Trace(False))
    assert result.status == "completed"
    assert result.answer == "(2 + 3) × 4 = 20"
    assert (result.provider_calls, result.tool_rounds) == (3, 2)
    assert [m["role"] for m in result.messages] == ["system", "user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert result.messages[2]["tool_calls"][0]["id"] == result.messages[3]["tool_call_id"]
    assert provider.requests[1].messages[-1].role == "tool"
    assert provider.requests[0].messages[-1].role == "user"  # 历史快照没被后来追加改变


@pytest.mark.parametrize("scenario", ["unknown", "bad_json", "missing_arg", "wrong_type", "exception"])
def test_errors_reach_next_request_then_repair(scenario):
    result = run_scenario(scenario, verbose=False)
    assert result.status == "completed"
    assert "修正调用" in result.answer
    assert any(e["state"] == "TOOL_RESULT" and not e["result"]["ok"] for e in result.events)


def test_limit_and_provider_failure_preserve_observation():
    limited = run_scenario("limit", verbose=False)
    assert (limited.status, limited.provider_calls, limited.tool_rounds) == ("tool_round_limit", 1, 1)
    failed = run_scenario("provider_error", verbose=False)
    assert failed.status == "provider_error"
    assert any(m["role"] == "tool" for m in failed.messages)


def test_async_success_and_timeout():
    results = asyncio.run(async_main(verbose=False))
    assert [r["ok"] for r in results] == [True, False]


def test_zero_limit_never_calls_provider():
    result = run_agent("计算", FakeProvider([]), create_registry(), max_tool_rounds=0, trace=Trace(False))
    assert result.status == "tool_round_limit"
    assert (result.provider_calls, result.tool_rounds) == (0, 0)


def test_tool_batch_is_one_round_and_all_results_reach_provider():
    response = call("add", {"a": 2, "b": 3})
    response.tool_calls.extend(call("multiply", {"value": 4, "factor": 6}, "call_2").tool_calls)
    provider = FakeProvider([response, answer("done")])
    result = run_agent("计算两个独立结果", provider, create_registry(), trace=Trace(False))
    assert (result.provider_calls, result.tool_rounds) == (2, 1)
    tool_messages = [m for m in provider.requests[1].messages if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["call_1", "call_2"]


def test_http_validation_and_request_isolation():
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/chat", json={"query": "计算"})
            assert response.status_code == 200
            assert response.json()["provider_calls"] == 3
            assert len(response.json()["messages"]) == 7
        assert client.post("/chat", json={"query": ""}).status_code == 422
        assert client.post("/chat", json={"query": "计算", "extra": True}).status_code == 422
        assert client.post("/chat", json={"query": "计算", "scenario": "limit"}).json()["status"] == "tool_round_limit"


def test_real_session_replay_and_continue():
    first, restored, continued = session_main(verbose=False)
    assert first.status == "completed"
    assert [m.role for m in restored] == ["user", "assistant", "tool", "assistant"]
    assert continued.provider_calls == 1
    assert continued.tool_rounds == 0
    assert len(continued.messages) == 6


def test_validation_rejects_extra_field_and_accepts_json():
    assert schema_and_execute('{"a": 2, "b": 3}', Trace(False))["content"] == "5"
    assert schema_and_execute({"a": 2, "b": 3, "c": 4}, Trace(False))["ok"] is False


def test_retry_uses_project_policy_and_stops_after_limit():
    trace = Trace(False)
    response = complete_with_retry(
        FlakyCall(2),
        ProviderRetryPolicy(max_retries=2, initial_delay_seconds=0.1, multiplier=2, max_delay_seconds=1),
        trace,
    )
    assert response.content == "重试后模型响应成功"
    assert [e["state"] for e in trace.events] == [
        "PROVIDER_ATTEMPT", "PROVIDER_ERROR", "BACKOFF",
        "PROVIDER_ATTEMPT", "PROVIDER_ERROR", "BACKOFF",
        "PROVIDER_ATTEMPT", "PROVIDER_SUCCESS",
    ]
    with pytest.raises(ProviderError) as error:
        complete_with_retry(FlakyCall(3), ProviderRetryPolicy(max_retries=2), Trace(False))
    assert error.value.kind == ProviderErrorKind.TIMEOUT


def test_streaming_commits_only_completed_response():
    import asyncio
    response = asyncio.run(consume(Trace(False)))
    assert response.content == "Agent 已经完成。"


def test_real_provider_defaults_to_qwen_flash_with_windows_user_key(monkeypatch):
    for name in ("DASHSCOPE_API_KEY", "TRACE_LEARNING_MODEL", "QWEN_MODEL", "DASHSCOPE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(step07_real_llm, "_windows_user_variable", lambda name: "test-key" if name == "DASHSCOPE_API_KEY" else None)
    provider = step07_real_llm.make_provider("qwen")
    assert provider.name == "qwen"
    assert provider.model == "qwen-flash"
    assert step07_real_llm.make_provider("qwen", "qwen-plus").model == "qwen-plus"
