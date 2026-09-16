"""任务运行历史查询：先授权，再分页读取轻量概要。"""

import re

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AgentRun
from app.schemas import TaskRunItemResponse, TaskRunListResponse
from app.services.tasks.task_workspace import owned_task


IDENTIFIER_PATTERN = re.compile(r"^[0-9a-f]{32}$")

# 当前 Run 主键使用 PostgreSQL INTEGER。
MAX_RUN_ID = 2_147_483_647
MAX_PAGE_SIZE = 50


class InvalidTaskRunQueryError(Exception):
    """任务运行历史的查询参数不符合要求。"""

    code = "invalid_task_run_query"

    def __init__(self) -> None:
        super().__init__("任务运行历史查询参数不符合要求")


def list_task_runs(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    before: int | None = None,
    limit: int = 20,
) -> TaskRunListResponse:
    """读取本人项目中指定任务的运行历史，不修改运行状态。"""

    # 服务可能被 HTTP 之外的代码直接调用，因此保留参数校验。
    # fullmatch 确保整个标识都符合要求。
    if (
        not isinstance(workspace_id, str)
        or IDENTIFIER_PATTERN.fullmatch(workspace_id) is None
        or not isinstance(task_id, str)
        or IDENTIFIER_PATTERN.fullmatch(task_id) is None
    ):
        raise InvalidTaskRunQueryError()

    # bool 是 int 的子类，用 type 精确判断，避免接受 True/False。
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise InvalidTaskRunQueryError()

    if before is not None and (
        type(before) is not int
        or not 1 <= before <= MAX_RUN_ID
    ):
        raise InvalidTaskRunQueryError()

    with SessionLocal() as session:
        # 必须先授权，即使任务没有运行记录，也不能直接返回空列表。
        # 此函数检查项目归属、Task 所属项目和 Conversation 归属。
        task, conversation = owned_task(
            session,
            user_id,
            workspace_id,
            task_id,
        )

        # 只选择列表需要的列，不访问 run.events 或事件 payload。
        query = select(
            AgentRun.id,
            AgentRun.status,
            AgentRun.started_at,
            AgentRun.finished_at,
        ).where(
            AgentRun.conversation_id == conversation.id,
        )

        # 游标表示“读取比它更小的 ID”，不要求游标记录仍然存在。
        # 查询始终限定在已授权会话，游标不会扩大资源访问范围。
        if before is not None:
            query = query.where(AgentRun.id < before)

        # 多取一条，只用于判断有没有下一页。
        rows = session.execute(
            query
            .order_by(AgentRun.id.desc())
            .limit(limit + 1)
        ).all()

        page = rows[:limit]
        items: list[TaskRunItemResponse] = []

        for run_id, status, started_at, finished_at in page:
            duration_ms = None

            if finished_at is not None:
                duration_ms = int(
                    (finished_at - started_at).total_seconds() * 1000
                )

            items.append(
                TaskRunItemResponse(
                    run_id=run_id,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                )
            )

        # 返回最后一条“已展示记录”的 ID，不能使用额外探测行的 ID。
        next_cursor = (
            str(page[-1].id)
            if len(rows) > limit
            else None
        )

        # 在 Session 关闭前构造完整返回值，不向外暴露 ORM 对象。
        result = TaskRunListResponse(
            workspace_id=workspace_id,
            task_id=task.external_id,
            items=items,
            next_cursor=next_cursor,
        )

    # 查询不需要 commit；关闭 Session 会结束本次只读事务。
    # 不写数据库，不调用模型，也不发送 Redis 通知。
    return result
