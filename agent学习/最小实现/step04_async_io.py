"""第 4 步：模拟外部 I/O，观察 await、并发和超时；没有网络请求。"""
import asyncio
from support import Trace


async def lookup(label: str, delay: float) -> str:
    # sleep 只模拟等待网络/数据库，await 期间允许其他协程运行。
    await asyncio.sleep(delay)
    return f"{label} 查询完成"


async def safe_lookup(label: str, delay: float, timeout: float, trace: Trace) -> dict:
    trace.emit("IO_STARTED", label=label)
    try:
        result = await asyncio.wait_for(lookup(label, delay), timeout=timeout)
        trace.emit("IO_COMPLETED", label=label, result=result)
        return {"ok": True, "content": result}
    except TimeoutError:
        trace.emit("IO_TIMEOUT", label=label, timeout=timeout)
        return {"ok": False, "content": f"{label} 超时"}


async def main(verbose=True):
    trace = Trace(verbose)
    # 独立 I/O 可并发；step02 的第二个计算依赖第一个结果，不能这样并发。
    results = await asyncio.gather(
        safe_lookup("快速查询", 0.02, 0.2, trace),
        safe_lookup("慢速查询", 0.2, 0.04, trace),
    )
    trace.emit("BATCH_COMPLETED", results=results)
    return results


if __name__ == "__main__":
    asyncio.run(main())
