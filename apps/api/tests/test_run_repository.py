import pytest
from sqlalchemy.orm import Session, sessionmaker
from app.models import User


@pytest.fixture
def owner_id(engine):
    with Session(engine) as session, session.begin():
        user = User(external_id="run-test-owner")
        session.add(user)
        session.flush()
        return user.id


from app.repositories import run_repository


def test_run_repository_records_and_loads_timeline(monkeypatch, engine, owner_id):
    test_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(run_repository, "SessionLocal", test_session_local)

    run_id = run_repository.create_agent_run(user_id=owner_id, session_id="timeline-session")

    run_repository.record_run_event(
        run_id=run_id,
        event_type="TEXT_MESSAGE_CONTENT",
        payload={"chunk": "第一段"},
    )

    run_repository.record_run_event(
        run_id=run_id,
        event_type="TEXT_MESSAGE_CONTENT",
        payload={"chunk": "第二段"},
    )

    run_repository.finish_agent_run(
        run_id=run_id,
        status="done",
    )

    timeline = run_repository.load_run_timeline(run_id)

    assert timeline["run_id"] == run_id
    assert timeline["status"] == "done"
    assert timeline["duration_ms"] is not None

    events = timeline["events"]

    assert [event["event_type"] for event in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "RUN_FINISHED",
    ]

    assert events[1]["payload"] == {"chunk": "第一段"}
    assert events[2]["payload"] == {"chunk": "第二段"}


def test_finish_run_rejects_unknown_status(monkeypatch, engine, owner_id):
    test_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(run_repository, "SessionLocal", test_session_local)

    run_id = run_repository.create_agent_run(user_id=owner_id, session_id="invalid-status-session")

    try:
        run_repository.finish_agent_run(
            run_id=run_id,
            status="unknown",
        )
    except KeyError:
        pass
    else:
        raise AssertionError("未知运行状态应该被拒绝")


def test_cancelling_a_running_run_records_its_terminal_event(monkeypatch, engine, owner_id):
    test_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(run_repository, "SessionLocal", test_session_local)

    run_id = run_repository.create_agent_run(user_id=owner_id, session_id="cancel-run-session")

    assert run_repository.request_run_cancellation(run_id, "user") is True
    assert run_repository.request_run_cancellation(run_id, "timeout") is False

    timeline = run_repository.load_run_timeline(run_id)

    assert timeline["status"] == "aborted"
    assert [event["event_type"] for event in timeline["events"]] == [
        "RUN_STARTED",
        "RUN_CANCELLATION_REQUESTED",
        "RUN_ABORTED",
    ]
    assert timeline["events"][-1]["payload"] == {"reason": "user"}
