"""Agent Run 与运行事件的持久化实现。"""

from datetime import datetime, timezone
from typing import TypedDict

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AgentRun, AgentRunEvent
from app.repositories.conversation_repository import get_or_create_conversation


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


def create_agent_run(session_id: str, prompt: str | None = None) -> int:
    """创建一次运行记录，并返回 run_id。"""

    with SessionLocal.begin() as session:
        conversation = get_or_create_conversation(session, session_id)

        run = AgentRun(
            conversation_id=conversation.id,
            status="running",
        )
        session.add(run)
        session.flush()  # 生成 run.id

        session.add(
            AgentRunEvent(
                run_id=run.id,
                event_type="RUN_STARTED",
                payload={
                    "session_id": session_id,
                    "prompt_length": len(prompt or ""),
                },
            )
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


def finish_agent_run(run_id: int, status: str) -> None:
    """结束一次运行并记录最终状态。"""
    event_type_by_status = {
        "done": "RUN_FINISHED",
        "aborted": "RUN_ABORTED",
        "error": "RUN_ERROR",
    }

    with SessionLocal.begin() as session:
        run = session.get(AgentRun, run_id)

        if run is None:
            raise ValueError(f"run_id: {run_id} 不存在")

        run.status = status
        run.finished_at = datetime.now(timezone.utc)

        session.add(
            AgentRunEvent(
                run_id=run.id,
                event_type=event_type_by_status[status],
                payload={},
            )
        )


def load_run_timeline(run_id: int) -> RunTimeline:
    """读取一次运行的完整时间线。"""
    with SessionLocal() as session:
        run = session.get(AgentRun, run_id)

        if run is None:
            raise ValueError(f"run_id: {run_id} 不存在")

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
