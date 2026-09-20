"""请求级执行占用：覆盖响应发送、异步资源关闭和后台线程收尾。"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request

from app.config import settings
from app.dependencies import CurrentUser
from app.database import SessionLocal
from app.repositories.chat.conversation_repository import (
    ensure_owned_conversation,
    require_owned_conversation,
)
from app.repositories.runtime.run_repository import create_agent_run, finish_agent_run
from app.schemas import ChatRequest
from app.services.chat.chat_service import conversations
from app.services.runtime.execution.conversation_execution_scope import (
    _wait_for_cleanup,
    conversation_execution,
)
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.runtime.execution.execution_budget import ExecutionBudget


logger = logging.getLogger(__name__)


@dataclass
class ChatExecution:
    """本请求独占的资源；Session 始终在线程内部创建和关闭。"""

    user_id: int
    body: ChatRequest
    threads: ExecutionThreads
    # 保留创建任务，取消等待后仍可取得随后成功创建的 Run ID。
    creation: asyncio.Task[int] | None = None
    stream: AsyncGenerator[str, None] | None = None
    monitors: list[asyncio.Task[None]] = field(default_factory=list)

    async def start_run(self) -> int:
        if self.creation is not None:
            raise RuntimeError("同一聊天请求不能重复创建运行")
        self.creation = asyncio.create_task(self.threads.run(
            create_agent_run,
            user_id=self.user_id,
            session_id=self.body.session_id,
            prompt=self.body.prompt,
        ))
        return await asyncio.shield(self.creation)

    async def close(self) -> None:
        """先关闭异步资源，再排空线程；退出后外层作用域才能释放占用。"""
        try:
            for monitor in self.monitors:
                monitor.cancel()
            if self.monitors:
                results = await asyncio.gather(*self.monitors, return_exceptions=True)
                if any(isinstance(result, Exception) for result in results):
                    logger.error("chat_cancellation_monitor_failed")
            if self.stream is not None:
                await self.stream.aclose()
        finally:
            try:
                run_id = None
                if self.creation is not None:
                    try:
                        run_id = await self.creation
                    except Exception:  # noqa: BLE001 -- 没有可信 ID 时不猜测更新运行
                        logger.error("chat_run_creation_unconfirmed")
                await self.threads.wait_closed()
                if run_id is not None:
                    # 独立短事务补齐仍 running 的运行，不覆盖已有终态。
                    # close 本身受到取消保护，释放前会等待这个线程完成。
                    await asyncio.to_thread(
                        finish_agent_run, run_id, "aborted", {"reason": "response_closed"},
                    )
            finally:
                # 提交确认可能丢失；下轮从数据库恢复，不信任内存残留。
                conversations.pop((self.user_id, self.body.session_id), None)


def _check_local_conversation(
    *,
    user_id: int,
    session_id: str,
) -> None:
    """在预算检查前验证本地会话归属，不创建会话或获取占用。"""

    # Session 在线程内部创建和关闭，只做授权读取。
    with SessionLocal() as session:
        require_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
        )

async def require_chat_execution(
    request: Request,
    body: ChatRequest,
    current_user: CurrentUser,
) -> AsyncGenerator[ChatExecution, None]:
    if settings.app_mode == "local":
        # 无权访问的会话仍返回既有授权错误，不被容量不足掩盖。
        # 等待读取线程结束后再传播取消，避免留下无人等待的数据库工作。
        await _wait_for_cleanup(
            asyncio.create_task(
                asyncio.to_thread(
                    _check_local_conversation,
                    user_id=current_user.id,
                    session_id=body.session_id,
                )
            )
        )
    else:
        # 保留账号扩展的原有会话准备行为。
        # 该步骤可能创建会话，但尚不创建 Run 或调用模型。
        await _wait_for_cleanup(
            asyncio.create_task(
                asyncio.to_thread(
                    ensure_owned_conversation,
                    user_id=current_user.id,
                    session_id=body.session_id,
                )
            )
        )

    budget = getattr(request.app.state, "execution_budget", None)

    # 装配缺失属于服务错误，不能临时创建预算而绕过共享容量限制。
    if not isinstance(budget, ExecutionBudget):
        raise RuntimeError("应用执行预算尚未初始化")  # noqa: TRY004 -- 应用装配失败，不是调用参数类型错误

    # 预算必须位于最外层：
    # 内层请求收尾和会话占用释放全部退出后，才归还进程内名额。
    with budget.reserve():
        async with conversation_execution(
            user_id=current_user.id,
            session_id=body.session_id,
        ) as threads:
            # 获取占用时仍会重新授权，不能用前面的读取代替事务内检查。
            conversations.pop((current_user.id, body.session_id), None)

            execution = ChatExecution(
                user_id=current_user.id,
                body=body,
                threads=threads,
            )

            try:
                yield execution
            finally:
                await _wait_for_cleanup(
                    asyncio.create_task(execution.close())
                )


CurrentChatExecution = Annotated[
    ChatExecution,
    Depends(require_chat_execution, scope="request"),
]
