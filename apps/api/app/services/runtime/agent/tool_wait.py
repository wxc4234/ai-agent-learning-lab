"""按等待来源区分工具预算超时、执行器错误与外部取消。"""

import asyncio
from collections.abc import Awaitable


class ToolWaitTimeout(TimeoutError):
    """仅由本层等待预算耗尽产生，不依赖执行器抛出的异常类型。"""


async def _join(task: asyncio.Future[str]) -> asyncio.CancelledError | None:
    # gather将子任务错误/取消变为结果，shield只隔离外层重复取消。
    # 本层始终等到拥有的任务完成，不遗留后台执行包装任务。
    completion = asyncio.gather(task, return_exceptions=True)
    cancelled = None
    while not completion.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError as error:
            cancelled = error
    completion.result()
    return cancelled


async def wait_for_tool(execution: Awaitable[str], *, timeout: float) -> str:
    """预算到期请求一次取消并等待收尾；外部取消优先于超时报告。"""

    task = asyncio.ensure_future(execution)
    try:
        # wait只报告是否在预算内完成，不将子任务的取消异常转换成超时。
        done, _ = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        await _join(task)
        # 保留原外部取消，重复取消不会再次打断子任务的finally。
        raise

    if done:
        # 执行器主动取消或自己抛出TimeoutError均保留原语义。
        return task.result()

    task.cancel()
    cancelled = await _join(task)
    if cancelled is not None:
        raise cancelled
    # 即使执行器吞掉取消并返回结果，也不能伪装成预算内成功。
    # 收尾可能超过预算，本层不承诺强制终止或硬截止。
    raise ToolWaitTimeout()
