"""Task 创建服务：在同一事务中创建任务及其对应会话。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Conversation, Task
from app.repositories.workspace.workspace_repository import (
    require_owned_workspace_for_update,
)


class InvalidTaskTitleError(Exception):
    """任务标题不符合要求。"""

    code = "invalid_task_title"

    def __init__(self) -> None:
        super().__init__("任务标题去除首尾空白后须为 1～200 个字符")


@dataclass(frozen=True)
class TaskCreationResult:
    """创建结果只包含普通数据，不依赖尚未关闭的 ORM Session。"""

    # Task 对外标识，不是数据库内部自增主键。
    external_id: str

    # 所属 Workspace 的对外标识。
    workspace_id: str

    # 新会话的对外标识，后续用于接入聊天和恢复历史。
    conversation_id: str

    # 返回规范化后的标题。
    title: str

    # 使用数据库生成的任务创建时间。
    created_at: datetime


def create_workspace_task(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
    title: str,
) -> TaskCreationResult:
    """拥有一次创建事务；调用方负责提供并关闭独立 Session。"""

    # 必须放在 try 外，拒绝已有事务时不能回滚调用方正在进行的工作。
    if session.in_transaction():
        raise RuntimeError("Task 创建服务需要无活动事务的 Session")

    try:
        # 身份由可信调用方提供，项目必须同时匹配标识和所有者。
        # 锁保持到提交，避免创建期间项目被删除或归属被并发修改。
        workspace = require_owned_workspace_for_update(
            session=session,
            user_id=user_id,
            workspace_id=workspace_id,
        )

        # 在创建记录之前完成标题校验，不把空白标题保存进数据库。
        # isinstance 让直接调用服务时的错误类型也得到明确业务异常。
        if not isinstance(title, str):
            raise InvalidTaskTitleError()

        normalized_title = title.strip()

        if not 1 <= len(normalized_title) <= 200:
            raise InvalidTaskTitleError()

        # Task 与 Conversation 分别生成独立标识。
        # 标题允许重复，不能拿标题作为唯一标识或幂等键。
        task = Task(
            external_id=uuid4().hex,
            workspace_id=workspace.id,
            title=normalized_title,
        )

        # 会话所有者取自已经授权的项目，保证这次创建的归属一致。
        # 使用 ORM 关系关联任务，由 SQLAlchemy 安排插入顺序和外键值。
        conversation = Conversation(
            external_id=uuid4().hex,
            user_id=workspace.user_id,
            title=normalized_title,
            task=task,
        )

        session.add_all([task, conversation])

        # flush 执行两条记录的写入、检查约束并取得数据库生成字段。
        # 此时仍未提交，后续任何一步失败都可以整体回滚。
        session.flush()

        # 提交前复制普通字段，避免提交后访问过期 ORM 属性开启新事务。
        result = TaskCreationResult(
            external_id=task.external_id,
            workspace_id=workspace.external_id,
            conversation_id=conversation.external_id,
            title=task.title,
            created_at=task.created_at,
        )

        # 只有提交成功才返回；不在 Task 和 Conversation 之间分别提交。
        session.commit()

    except Exception:
        # 保留原始异常类型，数据库错误不伪装成标题或权限错误。
        session.rollback()
        raise

    return result
