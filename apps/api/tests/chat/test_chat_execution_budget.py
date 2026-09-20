"""真实 ASGI/PostgreSQL 验证共享容量拒绝与副作用边界。"""

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, Message
from app.services.runtime.execution_budget import ExecutionBudget
from tests.chat import test_chat_execution_lifecycle as lifecycle
from tests.runtime.test_conversation_execution_service import tokens

scope_target = lifecycle.scope_target
lab = lifecycle.lab


@pytest.mark.parametrize("first", ["/chat", "/chat/stream"])
@pytest.mark.parametrize("second", ["/chat", "/chat/stream"])
def test_cross_entry_capacity_blocks_other_conversation_without_side_effects(lab, engine, monkeypatch, first, second):
    async def scenario():
        app, session_id, other = lab
        budget = app.state.execution_budget = ExecutionBudget(capacity=1)
        started, proceed = asyncio.Event(), asyncio.Event()
        calls = lifecycle.models(monkeypatch, started, proceed)
        task = asyncio.create_task(lifecycle.request(app, session_id, first))
        try:
            await asyncio.wait_for(started.wait(), 3)
            original = tokens(engine)
            with Session(engine) as session:
                before = session.scalar(select(func.count()).select_from(AgentRun))
            for target in [other, session_id]:
                result = await lifecycle.request(app, target, second)
                assert result[0]["status"] == 503
                assert b"execution_capacity_exceeded" in result[1]["body"]
                assert (b"cache-control", b"no-store") in result[0]["headers"]
                assert tokens(engine) == original and len(calls) == 1
                assert budget.in_use == 1
            # 本地会话授权先于预算检查，即使满额也保持统一404。
            assert (await lifecycle.request(app, "missing", second))[0]["status"] == 404
            with Session(engine) as session:
                assert session.scalar(select(func.count()).select_from(AgentRun)) == before
                assert session.scalar(select(func.count()).select_from(Message)) == 0
            proceed.set()
            await task
            assert budget.in_use == 0 and tokens(engine) == []
            assert (await lifecycle.request(app, other, second))[0]["status"] == 200
            assert budget.in_use == 0
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_missing_budget_fails_closed(lab, engine, monkeypatch, path):
    async def scenario():
        app, session_id, _ = lab
        del app.state.execution_budget
        calls = lifecycle.models(monkeypatch)
        result = await lifecycle.request(app, session_id, path)
        assert result[0]["status"] == 500
        assert b"chat_failed" in result[1]["body"]
        assert calls == [] and tokens(engine) == []
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    asyncio.run(scenario())
