"""提案的授权查询、归属锁与插入；事务由服务层管理。"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.models import Conversation, FileEditProposal, Task, Workspace
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    require_owned_workspace_for_update,
)


def lock_owned_proposal_task(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
) -> tuple[Workspace, Task]:
    """沿用删除流程的项目→任务→会话锁顺序。"""

    workspace = require_owned_workspace_for_update(
        session=session,
        user_id=user_id,
        workspace_id=workspace_id,
    )

    task = session.scalar(
        select(Task)
        .where(
            Task.workspace_id == workspace.id,
            Task.external_id == task_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise WorkspaceNotAccessibleError()

    conversation = session.scalar(
        select(Conversation)
        .where(
            Conversation.task_id == task.id,
            Conversation.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if conversation is None:
        raise WorkspaceNotAccessibleError()

    return workspace, task


def insert_file_edit_proposal(
    session: Session,
    *,
    task_id: int,
    bound_root: str,
    relative_path: str,
    baseline_sha256: str,
    proposed_content: str,
    proposed_sha256: str,
    diff: str,
    diff_truncated: bool,
) -> FileEditProposal:
    """插入服务端已生成的内容，不接收模型指定的状态或提案编号。"""

    proposal = FileEditProposal(
        external_id=uuid4().hex,
        task_id=task_id,
        bound_root=bound_root,
        relative_path=relative_path,
        baseline_sha256=baseline_sha256,
        proposed_content=proposed_content,
        proposed_sha256=proposed_sha256,
        diff=diff,
        diff_truncated=diff_truncated,
        status="pending",
    )
    session.add(proposal)

    # flush取得数据库字段并检查约束，但不提交调用方事务。
    session.flush()
    return proposal


def read_owned_file_edit_proposal(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
) -> RowMapping:
    """在同一查询中授权并投影公开字段，编号本身不是访问凭据。"""

    # 不加载完整 ORM 实体，避免私有目录和新文件正文进入查询结果。
    # 只读审阅不持有行锁，也不证明文件仍符合保存时的基线。
    statement = (
        select(
            FileEditProposal.external_id.label("proposal_id"),
            Workspace.external_id.label("workspace_id"),
            Task.external_id.label("task_id"),
            FileEditProposal.relative_path,
            FileEditProposal.status,
            FileEditProposal.baseline_sha256,
            FileEditProposal.proposed_sha256,
            FileEditProposal.diff,
            FileEditProposal.diff_truncated,
            FileEditProposal.created_at,
        )
        .select_from(FileEditProposal)
        .join(Task, FileEditProposal.task_id == Task.id)
        .join(Workspace, Task.workspace_id == Workspace.id)
        .join(Conversation, Conversation.task_id == Task.id)
        .where(
            FileEditProposal.external_id == proposal_id,
            Task.external_id == task_id,
            Workspace.external_id == workspace_id,
            Workspace.user_id == user_id,
            Conversation.user_id == user_id,
        )
    )
    # 仓库不提交调用方事务，也不因读取而自动 flush 无关待写对象。
    with session.no_autoflush:
        result = session.execute(statement).mappings().one_or_none()
    if result is None:
        # 不区分不存在和无权访问，避免通过错误枚举他人提案。
        raise WorkspaceNotAccessibleError()
    return result


def lock_file_edit_proposal_for_decision(
    session: Session,
    *,
    task_id: int,
    proposal_id: str,
) -> FileEditProposal:
    """锁定已授权任务下的提案；调用方必须先取得归属链上的锁。"""

    # 固定顺序为Workspace→Task→Conversation→FileEditProposal。
    # 这里的task_id必须来自前面的授权查询，不能使用外部传入的内部主键。
    statement = (
        select(FileEditProposal)
        .where(
            FileEditProposal.task_id == task_id,
            FileEditProposal.external_id == proposal_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    with session.no_autoflush:
        proposal = session.scalar(statement)

    if proposal is None:
        # 与无权访问保持相同错误，不泄露提案是否属于其他任务。
        raise WorkspaceNotAccessibleError()

    return proposal


def read_owned_proposal_application_source(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
) -> RowMapping:
    """内部核对专用投影；完整内容和绑定路径不得直接作为公开响应。"""

    statement = (
        select(
            FileEditProposal.id.label("proposal_pk"),
            Task.id.label("task_pk"),
            Workspace.id.label("workspace_pk"),
            Conversation.id.label("conversation_pk"),
            Workspace.root_path.label("current_root"),
            FileEditProposal.bound_root,
            FileEditProposal.relative_path,
            FileEditProposal.status,
            FileEditProposal.baseline_sha256,
            FileEditProposal.proposed_sha256,
            FileEditProposal.proposed_content,
            FileEditProposal.diff_truncated,
        )
        .select_from(FileEditProposal)
        .join(Task, FileEditProposal.task_id == Task.id)
        .join(Workspace, Task.workspace_id == Workspace.id)
        .join(Conversation, Conversation.task_id == Task.id)
        .where(
            FileEditProposal.external_id == proposal_id,
            Task.external_id == task_id,
            Workspace.external_id == workspace_id,
            Workspace.user_id == user_id,
            Conversation.user_id == user_id,
        )
    )
    # 查询不flush、不加锁、不提交；文件I/O之前必须结束此事务。
    with session.no_autoflush:
        row = session.execute(statement).mappings().one_or_none()
    if row is None:
        raise WorkspaceNotAccessibleError()
    return row


def read_owned_proposal_application_status(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
) -> RowMapping:
    """同一次查询完成归属校验，只投影应用状态与公开资源标识。"""

    # 不加载完整ORM对象，避免执行令牌、绑定路径和正文进入结果。
    # 不要求当前绑定有效：目录变化不能阻止用户查看历史执行记录。
    statement = (
        select(
            FileEditProposal.external_id.label("proposal_id"),
            Workspace.external_id.label("workspace_id"),
            Task.external_id.label("task_id"),
            FileEditProposal.application_status,
        )
        .select_from(FileEditProposal)
        .join(Task, FileEditProposal.task_id == Task.id)
        .join(Workspace, Task.workspace_id == Workspace.id)
        .join(Conversation, Conversation.task_id == Task.id)
        .where(
            FileEditProposal.external_id == proposal_id,
            Task.external_id == task_id,
            Workspace.external_id == workspace_id,
            Workspace.user_id == user_id,
            Conversation.user_id == user_id,
        )
    )

    # 查询不自动flush调用方的其他修改，也不持有行锁或提交事务。
    with session.no_autoflush:
        row = session.execute(statement).mappings().one_or_none()

    if row is None:
        # 不区分记录不存在和没有权限，避免泄露他人资源信息。
        raise WorkspaceNotAccessibleError()

    return row
