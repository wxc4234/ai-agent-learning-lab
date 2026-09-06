from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.runs as runs_router_module


def create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(runs_router_module.router)
    return TestClient(app)


def test_get_run_timeline_returns_events(monkeypatch):
    timeline = {
        "run_id": 17,
        "status": "done",
        "started_at": datetime(2026, 9, 6, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 9, 6, 0, 0, 1, tzinfo=timezone.utc),
        "duration_ms": 1000,
        "events": [
            {
                "id": 1,
                "event_type": "RUN_STARTED",
                "payload": {"prompt_length": 2},
                "created_at": datetime(2026, 9, 6, tzinfo=timezone.utc),
            },
            {
                "id": 2,
                "event_type": "RUN_FINISHED",
                "payload": {"status": "done"},
                "created_at": datetime(2026, 9, 6, 0, 0, 1, tzinfo=timezone.utc),
            },
        ],
    }
    monkeypatch.setattr(
        runs_router_module,
        "load_run_timeline",
        lambda run_id: timeline,
    )

    response = create_test_client().get("/runs/17")

    assert response.status_code == 200
    assert response.json()["run_id"] == 17
    assert response.json()["status"] == "done"
    assert [event["event_type"] for event in response.json()["events"]] == [
        "RUN_STARTED",
        "RUN_FINISHED",
    ]


def test_get_missing_run_returns_404(monkeypatch):
    monkeypatch.setattr(
        runs_router_module,
        "load_run_timeline",
        lambda run_id: (_ for _ in ()).throw(ValueError("missing")),
    )

    response = create_test_client().get("/runs/999")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "run_id: 999 不存在",
    }
