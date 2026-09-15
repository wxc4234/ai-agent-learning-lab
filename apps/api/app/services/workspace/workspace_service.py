"""Workspace 创建服务：管理事务，并返回不依赖 ORM Session 的结果。"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.repositories.workspace.workspace_repository import create_workspace


@dataclass(frozen=True)
class WorkspaceCreationResult:
    """创建成功后的公开业务结果，不携带 ORM 对象或用户凭证。"""

    # 对外使用服务端生成的标识，不暴露数据库内部主键。
    external_id: str

    # 返回仓储规范化后的名称，而不是调用方传入的原始字符串。
    name: str

    # 沿用数据库生成的创建时间，不在服务层重新生成。
    created_at: datetime


def create_user_workspace(
    session: Session,
    *,
    user_id: int,
    name: str,
) -> WorkspaceCreationResult:
    """拥有一次创建事务；调用方负责提供并关闭无活动事务的 Session。"""

    # 检查必须在 try 外：拒绝调用方已有事务时，不能顺手回滚其工作。
    # 即使只是执行过 SELECT，也可能已经触发 SQLAlchemy 的自动开启事务。
    if session.in_transaction():
        raise RuntimeError("Workspace 创建服务需要无活动事务的 Session")

    try:
        # user_id 由可信调用方提供；后续 HTTP 层从 CurrentUser.id 获取。
        # 名称规范化与校验复用仓储，避免两层维护不同规则。
        workspace = create_workspace(
            session=session,
            user_id=user_id,
            name=name,
        )

        # 仓储 flush 后，主键和数据库生成的创建时间已经可用。
        # 在提交前复制普通字段，避免提交后访问过期 ORM 属性触发新的查询。
        result = WorkspaceCreationResult(
            external_id=workspace.external_id,
            name=workspace.name,
            created_at=workspace.created_at,
        )

        # commit 成功才允许返回；flush 成功不代表记录已经持久化。
        session.commit()

    except Exception:
        # 回滚本次服务拥有的事务，并保留原来的异常类型和调用链。
        # 名称错误仍是名称错误，数据库故障不能伪装成业务校验失败。
        session.rollback()
        raise

    return result
