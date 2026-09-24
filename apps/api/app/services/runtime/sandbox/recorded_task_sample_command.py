"""预留记录后执行 Task 快照命令；不提供公开工具错误映射或自动恢复。"""

import asyncio

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.sandbox_sample_command import (
    SampleCommandCancelled,
    SampleCommandResult,
    SampleCommandUnconfirmed,
)
from app.services.runtime.sandbox.task_sample_command import run_task_sample_command
from app.services.runtime.sandbox.task_sample_command_journal import (
    TaskSampleCommandJournal,
    TaskSampleJournalUnavailable,
)
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.context import ToolExecutionContext


async def run_recorded_task_sample_command(
    *, request: CommandRequest, user_id: int, conversation_id: str,
    bindings: TaskSampleBindings, journal: TaskSampleCommandJournal,
    expected_context: ToolExecutionContext | None = None,
) -> SampleCommandResult:
    """在任何准备副作用前预留；保存终态与传播结果之间没有 await。"""

    if not isinstance(journal, TaskSampleCommandJournal):
        raise TaskSampleJournalUnavailable()
    journal.require_scope(user_id=user_id, conversation_id=conversation_id)
    index = journal.reserve(request)
    # 从记录构造新请求，不让调用方对原列表的修改影响执行参数。
    frozen_request = journal.records[index].build_request()
    try:
        result = await run_task_sample_command(
            request=frozen_request, user_id=user_id,
            conversation_id=conversation_id, bindings=bindings,
            **({} if expected_context is None else {"expected_context": expected_context}),
        )
    except SampleCommandCancelled as error:
        journal.finish(index, status='cancelled', recovery=error.recovery)
        raise
    except asyncio.CancelledError:
        journal.finish(index, status='cancelled')
        raise
    except SampleCommandUnconfirmed as error:
        journal.finish(index, status='unconfirmed', recovery=error.recovery)
        raise
    except Exception:  # noqa: BLE001 -- 无证据的失败保持未知，不反射底层错误。
        journal.finish(index, status='unconfirmed')
        raise RuntimeError('task_sample_command_unconfirmed') from None
    journal.finish(index, status='completed', result=result)
    return result
