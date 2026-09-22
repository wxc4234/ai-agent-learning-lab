"""授权查询提案应用记录；不读取文件、不触发执行或恢复。"""

from dataclasses import dataclass
from typing import Literal, cast, get_args

from app.database import SessionLocal
from app.repositories.workspace.file_edit_proposal_repository import (
    read_owned_proposal_application_status,
)


ApplicationStatus = Literal[
    "idle",
    "running",
    "applied",
    "not_applied",
    "uncertain",
]


class ProposalApplicationQueryError(ValueError):
    """拒绝未知状态，不把异常数据解释成可重新执行。"""

    code = "proposal_application_status_invalid"

    def __init__(self) -> None:
        super().__init__("提案应用状态无法识别")


@dataclass(frozen=True)
class FileEditProposalApplicationStatus:
    """数据库记录的只读快照，不代表当前磁盘内容或执行进程状态。"""

    proposal_id: str
    workspace_id: str
    task_id: str
    application_status: ApplicationStatus


def get_task_file_edit_proposal_application_status(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
) -> FileEditProposalApplicationStatus:
    """每次查询重新授权，不将读取行为变成重试或状态修复。"""

    # 只读事务由Session作用域管理。
    # 无需commit；退出时关闭Session并释放连接。
    with SessionLocal() as session:
        row = read_owned_proposal_application_status(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
            proposal_id=proposal_id,
        )

        status = row["application_status"]

        # 数据库虽有约束，服务输出仍显式检查协议。
        # 先完成运行时校验，再通过cast表达已确认的类型。
        if (
            not isinstance(status, str)
            or status not in get_args(ApplicationStatus)
        ):
            raise ProposalApplicationQueryError()

        result = FileEditProposalApplicationStatus(
            proposal_id=row["proposal_id"],
            workspace_id=row["workspace_id"],
            task_id=row["task_id"],
            application_status=cast(ApplicationStatus, status),
        )

    return result
