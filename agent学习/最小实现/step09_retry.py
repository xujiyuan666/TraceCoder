"""第 9 步：只对可重试的 provider 瞬时错误做有限重试。"""
from dataclasses import dataclass

from tracecoder.agent.provider_retry import ProviderRetryPolicy
from tracecoder.providers.errors import ProviderError, ProviderErrorKind
from support import Trace, answer


@dataclass
class FlakyCall:
    failures_left: int

    def complete(self):
        if self.failures_left:
            self.failures_left -= 1
            raise ProviderError(ProviderErrorKind.TIMEOUT, "模拟网络超时")
        return answer("重试后模型响应成功")


def complete_with_retry(call: FlakyCall, policy: ProviderRetryPolicy, trace: Trace):
    attempt = 0
    while True:
        try:
            attempt += 1
            trace.emit("PROVIDER_ATTEMPT", attempt=attempt)
            response = call.complete()
            trace.emit("PROVIDER_SUCCESS", attempt=attempt, content=response.content)
            return response
        except ProviderError as exc:
            retry_number = attempt
            can_retry = exc.retryable and retry_number <= policy.max_retries
            trace.emit("PROVIDER_ERROR", attempt=attempt, kind=exc.kind.value, retryable=exc.retryable,
                       will_retry=can_retry)
            if not can_retry:
                raise
            delay = policy.delay_for_retry(retry_number)
            # 教学版不真的 sleep；真实 AgentLoop 会调用 sleeper。
            trace.emit("BACKOFF", retry_number=retry_number, delay_seconds=delay)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--failures", type=int, default=2)
    args = parser.parse_args()
    complete_with_retry(
        FlakyCall(args.failures),
        ProviderRetryPolicy(max_retries=2, initial_delay_seconds=0.1, multiplier=2, max_delay_seconds=1),
        Trace(True),
    )


if __name__ == "__main__":
    main()
