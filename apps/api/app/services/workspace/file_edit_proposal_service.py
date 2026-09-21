"""生成、保存与授权查询待审批文件修改提案，不执行文件写入。"""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from app.database import SessionLocal
from app.repositories.workspace.file_edit_proposal_repository import (
    insert_file_edit_proposal,
    lock_owned_proposal_task,
    read_owned_file_edit_proposal,
)
from app.services.tasks.task_workspace import owned_task
from app.services.workspace.workspace_file_preview import (
    preview_task_file_replacement,
)
from app.services.workspace.workspace_path import WorkspacePathError


class ProposalBindingChangedError(ValueError):
    """生成预览期间资源或目录绑定发生变化，不能保存到另一目标。"""

    code = "proposal_binding_changed"

    def __init__(self) -> None:
        super().__init__("任务或项目目录绑定已变化，请重新生成提案")


@dataclass(frozen=True)
class CreatedFileEditProposal:
    """提交成功后返回公开定位信息，不暴露宿主路径或完整文件内容。"""

    proposal_id: str
    workspace_id: str
    task_id: str
    relative_path: str
    status: str
    baseline_sha256: str
    proposed_sha256: str
    diff_truncated: bool
    created_at: datetime


def create_task_file_edit_proposal(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    relative_path: str,
    old_text: str,
    new_text: str,
) -> CreatedFileEditProposal:
    """复制归属快照、生成预览，再重新授权并提交待审批提案。"""

    with SessionLocal() as session:
        task, _ = owned_task(
            session,
            user_id,
            workspace_id,
            task_id,
        )
        expected_task_id = task.id
        expected_workspace_id = task.workspace_id
        expected_root = task.workspace.root_path

    # 第一段只有查询，Session关闭后再进行文件访问和Diff计算。
    if expected_root is None:
        raise WorkspacePathError(
            "workspace_directory_unbound",
            "当前任务所属项目尚未绑定本地目录",
        )

    source = preview_task_file_replacement(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        relative_path=relative_path,
        old_text=old_text,
        new_text=new_text,
    )

    proposed_sha256 = sha256(
        source.preview.updated_content.encode("utf-8")
    ).hexdigest()

    # 新Session拥有唯一写事务；异常自动回滚。
    # 文件读取和Diff计算已结束，不占用下面的数据库行锁。
    with SessionLocal.begin() as session:
        workspace, task = lock_owned_proposal_task(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )

        if (
            task.id != expected_task_id
            or workspace.id != expected_workspace_id
            or workspace.root_path != expected_root
        ):
            raise ProposalBindingChangedError()

        proposal = insert_file_edit_proposal(
            session,
            task_id=task.id,
            bound_root=expected_root,
            relative_path=source.relative_path,
            baseline_sha256=source.baseline_sha256,
            proposed_content=source.preview.updated_content,
            proposed_sha256=proposed_sha256,
            diff=source.preview.diff,
            diff_truncated=source.preview.diff_truncated,
        )

        # 提交前复制普通字段，避免离开Session后读取ORM对象。
        result = CreatedFileEditProposal(
            proposal_id=proposal.external_id,
            workspace_id=workspace.external_id,
            task_id=task.external_id,
            relative_path=proposal.relative_path,
            status=proposal.status,
            baseline_sha256=proposal.baseline_sha256,
            proposed_sha256=proposal.proposed_sha256,
            diff_truncated=proposal.diff_truncated,
            created_at=proposal.created_at,
        )

    # 必须退出begin并成功提交后才能返回；提交失败不返回成功结果。
    return result


@dataclass(frozen=True)
class FileEditProposalDetail:
    """已保存提案的审阅快照，不包含内部路径或完整待写内容。"""

    proposal_id: str
    workspace_id: str
    task_id: str
    relative_path: str
    status: str
    baseline_sha256: str
    proposed_sha256: str
    diff: str
    diff_truncated: bool
    created_at: datetime


def get_task_file_edit_proposal(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
) -> FileEditProposalDetail:
    """重新授权后读取历史提案；不重新读文件、不批准或应用修改。"""

    # Session 仅拥有一次查询的只读事务，退出时关闭并释放连接，无需 commit。
    with SessionLocal() as session:
        row = read_owned_file_edit_proposal(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
            proposal_id=proposal_id,
        )
        result = FileEditProposalDetail(**row)
    return result
