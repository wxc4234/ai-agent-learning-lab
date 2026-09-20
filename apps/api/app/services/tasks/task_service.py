"""Task 创建服务：在同一事务中创建任务及其对应会话。"""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Task, TaskCreationRequest
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    require_owned_workspace_for_update,
)


class InvalidTaskTitleError(Exception):
    """任务标题不符合要求。"""

    code = "invalid_task_title"

    def __init__(self) -> None:
        super().__init__("任务标题去除首尾空白后须为 1～200 个字符")

class InvalidTaskRequestKeyError(Exception):
    """请求键不符合约定格式。"""

    code = "invalid_task_request_key"

    def __init__(self) -> None:
        super().__init__("任务创建请求键须为 32 位小写十六进制字符串")


class TaskCreationConflictError(Exception):
    """同一请求键已经用于不同的创建内容。"""

    code = "task_creation_conflict"

    def __init__(self) -> None:
        super().__init__("该请求键已用于不同的任务创建内容")


class TaskCreationResultDeletedError(Exception):
    """原创建结果已删除，旧请求不能重新创建任务。"""

    code = "task_creation_result_deleted"

    def __init__(self) -> None:
        super().__init__("该请求对应的任务已删除，请使用新的请求键创建")

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
    request_key: str | None = None,
) -> TaskCreationResult:
    """拥有一次创建事务；提供请求键时启用幂等创建。"""

    # 必须放在 try 外，不能回滚调用方已经开始的事务。
    if session.in_transaction():
        raise RuntimeError("Task 创建服务需要无活动事务的 Session")

    try:
        # 先授权并锁项目，再查询请求记录。
        # 请求键不是访问凭证，不能绕过项目归属检查。
        # 项目锁保持到事务结束，与删除服务保持相同的起始锁顺序。
        workspace = require_owned_workspace_for_update(
            session=session,
            user_id=user_id,
            workspace_id=workspace_id,
        )

        # 请求指纹必须基于真正写入的规范化内容。
        # 因此首尾空白不同、规范化后相同的标题视为同一份输入。
        if not isinstance(title, str):
            raise InvalidTaskTitleError()

        normalized_title = title.strip()

        if not 1 <= len(normalized_title) <= 200:
            raise InvalidTaskTitleError()

        # None 暂时兼容现有 HTTP 调用；空字符串不等于未提供。
        # 不自动修改请求键，避免不同输入被隐式合并。
        if request_key is not None and (
            not isinstance(request_key, str)
            or re.fullmatch(r"[0-9a-f]{32}", request_key) is None
        ):
            raise InvalidTaskRequestKeyError()

        # 固定字段排序、分隔符和编码，保证相同输入得到相同摘要。
        # 用户和项目已经构成查询作用域，摘要保存当前创建内容。
        request_payload = json.dumps(
            {"title": normalized_title},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request_hash = hashlib.sha256(
            request_payload.encode("utf-8")
        ).hexdigest()

        receipt = None

        if request_key is not None:
            # 项目锁已将遵循本服务协议的同项目创建串行化。
            # READ COMMITTED 下，等待项目锁后执行的新查询
            # 能看到前一个事务已经提交的请求记录。
            receipt = session.scalar(
                select(TaskCreationRequest)
                .where(
                    TaskCreationRequest.user_id == user_id,
                    TaskCreationRequest.workspace_id == workspace.id,
                    TaskCreationRequest.request_key == request_key,
                )
                .execution_options(populate_existing=True)
            )

        if receipt is not None:
            # 必须与首次创建时保存的指纹比较。
            # 不能拿任务当前标题比较，因为它可能已经被异步总结更新。
            if receipt.request_hash != request_hash:
                raise TaskCreationConflictError()

            # 删除 Task 后，数据库会将请求记录的 task_id 置空。
            # 这是已使用过的键，不能落入下面的新建分支。
            if receipt.task_id is None:
                raise TaskCreationResultDeletedError()

            # 请求记录只提供定位线索，仍须重新核对任务所属项目。
            # 锁顺序保持项目 → 任务 → 会话，与任务删除服务一致。
            task = session.scalar(
                select(Task)
                .where(
                    Task.id == receipt.task_id,
                    Task.workspace_id == workspace.id,
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
                # 会话缺失或归属错配时拒绝，不在重放时修复异常数据。
                raise WorkspaceNotAccessibleError()

        else:
            # 标题允许重复；不同请求键代表不同的创建意图。
            task = Task(
                external_id=uuid4().hex,
                workspace_id=workspace.id,
                title=normalized_title,
            )

            # 会话所有者取自已授权项目，使用 ORM 关系安排插入顺序。
            conversation = Conversation(
                external_id=uuid4().hex,
                user_id=workspace.user_id,
                title=normalized_title,
                task=task,
            )

            session.add_all([task, conversation])

            # flush 取得 Task 内部主键及数据库生成字段，但尚未提交。
            # 后面的请求记录写入失败时，这两条记录也必须回滚。
            session.flush()

            if request_key is not None:
                receipt = TaskCreationRequest(
                    user_id=workspace.user_id,
                    workspace_id=workspace.id,
                    request_key=request_key,
                    request_hash=request_hash,
                    task_id=task.id,
                )
                session.add(receipt)

                # 提交前检查请求记录的唯一约束和外键约束。
                # 不把任意 IntegrityError 当成可重放成功；
                # 未预期的数据库错误应整体回滚并保留原始异常。
                session.flush()

        # 新建与重放共用公开结果结构。
        # 重放返回任务当前标题，而不是首次创建标题的历史快照。
        # 提交前复制普通字段，避免提交后 ORM 过期读取开启新事务。
        result = TaskCreationResult(
            external_id=task.external_id,
            workspace_id=workspace.external_id,
            conversation_id=conversation.external_id,
            title=task.title,
            created_at=task.created_at,
        )

        # 新建时三条记录一次提交；重放时结束读事务并释放行锁。
        # 只有 commit 成功后才能返回结果。
        session.commit()

    except Exception:
        # 同时撤销本事务内的写入并释放锁，保留原始异常类型。
        session.rollback()
        raise

    return result
