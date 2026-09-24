"""Task 快照命令的请求级有界记录；保留句柄，不执行恢复或删除。"""

from dataclasses import dataclass, field, replace
from typing import Literal

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandRecovery, SampleCommandResult
from app.services.runtime.sandbox.sandbox_sample_reconciliation import validate_sample_command_recovery
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec

MAX_TASK_SAMPLE_COMMAND_RECORDS = 16
Status = Literal['pending', 'completed', 'unconfirmed', 'cancelled']


class TaskSampleJournalUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__('task_sample_journal_unavailable')


@dataclass(frozen=True, slots=True, repr=False)
class TaskSampleCommandRecord:
    argv: tuple[str, ...]
    working_directory: str
    status: Status = 'pending'
    recovery: SampleCommandRecovery | None = field(default=None, repr=False)
    # 正常结果用不可变 JSON 保存，非零退出仍然是 completed。
    command_json: str | None = field(default=None, repr=False)
    container_id: str | None = None
    execution_token: str | None = None
    sample_token: str | None = None
    sample_cleaned: bool = False

    def build_request(self) -> CommandRequest:
        return CommandRequest(argv=list(self.argv), working_directory=self.working_directory)


class TaskSampleCommandJournal:
    """单事件循环、单请求拥有；关闭不丢弃记录，也不释放资源。

    归属匹配只防止内部误用，不替代实际 Task 授权。拥有者须在请求退出后
    保留仍有未处置资源的 journal；本类不提供跨进程存储或自动回收。
    """

    def __init__(self, *, user_id: int, conversation_id: str) -> None:
        if type(user_id) is not int or user_id <= 0 or not isinstance(conversation_id, str) or not conversation_id:
            raise TaskSampleJournalUnavailable()
        self._scope = (user_id, conversation_id)
        self._closed = False
        self._records: list[TaskSampleCommandRecord] = []

    def require_scope(self, *, user_id: int, conversation_id: str) -> None:
        if type(user_id) is not int or self._scope != (user_id, conversation_id):
            raise TaskSampleJournalUnavailable()

    @property
    def records(self) -> tuple[TaskSampleCommandRecord, ...]:
        # recovery 与 CommandResult 均冻结；保留原样例句柄身份，不做深复制。
        return tuple(self._records)

    def reserve(self, request: CommandRequest) -> int:
        if self._closed or len(self._records) >= MAX_TASK_SAMPLE_COMMAND_RECORDS:
            raise TaskSampleJournalUnavailable()
        if not isinstance(request, CommandRequest):
            raise TypeError('request 必须是 CommandRequest')
        validated = CommandRequest.model_validate(request.model_dump())
        # 纯策略验证先于登记，不把明显不可执行的请求记成 pending。
        build_sandbox_create_spec(request=validated, execution_token='0' * 32)
        index = len(self._records)
        self._records.append(TaskSampleCommandRecord(tuple(validated.argv), validated.working_directory))
        return index

    def finish(
        self, index: int, *, status: Literal['completed', 'unconfirmed', 'cancelled'],
        recovery: SampleCommandRecovery | None = None, result: SampleCommandResult | None = None,
    ) -> None:
        # 检查与替换之间没有 await；关闭后仍允许在途调用补齐一次终态。
        if type(index) is not int or not 0 <= index < len(self._records):
            raise ValueError('记录索引无效')
        record = self._records[index]
        if record.status != 'pending' or status not in ('completed', 'unconfirmed', 'cancelled'):
            raise ValueError('记录终态无效或已完成')
        if status == 'completed':
            if not isinstance(result, SampleCommandResult) or recovery is not None or result.sample_cleaned is not True:
                raise ValueError('正常完成必须提供完整结果')
        elif result is not None:
            raise ValueError('未确认或取消不能附加正常完成结果')
        if recovery is not None:
            validate_sample_command_recovery(recovery)
            if (recovery.argv, recovery.working_directory) != (record.argv, record.working_directory):
                raise ValueError('恢复参数与记录不一致')
        self._records[index] = replace(
            record, status=status, recovery=recovery,
            command_json=None if result is None else result.command.model_dump_json(),
            container_id=None if result is None else result.cleanup.container_id,
            execution_token=None if result is None else result.cleanup.execution_token,
            sample_token=None if result is None else result.sample_token,
            sample_cleaned=False if result is None else result.sample_cleaned,
        )

    def close(self) -> None:
        self._closed = True
