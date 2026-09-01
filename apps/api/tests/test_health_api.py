from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.health import router as health_router


def test_home_returns_service_status():
    app = FastAPI()
    app.include_router(health_router)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "AI 对话服务已启动",
        "docs": "/docs",
    }