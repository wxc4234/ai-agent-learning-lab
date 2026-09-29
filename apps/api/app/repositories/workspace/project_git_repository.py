"""Git来源的联表只读授权查询；事务由服务层关闭，不加写锁。"""

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.models import Conversation, Task, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError


def read_owned_git_binding(session: Session, *, user_id: int, workspace_id: str, task_id: str) -> RowMapping:
    # 同一条SELECT同时核对Workspace、Task和Conversation，避免分步查出混合归属。
    row = session.execute(select(
        Workspace.id.label('workspace_pk'), Task.id.label('task_pk'),
        Conversation.id.label('conversation_pk'), Workspace.root_path, Workspace.binding_revision,
    ).join(Task, Task.workspace_id == Workspace.id)
        .join(Conversation, Conversation.task_id == Task.id)
        .where(Workspace.user_id == user_id, Workspace.external_id == workspace_id,
               Task.external_id == task_id, Conversation.user_id == user_id)).mappings().one_or_none()
    if row is None:
        raise WorkspaceNotAccessibleError()
    return row
