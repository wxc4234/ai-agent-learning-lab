"""跟踪一次执行启动的同步工作，供占用释放前等待线程结束。"""

import asyncio
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar


Parameters = ParamSpec("Parameters")
Result = TypeVar("Result")


class ExecutionThreads:
    """每次聊天执行独立创建，不在不同会话之间共享。"""

    def __init__(self) -> None:
        # 保留强引用：调用方不再等待时，仍须跟踪后台任务。
        # 已完成任务也暂时保留，关闭后统一清理。
        self._tasks: set[asyncio.Task[Any]] = set()

        # 开始收尾后拒绝新工作，保证等待集合不再增长。
        self._closing = False

        # 重复调用关闭方法时，复用同一个等待结果。
        self._drain: asyncio.Future[Any] | None = None

    @staticmethod
    def _observe_completion(task: asyncio.Task[Any]) -> None:
        """读取后台异常，避免调用方取消后产生未处理异常警告。"""
        if not task.cancelled():
            # 读取异常不改变任务结果；正常等待者仍会收到原异常。
            task.exception()

    async def run(
        self,
        function: Callable[Parameters, Result],
        *args: Parameters.args,
        **kwargs: Parameters.kwargs,
    ) -> Result:
        """在线程中执行同步函数，取消等待时仍保留后台工作。"""
        if self._closing:
            raise RuntimeError("执行正在收尾，不能启动新的线程工作")

        # 检查状态、创建任务和登记之间没有 await，
        # 因而同一事件循环内不会被关闭操作插入。
        task = asyncio.create_task(
            asyncio.to_thread(function, *args, **kwargs),
        )
        self._tasks.add(task)
        task.add_done_callback(self._observe_completion)

        # shield 只保护内部任务。
        # 外层等待仍可取消，但不会连带取消线程任务的 asyncio 包装。
        return await asyncio.shield(task)

    async def wait_closed(self) -> None:
        """停止接收新工作，并等待所有已登记任务结束。"""
        self._closing = True

        if self._drain is None:
            # 某个工作失败不应提前结束收尾。
            # 业务异常由 run 的调用方处理；这里仅确认所有工作已结束。
            self._drain = asyncio.gather(
                *self._tasks,
                return_exceptions=True,
            )

        cancellation_requested = False

        while True:
            try:
                # 收尾期间再次取消外层等待，也不能取消后台任务集合。
                await asyncio.shield(self._drain)
                break
            except asyncio.CancelledError:
                # 延后传播取消，直到线程工作真正结束。
                # 重复 cancel 也必须走同样的收尾过程。
                cancellation_requested = True

        self._tasks.clear()

        if cancellation_requested:
            # 不吞掉取消，只把它延后到安全收尾之后。
            raise asyncio.CancelledError
