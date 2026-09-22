"""绑定本地项目目录，统一管理归属检查、行锁与事务。"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories.workspace.proposal_application_guard import require_no_active_proposal_application

from app.repositories.workspace.workspace_repository import (
    require_owned_workspace_for_update,
)
from app.services.workspace.directory.workspace_directory import validate_workspace_directory


class WorkspaceAlreadyBoundError(Exception):
    """工作空间已经绑定其他目录，不允许直接覆盖。"""

    code = "workspace_already_bound"

    def __init__(self) -> None:
        super().__init__("工作空间已绑定其他项目目录")


@dataclass(frozen=True)
class WorkspaceBindingResult:
    """返回普通字段，避免离开服务后继续依赖 ORM Session。"""

    external_id: str
    name: str
    root_path: str


def bind_workspace_directory(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
    root_path: str,
) -> WorkspaceBindingResult:
    """为工作空间绑定规范目录；调用方负责提供并关闭 Session。"""

    # 放在 try 外，防止拒绝已有事务时回滚调用方的其他工作。
    if session.in_transaction():
        raise RuntimeError("目录绑定服务需要无活动事务的 Session")

    try:
        # user_id 必须来自可信服务端身份，不能直接信任浏览器输入。
        # 先检查资源归属，再访问文件系统；锁持续到提交或回滚。
        workspace = require_owned_workspace_for_update(
            session=session,
            user_id=user_id,
            workspace_id=workspace_id,
        )

        # 复用上一课的目录规则，只保存解析后的真实绝对路径。
        # 这里不创建目录，也不授予后续文件工具访问权限。
        normalized_path = str(validate_workspace_directory(root_path))

        # 同一路径是无写入的幂等查询；只有变更目标时检查整个项目的应用占用。
        if workspace.root_path != normalized_path:
            require_no_active_proposal_application(session, workspace_id=workspace.id)

        # 允许重复绑定同一规范路径，但不能覆盖已经绑定的其他目录。
        # 在行锁内判断，避免两个并发请求都认为当前尚未绑定。
        if (
            workspace.root_path is not None
            and workspace.root_path != normalized_path
        ):
            raise WorkspaceAlreadyBoundError()

        # 相同路径不重复赋值；首次绑定由 commit 自动刷新到数据库。
        if workspace.root_path is None:
            workspace.root_path = normalized_path

        # 提交前复制普通字段，避免提交后读取过期 ORM 属性开启新事务。
        result = WorkspaceBindingResult(
            external_id=workspace.external_id,
            name=workspace.name,
            root_path=normalized_path,
        )

        # 提交成功才返回；同时释放该工作空间的行锁。
        session.commit()

    except Exception:
        # 保留归属、目录校验、绑定冲突与数据库异常的原始分类。
        session.rollback()
        raise

    return result
