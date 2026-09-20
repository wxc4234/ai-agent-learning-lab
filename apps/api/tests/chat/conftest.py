"""认证边界单元测试的执行替身；真实占用另由 PostgreSQL 集成验证。"""
from contextlib import asynccontextmanager

import pytest

from app.routers.chat import chat_execution
from app.services.runtime.execution.execution_threads import ExecutionThreads


@pytest.fixture
def execution_stub(monkeypatch):
    @asynccontextmanager
    async def fake_scope(**kwargs):
        tracker = ExecutionThreads()
        try:
            yield tracker
        finally:
            await tracker.wait_closed()

    monkeypatch.setattr(chat_execution, "conversation_execution", fake_scope)
    monkeypatch.setattr(chat_execution, "ensure_owned_conversation", lambda **kwargs: None)
    monkeypatch.setattr(chat_execution, "finish_agent_run", lambda *args: None)
