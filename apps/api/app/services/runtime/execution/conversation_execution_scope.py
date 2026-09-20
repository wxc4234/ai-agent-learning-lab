"""组合会话占用和后台线程收尾，供聊天入口统一管理执行生命周期。"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import anyio

from app.database import SessionLocal
from app.services.runtime.execution.conversation_execution_service import (
    ConversationExecutionOwnership,
    acquire_conversation_execution,
    release_conversation_execution,
)
from app.services.runtime.execution.execution_threads import ExecutionThreads


def _acquire(
    *,
    user_id: int,
    session_id: str,
) -> ConversationExecutionOwnership:
    """在线程内部创建并关闭独立 Session。"""
    with SessionLocal() as session:
        # 获取服务负责提交或回滚短事务。
        # 返回后不持有数据库事务，也不跨线程传递 Session。
        return acquire_conversation_execution(
            session,
            user_id=user_id,
            session_id=session_id,
        )


def _release(
    *,
    user_id: int,
    ownership: ConversationExecutionOwnership,
) -> None:
    """使用本次获取返回的 token，执行独立释放事务。"""
    with SessionLocal() as session:
        released = release_conversation_execution(
            session,
            user_id=user_id,
            session_id=ownership.session_id,
            owner_token=ownership.owner_token,
        )

    if not released:
        # 本作用域只负责释放一次。
        # 匹配不到说明占用发生了预期外变化，不能静默当作正常成功。
        # 不尝试按会话强制删除，以免清除其他执行的占用。
        raise RuntimeError("执行占用与当前持有者不匹配")


async def _cleanup(
    *,
    user_id: int,
    acquisition: asyncio.Task[ConversationExecutionOwnership],
    threads: ExecutionThreads,
) -> None:
    """确认获取结果，等待后台工作结束，最后释放占用。"""
    try:
        # 即使外层在获取期间被取消，也继续等待真实获取结果。
        # 获取成功后仍能取得 token，而不是遗留一个无人负责的占用。
        ownership = await acquisition
    except Exception:  # noqa: BLE001 -- 原获取异常由外层传播，此处仅避免猜测性释放
        # 获取没有返回可确认的持有者，不执行猜测性释放。
        # 原获取异常或外层取消由作用域调用方继续接收。
        #
        # 若数据库已提交但确认丢失，此处同样没有可信 token；
        # 保守保留占用，交给后续恢复机制处理。
        return

    # 该清理协程由独立任务承载，外层取消不会直接取消它。
    # 只有所有登记线程结束，才允许开始释放事务。
    await threads.wait_closed()

    # 跟踪器已关闭，释放事务使用独立线程调用。
    # 外层会等待整个清理任务，因此释放完成也在收尾范围内。
    await asyncio.to_thread(
        _release,
        user_id=user_id,
        ownership=ownership,
    )


async def _wait_for_cleanup(
    cleanup_task: asyncio.Task[None],
) -> None:
    """保护完整收尾过程，完成后重新传播期间收到的取消。"""
    cancellation: asyncio.CancelledError | None = None

    # AnyIO 的取消域保护与 asyncio.shield 职责不同：
    # 前者阻止外层取消域反复打断清理等待；
    # 后者防止直接 Task.cancel() 连带取消内部清理任务。
    with anyio.CancelScope(shield=True):
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError as error:
                if cleanup_task.cancelled():
                    # 内部任务自身被取消时，不能无限重复等待，
                    # 更不能假装收尾成功。此时保守保留未释放占用。
                    raise

                # 记住外层取消，但先等待清理任务真正结束。
                cancellation = error

        # 明确读取结果：数据库释放失败必须向外传播，
        # 不能把失败的清理当作成功。
        cleanup_task.result()

    if cancellation is not None:
        raise cancellation


@asynccontextmanager
async def conversation_execution(
    *,
    user_id: int,
    session_id: str,
) -> AsyncGenerator[ExecutionThreads, None]:
    """持有已有会话的执行占用，并在退出时完成安全收尾。"""
    threads = ExecutionThreads()

    # 获取操作单独保存任务引用，便于取消后继续取得真实结果。
    acquisition = asyncio.create_task(
        asyncio.to_thread(
            _acquire,
            user_id=user_id,
            session_id=session_id,
        ),
    )

    try:
        # 获取失败或会话忙时，不进入业务代码。
        # shield 不吞掉调用方取消，只保护内部获取任务。
        await asyncio.shield(acquisition)

        # 调用方通过这个实例登记本次执行的后台线程。
        # 本作用域不自动发现其他地方自行启动的线程或任务。
        yield threads
    finally:
        # 独立任务负责完整清理，避免第二次取消截断释放步骤。
        cleanup_task = asyncio.create_task(
            _cleanup(
                user_id=user_id,
                acquisition=acquisition,
                threads=threads,
            ),
        )
        await _wait_for_cleanup(cleanup_task)
