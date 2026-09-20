"""Agent Run 与运行事件的持久化实现。"""

import os
from datetime import datetime, timezone
from app.services.runtime.execution.execution_process import host_identity
from typing import Literal, TypedDict

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AgentRun, AgentRunEvent, Conversation
from app.repositories.chat.conversation_repository import (
    get_or_create_owned_conversation,
)


class RunTimelineEvent(TypedDict):
    """时间线中的单条事件。"""

    id: int
    event_type: str
    payload: dict[str, object]
    created_at: datetime


class RunTimeline(TypedDict):
    """一次运行记录的完整时间线。"""

    run_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    events: list[RunTimelineEvent]


class RunNotAccessibleError(Exception):
    """运行不存在或不属于当前用户。"""


def create_agent_run(
    *,
    user_id: int,
    session_id: str,
    prompt: str | None = None,
) -> int:
    """确认会话所有权后，在同一事务中创建运行记录。"""
    with SessionLocal.begin() as session:
        conversation = get_or_create_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
        )

        run = AgentRun(
            conversation_id=conversation.id,
            status="running",
            owner_host_id=host_identity(),
            owner_pid=os.getpid(),
        )
        session.add(run)
        session.flush()

        session.add(
            AgentRunEvent(
                run_id=run.id,
                event_type="RUN_STARTED",
                payload={
                    "session_id": session_id,
                    "prompt_length": len(prompt or ""),
                },
            ),
        )

        return run.id

def record_run_event(
    run_id: int, event_type: str, payload: dict[str, object] | None = None
) -> None:
    """保存一次运行事件。"""
    with SessionLocal.begin() as session:
        session.add(
            AgentRunEvent(
                run_id=run_id,
                event_type=event_type,
                payload=payload or {},
            )
        )


def request_run_cancellation(
    run_id: int,
    reason: Literal["user", "timeout"],
    *,
    user_id: int,
) -> bool:
    """原子地记录取消意图和终态，返回本次是否实际取消了运行。"""

    terminal_state_by_reason = {
        "user": ("aborted", "RUN_ABORTED"),
        "timeout": ("error", "RUN_ERROR"),
    }
    status, terminal_event_type = terminal_state_by_reason[reason]

    with SessionLocal.begin() as session:
        run = session.scalars(
            select(AgentRun)
            .join(
                Conversation,
                AgentRun.conversation_id == Conversation.id,
            )
            .where(
                AgentRun.id == run_id,
                Conversation.user_id == user_id,
            )
            .with_for_update(of=AgentRun)
        ).one_or_none()

        if run is None:
            raise RunNotAccessibleError()

        if run.status != "running":
            return False

        session.add(
            AgentRunEvent(
                run_id=run_id,
                event_type="RUN_CANCELLATION_REQUESTED",
                payload={"reason": reason},
            )
        )
        run.status = status
        run.finished_at = datetime.now(timezone.utc)
        session.add(
            AgentRunEvent(
                run_id=run_id,
                event_type=terminal_event_type,
                payload={"reason": reason},
            )
        )

        return True


def get_run_cancellation_reason(
    run_id: int,
) -> Literal["user", "timeout"] | None:
    """读取最近一次取消意图的原因。"""
    with SessionLocal() as session:
        event = session.scalars(
            select(AgentRunEvent)
            .where(
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.event_type == "RUN_CANCELLATION_REQUESTED",
            )
            .order_by(AgentRunEvent.id.desc())
        ).first()

    if event is None:
        return None

    reason = event.payload.get("reason")
    if reason in ("user", "timeout"):
        return reason

    return None


def finish_agent_run(
    run_id: int, status: str, payload: dict[str, object] | None = None
) -> None:
    """结束一次运行并记录最终状态。"""
    event_type_by_status = {
        "done": "RUN_FINISHED",
        "aborted": "RUN_ABORTED",
        "error": "RUN_ERROR",
    }
    event_type = event_type_by_status[status]

    with SessionLocal.begin() as session:
        run = session.scalars(
            select(AgentRun)
            .where(AgentRun.id == run_id)
            .with_for_update(of=AgentRun)
        ).one_or_none()

        if run is None:
            raise ValueError(f"run_id: {run_id} 不存在")

        # 取消接口已在自己的事务中写入终态时，流任务清理不得覆盖它。
        if run.status != "running":
            return

        run.status = status
        run.finished_at = datetime.now(timezone.utc)

        session.add(
            AgentRunEvent(
                run_id=run.id,
                event_type=event_type,
                payload=payload or {},
            )
        )


def load_run_timeline(
    run_id: int,
    *,
    user_id: int,
) -> RunTimeline:
    """读取一次运行的完整时间线。"""
    with SessionLocal() as session:
        run = session.scalars(
            select(AgentRun)
            .join(
                Conversation,
                AgentRun.conversation_id == Conversation.id,
            )
            .where(
                AgentRun.id == run_id,
                Conversation.user_id == user_id,
            )
        ).one_or_none()

        if run is None:
            raise RunNotAccessibleError()

        events = session.scalars(
            select(AgentRunEvent)
            .where(AgentRunEvent.run_id == run_id)
            .order_by(AgentRunEvent.id)
        ).all()

    duration_ms = None
    if run.finished_at is not None:
        duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)

    return {
        "run_id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": duration_ms,
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.create_at,
            }
            for event in events
        ],
    }
