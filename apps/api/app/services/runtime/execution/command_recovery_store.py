"""应用级有界恢复记录；仅供服务端内部使用，不提供公开查询接口。"""

from dataclasses import dataclass

from app.services.runtime.sandbox.command_recovery_journal import (
    CommandRecoveryJournal,
    CommandRecoveryJournalUnavailable,
)


MAX_COMMAND_RECOVERY_SCOPES = 32


@dataclass(frozen=True, slots=True)
class CommandRecoveryScope:
    """将记录容器绑定到服务端确认的用户、会话和运行。"""

    user_id: int
    session_id: str
    run_id: int
    journal: CommandRecoveryJournal


class CommandRecoveryStore:
    """单事件循环内的应用级存储，不自动覆盖未处理记录。"""

    def __init__(self) -> None:
        self._scopes: dict[int, CommandRecoveryScope] = {}

    def acquire(
        self,
        *,
        user_id: int,
        session_id: str,
        run_id: int,
    ) -> CommandRecoveryScope:
        """首次执行命令前登记；身份只能来自服务端运行上下文。"""

        if type(user_id) is not int or user_id <= 0:
            raise ValueError("user_id 必须是正整数")
        if type(run_id) is not int or run_id <= 0:
            raise ValueError("run_id 必须是正整数")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id 不能为空")

        existing = self._scopes.get(run_id)
        if existing is not None:
            if (
                existing.user_id != user_id
                or existing.session_id != session_id
            ):
                raise ValueError("运行恢复记录的归属不一致")
            return existing

        if len(self._scopes) >= MAX_COMMAND_RECOVERY_SCOPES:
            raise CommandRecoveryJournalUnavailable()

        scope = CommandRecoveryScope(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            journal=CommandRecoveryJournal(),
        )
        self._scopes[run_id] = scope
        return scope

    def get(
        self,
        *,
        user_id: int,
        session_id: str,
        run_id: int,
    ) -> CommandRecoveryScope | None:
        """内部按完整归属读取，不能仅凭Run ID取得恢复身份。"""

        scope = self._scopes.get(run_id)
        if scope is None:
            return None

        if (
            scope.user_id != user_id
            or scope.session_id != session_id
        ):
            return None

        return scope

    def close_scope(self, scope: CommandRecoveryScope) -> None:
        """关闭新登记；仅释放全部正常完成或没有命令的记录。"""

        # 必须是本存储持有的原对象，不能用同ID的新对象代替。
        if self._scopes.get(scope.run_id) is not scope:
            raise ValueError("恢复记录不属于当前存储")

        scope.journal.close()
        records = scope.journal.records

        # pending、unconfirmed和cancelled都需要保留。
        # completed表示统一执行及清理正常返回，不要求退出码为0。
        if all(record.status == "completed" for record in records):
            del self._scopes[scope.run_id]
