"""保存一次调用作用域中的命令恢复证据，不执行外部操作。"""

from dataclasses import dataclass, replace
from typing import Literal

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.sandbox_command import SandboxCommandRecovery


MAX_COMMAND_RECOVERY_RECORDS = 16

CommandAttemptStatus = Literal[
    "pending",
    "completed",
    "unconfirmed",
    "cancelled",
]


@dataclass(frozen=True, slots=True)
class CommandRecoveryRecord:
    """内部记录；参数和恢复身份不得直接进入模型Observation。"""

    # 保存不可变参数快照，不继续引用调用方的可变argv。
    argv: tuple[str, ...]
    working_directory: str
    status: CommandAttemptStatus = "pending"
    recovery: SandboxCommandRecovery | None = None

    def build_request(self) -> CommandRequest:
        """为后续只读核对构造独立请求对象。"""

        return CommandRequest(
            argv=list(self.argv),
            working_directory=self.working_directory,
        )


class CommandRecoveryJournalUnavailable(RuntimeError):
    """记录容器关闭或容量耗尽，不允许启动新的命令。"""

    def __init__(self) -> None:
        super().__init__("命令恢复记录当前不可接收新执行")


class CommandRecoveryJournal:
    """单个事件循环内由调用方拥有的有界记录容器。"""

    def __init__(self) -> None:
        self._records: list[CommandRecoveryRecord] = []
        self._closed = False

    @property
    def records(self) -> tuple[CommandRecoveryRecord, ...]:
        """返回不可变快照，不暴露内部列表。"""

        return tuple(self._records)

    def reserve(self, request: CommandRequest) -> int:
        """执行前预留记录，容量不足时不得先启动再尝试保存。"""

        if self._closed or len(self._records) >= MAX_COMMAND_RECOVERY_RECORDS:
            raise CommandRecoveryJournalUnavailable()

        if not isinstance(request, CommandRequest):
            raise TypeError("request 必须是 CommandRequest")

        validated = CommandRequest.model_validate(request.model_dump())
        record = CommandRecoveryRecord(
            argv=tuple(validated.argv),
            working_directory=validated.working_directory,
        )

        # 检查与登记之间没有await；同一事件循环内不会被其他协程插入。
        # 不承诺跨线程共享安全。
        index = len(self._records)
        self._records.append(record)
        return index

    def finish(
        self,
        index: int,
        *,
        status: Literal["completed", "unconfirmed", "cancelled"],
        recovery: SandboxCommandRecovery | None = None,
    ) -> None:
        """补齐已有尝试；关闭只禁止新登记，不阻止在途执行收尾。"""

        if type(index) is not int or not 0 <= index < len(self._records):
            raise ValueError("记录索引无效")

        if status not in ("completed", "unconfirmed", "cancelled"):
            raise ValueError("命令尝试终态无效")

        if recovery is not None and not isinstance(
            recovery,
            SandboxCommandRecovery,
        ):
            raise TypeError("recovery 必须是 SandboxCommandRecovery")

        if status == "completed" and recovery is not None:
            raise ValueError("正常完成不能附带失败恢复证据")

        record = self._records[index]
        if record.status != "pending":
            raise ValueError("命令尝试已经记录终态")

        self._records[index] = replace(
            record,
            status=status,
            recovery=recovery,
        )

    def close(self) -> None:
        """停止接收新执行；记录仍由拥有者读取和处理。"""

        self._closed = True
