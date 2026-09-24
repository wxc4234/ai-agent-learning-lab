"""请求级执行占用：覆盖响应发送、异步资源关闭和后台线程收尾。"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
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
from app.services.runtime.execution.command_recovery_store import (
    CommandRecoveryScope,
    CommandRecoveryStore,
)
from app.services.runtime.sandbox.command_recovery_journal import (
    CommandRecoveryJournalUnavailable,
)
from app.tools.run_command import CommandToolExecutionError, run_command


from app.services.runtime.agent.tool_execution_context import load_tool_execution_context
from app.services.runtime.execution.task_sample_recovery_store import TaskSampleRecoveryScope, TaskSampleRecoveryStore
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.services.workspace.samples.sample_execution_runtime import get_sample_bindings
from app.tools.context import ToolExecutionContext
from app.tools.registry import CommandToolBinding, GitStatusExecutor
from app.tools.git_sample_status import make_git_sample_status_executor
from app.services.workspace.git.application_samples import get_git_samples
from app.services.workspace.git.task_git_samples import TaskGitSamples
from app.tools.task_sample_command import TaskSampleCommandToolContext, run_task_sample_command_tool
from app.tools.errors import SafeToolExecutionError


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
    # 应用持有存储，请求只持有引用；默认值兼容已有内部构造路径。
    # 真正执行命令时必须存在，不允许临时创建存储绕过容量限制。
    command_recovery_store: CommandRecoveryStore | None = None

    # 首次命令调用时才登记，不让普通聊天占用恢复记录容量。
    command_scope: CommandRecoveryScope | None = field(
        default=None,
        init=False,
    )
    _command_closed: bool = field(default=False, init=False)
    task_sample_recovery_store: TaskSampleRecoveryStore | None = None
    sample_bindings: TaskSampleBindings | None = None
    sample_scope: TaskSampleRecoveryScope | None = field(default=None, init=False)

    # 延迟读取当前应用所有者：普通无Task聊天不需要Git能力，也不临时创建manager。
    git_samples_provider: Callable[[], TaskGitSamples] | None = None

    def bind_git_status_tool(self, context: ToolExecutionContext) -> GitStatusExecutor:
        if (
            self._command_closed or not isinstance(context, ToolExecutionContext)
            or context.user_id != self.user_id or context.conversation_id != self.body.session_id
            or self.git_samples_provider is None
        ):
            raise SafeToolExecutionError('task_git_sample_unavailable')
        try:
            manager = self.git_samples_provider()
            adapter = make_git_sample_status_executor(manager)
        except Exception:  # noqa: BLE001 -- 缺失/关闭应用资源不能降级或公开内部异常。
            raise SafeToolExecutionError('task_git_sample_unavailable') from None
        expected_context = context

        def execute(*, context: ToolExecutionContext) -> str:
            if self._command_closed or context is not expected_context:
                raise SafeToolExecutionError('task_git_sample_unavailable')
            # Runtime在线程中调用；短授权事务结束后才进入Git采集。
            try:
                current = load_tool_execution_context(
                    user_id=self.user_id, conversation_id=self.body.session_id,
                )
            except Exception:  # noqa: BLE001 -- 会话授权失败只公开固定错误。
                raise SafeToolExecutionError('workspace_not_accessible') from None
            if current != expected_context or self._command_closed:
                raise SafeToolExecutionError('workspace_not_accessible')
            return adapter(context=context)

        return execute

    async def bind_command_tool(self, context: ToolExecutionContext) -> CommandToolBinding | None:
        """查询只决定请求能力快照；实际执行仍重新授权并借用，不缓存 ready 为许可。"""
        if (
            self._command_closed or not isinstance(context, ToolExecutionContext)
            or context.user_id != self.user_id or context.conversation_id != self.body.session_id
            or not isinstance(self.sample_bindings, TaskSampleBindings)
        ):
            raise SafeToolExecutionError('command_recovery_unavailable')
        status = await self.threads.run(
            self.sample_bindings.read_status, user_id=context.user_id,
            workspace_id=context.workspace_id, task_id=context.task_id,
        )
        if self._command_closed:
            raise SafeToolExecutionError('command_recovery_unavailable')
        if status.status == 'missing':
            return CommandToolBinding(self.execute_command)
        if status.status != 'ready':
            return None

        async def execute(*, argv: list[str], working_directory: str = '.') -> str:
            return await self.execute_task_command(
                context=context, argv=argv, working_directory=working_directory,
            )
        return CommandToolBinding(execute, sample_snapshot=True)

    async def execute_task_command(
        self, *, context: ToolExecutionContext, argv: list[str], working_directory: str = '.',
    ) -> str:
        if (
            self._command_closed or not isinstance(self.task_sample_recovery_store, TaskSampleRecoveryStore)
            or self.creation is None or not self.creation.done() or self.creation.cancelled()
        ):
            raise SafeToolExecutionError('command_recovery_unavailable')
        # 先拒绝请求能力建立后可观察的会话迁移；借用内部还会再授权及核对绑定。
        try:
            current = await self.threads.run(
                load_tool_execution_context, user_id=self.user_id, conversation_id=self.body.session_id,
            )
        except Exception:  # noqa: BLE001 -- 授权读取失败不能降级到普通命令。
            raise SafeToolExecutionError('task_command_preparation_unconfirmed') from None
        if current != context or self._command_closed:
            raise SafeToolExecutionError('task_command_preparation_unconfirmed')
        try:
            run_id = self.creation.result()
            scope = self.task_sample_recovery_store.acquire(
                user_id=self.user_id, conversation_id=self.body.session_id, run_id=run_id,
            )
        except Exception:  # noqa: BLE001 -- Run 和存储失败仅公开固定错误。
            raise SafeToolExecutionError('command_recovery_unavailable') from None
        self.sample_scope = scope
        return await run_task_sample_command_tool(
            argv=argv, working_directory=working_directory,
            context=TaskSampleCommandToolContext(
                self.user_id, self.body.session_id, run_id,
                self.task_sample_recovery_store, self.sample_bindings, expected_context=context,
            ),
        )

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

    async def execute_command(
        self,
        *,
        argv: list[str],
        working_directory: str = ".",
    ) -> str:
        """服务端绑定的命令入口，不允许模型指定恢复容器或运行身份。"""

        if (
            self._command_closed
            or not isinstance(
                self.command_recovery_store,
                CommandRecoveryStore,
            )
            or self.creation is None
            or not self.creation.done()
            or self.creation.cancelled()
        ):
            raise CommandToolExecutionError(
                "command_recovery_unavailable",
            )

        try:
            # 不重新创建Run，也不从模型参数读取Run ID。
            run_id = self.creation.result()
        except Exception:  # noqa: BLE001 -- Run创建失败时只返回固定安全错误。
            raise CommandToolExecutionError(
                "command_recovery_unavailable",
            ) from None

        if self.command_scope is None:
            try:
                self.command_scope = self.command_recovery_store.acquire(
                    user_id=self.user_id,
                    session_id=self.body.session_id,
                    run_id=run_id,
                )
            except CommandRecoveryJournalUnavailable:
                raise CommandToolExecutionError(
                    "command_recovery_unavailable",
                ) from None

        return await run_command(
            argv=argv,
            working_directory=working_directory,
            recovery_journal=self.command_scope.journal,
        )

    async def close(self) -> None:
        """停止新命令，关闭异步资源，再排空线程并整理恢复记录。"""

        self._command_closed = True
        if self.sample_scope is not None:
            self.sample_scope.journal.close()
        if self.command_scope is not None:
            # 关闭登记不阻止在途命令补齐异常或取消证据。
            self.command_scope.journal.close()

        try:
            for monitor in self.monitors:
                monitor.cancel()

            if self.monitors:
                results = await asyncio.gather(
                    *self.monitors,
                    return_exceptions=True,
                )
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
                    except Exception:  # noqa: BLE001 -- 无可信ID时不猜测更新运行。
                        logger.error("chat_run_creation_unconfirmed")

                await self.threads.wait_closed()

                if run_id is not None:
                    # 独立短事务补齐运行状态，不覆盖已有终态。
                    await asyncio.to_thread(
                        finish_agent_run,
                        run_id,
                        "aborted",
                        {"reason": "response_closed"},
                    )
            finally:
                conversations.pop(
                    (self.user_id, self.body.session_id),
                    None,
                )

                if self.sample_scope is not None and self.task_sample_recovery_store is not None:
                    self.task_sample_recovery_store.close_scope(self.sample_scope)

                # 未确认、取消或仍pending的记录继续由应用存储持有。
                # 即使请求对象随后回收，恢复身份也不会随之丢失。
                if (
                    self.command_scope is not None
                    and self.command_recovery_store is not None
                ):
                    self.command_recovery_store.close_scope(
                        self.command_scope,
                    )


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
    recovery_store = getattr(
        request.app.state,
        "command_recovery_store",
        None,
    )

    # 应用资源只能在生命周期中统一装配，不能按请求临时创建。
    if not isinstance(budget, ExecutionBudget):
        raise RuntimeError("应用执行预算尚未初始化")  # noqa: TRY004

    if not isinstance(recovery_store, CommandRecoveryStore):
        raise RuntimeError("命令恢复存储尚未初始化")  # noqa: TRY004

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
                command_recovery_store=recovery_store,
                task_sample_recovery_store=getattr(request.app.state, 'task_sample_recovery_store', None),
                sample_bindings=get_sample_bindings(),
                git_samples_provider=lambda: get_git_samples(request),
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
