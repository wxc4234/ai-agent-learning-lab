"""Workspace 创建与所有权查询，由调用方管理事务。"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace

class InvalidWorkspaceNameError(Exception):
    """工作空间名称不符合要求。"""

    code = "invalid_workspace_name"

    def __init__(self) -> None:
        super().__init__("工作空间名称去除首尾空白后须为 1～100 个字符")

class WorkspaceNotAccessibleError(Exception):
    """工作空间不存在，或不属于当前用户。"""

    code = "workspace_not_accessible"

    def __init__(self) -> None:
        super().__init__("工作空间不存在或不可访问")


def create_workspace(
    session: Session,
    *,
    user_id: int,
    name: str,
) -> Workspace:
    """为可信用户身份创建工作空间，只 flush，不提交事务。"""

    # 在 add/flush 前检查名称，非法输入不触发调用方待提交数据的刷新。
    normalized_name = name.strip()
    if not 1 <= len(normalized_name) <= 100:
        raise InvalidWorkspaceNameError()

    # 标识由服务端生成，调用方只提供可信用户身份与显示名称。
    workspace = Workspace(
        external_id=uuid4().hex,
        user_id=user_id,
        name=normalized_name,
    )

    session.add(workspace)
    # flush 取得主键并检查约束；提交或回滚仍由调用方决定。
    session.flush()

    return workspace

def require_owned_workspace(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
) -> Workspace:
    """按对外标识和所有者读取工作空间，不创建记录。"""

    # 标识与所有者必须同时匹配，未知资源和他人资源走同一拒绝分支。
    workspace = session.scalar(
        select(Workspace).where(
            Workspace.external_id == workspace_id,
            Workspace.user_id == user_id,
        )
    )

    if workspace is None:
        raise WorkspaceNotAccessibleError()

    return workspace

def list_owned_workspaces(
    session: Session,
    *,
    user_id: int,
    limit: int,
) -> list[Workspace]:
    """按当前身份查询最近的工作空间，不提交或回滚调用方事务。"""

    # HTTP 最多展示 100 条，内部允许额外查询一条来判断是否还有数据。
    if not 1 <= limit <= 101:
        raise ValueError("Workspace 查询数量必须为 1～101")

    statement = (
        select(Workspace)
        .where(Workspace.user_id == user_id)
        .order_by(
            Workspace.created_at.desc(),
            Workspace.id.desc(),
        )
        .limit(limit)
    )

    # 查询不应意外刷新调用方尚未提交的写入。
    # 相同创建时间使用唯一 ID 决定顺序，避免结果顺序不确定。
    with session.no_autoflush:
        return list(session.scalars(statement).all())

def require_owned_workspace_for_update(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
) -> Workspace:
    """按归属读取并锁定工作空间，由调用方结束事务。"""

    # 同时过滤标识和归属，未知资源与不可访问资源使用相同错误。
    statement = (
        select(Workspace)
        .where(
            Workspace.external_id == workspace_id,
            Workspace.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    # 不提前刷新调用方的待提交修改。
    # populate_existing 让查询结果覆盖 Session 中可能缓存的旧字段值。
    with session.no_autoflush:
        workspace = session.scalar(statement)

    if workspace is None:
        raise WorkspaceNotAccessibleError()

    # 此处不能 commit：绑定服务还需要在同一个事务里检查并保存路径。
    return workspace


def set_locked_workspace_root(workspace: Workspace, root_path: str | None) -> None:
    """调用方须已重新授权并持有Workspace行锁；不提交、不读取文件系统。

    所有应用内根目录变更复用本函数，路径与修订随外层事务一起回滚。
    修订不跟踪手工SQL、外部文件替换或未flush的临时属性变化，不构成CAS。
    """
    if workspace.root_path == root_path:
        return
    revision = workspace.binding_revision
    # 溢出前拒绝，不能把版本绕回旧值；新建行须先flush取得初始版本。
    if type(revision) is not int or not 1 <= revision < 2**63 - 1:
        raise ValueError("workspace_binding_revision_unavailable")
    workspace.root_path = root_path
    workspace.binding_revision = revision + 1
