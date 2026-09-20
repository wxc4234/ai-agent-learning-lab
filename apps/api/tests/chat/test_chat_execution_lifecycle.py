from app.services.runtime.execution.execution_budget import ExecutionBudget
"""真实 ASGI 请求、PostgreSQL 与受控模型验证执行占用生命周期。"""

import asyncio
import json
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import select, func
from starlette.requests import ClientDisconnect
from sqlalchemy.orm import Session, sessionmaker

from app.dependencies import require_current_user
from app.models import AgentRun, Message
from app.repositories.chat import conversation_repository
from app.repositories.runtime import run_repository
from app.routers.chat import chat, chat_execution
from app.services.auth.authentication_service import AuthenticatedUser
from app.services.chat import chat_service
from app.services.runtime.execution import conversation_execution_scope
from app.services.runtime.agent import tool_execution_context
from app.services.runtime.agent.agent_runtime import FinalAnswer
from tests.runtime.execution import test_conversation_execution_service as service_tests
from tests.runtime.execution.test_conversation_execution_service import tokens
from tests.runtime.execution.test_execution_threads import checkpoint


scope_target = service_tests.target


@pytest.fixture
def lab(engine, scope_target, monkeypatch):
    factory = sessionmaker(bind=engine)
    for module in (conversation_repository, run_repository, conversation_execution_scope, chat_execution, tool_execution_context):
        monkeypatch.setattr(module, "SessionLocal", factory)
    app = FastAPI()
    app.state.execution_budget = ExecutionBudget(capacity=2)
    app.include_router(chat.router)
    app.dependency_overrides[require_current_user] = lambda: AuthenticatedUser(scope_target[0], "local", "local")

    async def never(*args):
        await asyncio.Future()

    monkeypatch.setattr(chat_service, "wait_for_run_cancellation", never)
    chat_service.conversations.clear()
    yield app, scope_target[1], scope_target[3]
    chat_service.conversations.clear()


async def request(app, session_id, path, *, spec="2.4", fail_at=None, started=None, disconnect=None):
    body = json.dumps({"session_id": session_id, "prompt": "hello"}).encode()
    received = False
    messages = []

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        if disconnect is not None:
            await disconnect.wait()
            return {"type": "http.disconnect"}
        await asyncio.Future()

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.start" and started is not None:
            started.set()
        if fail_at == message["type"]:
            raise OSError("client disconnected")

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": spec},
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "http_version": "1.1",
        "server": ("test", 80), "client": ("127.0.0.1", 1234),
        "headers": [(b"origin", b"http://localhost:3000"), (b"content-type", b"application/json")],
    }
    await app(scope, receive, send)
    return messages


def models(monkeypatch, started=None, proceed=None):
    calls = []

    async def answer():
        calls.append(True)
        if started is not None:
            started.set()
        if proceed is not None:
            await proceed.wait()
        return "reply"

    class Decision:
        def __init__(self, **kwargs):
            pass

        async def __call__(self, observations):
            return FinalAnswer(content=await answer())

    async def completion(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=await answer()))])

    monkeypatch.setattr(chat_service, "DeepSeekDecisionMaker", Decision)
    monkeypatch.setattr(chat_service.client.chat.completions, "create", completion)
    return calls


@pytest.mark.parametrize("first", ["/chat", "/chat/stream"])
@pytest.mark.parametrize("second", ["/chat", "/chat/stream"])
def test_cross_entry_exclusion_and_independent_conversation(lab, engine, monkeypatch, first, second):
    async def scenario():
        app, session_id, other = lab
        started, proceed = asyncio.Event(), asyncio.Event()
        calls = models(monkeypatch, started, proceed)
        task = asyncio.create_task(request(app, session_id, first))
        try:
            await asyncio.wait_for(started.wait(), 3)
            original = tokens(engine)
            with Session(engine) as session:
                runs_before = session.scalar(select(func.count()).select_from(AgentRun))
            rejected = await request(app, session_id, second)
            assert rejected[0]["status"] == 409
            assert b"conversation_busy" in rejected[1]["body"]
            assert len(calls) == 1 and tokens(engine) == original
            with Session(engine) as session:
                assert session.scalar(select(func.count()).select_from(AgentRun)) == runs_before
                assert session.scalar(select(func.count()).select_from(Message)) == 0
            # 另一个会话不受占用影响，用立即完成的模型独立执行。
            models(monkeypatch)
            independent = await request(app, other, second)
            assert independent[0]["status"] == 200
            proceed.set()
            assert (await asyncio.wait_for(task, 3))[0]["status"] == 200
            assert tokens(engine) == [] and chat_service.conversations == {}
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("spec", ["2.0", "2.4"])
@pytest.mark.parametrize("fail_at", ["http.response.start", "http.response.body"])
def test_send_failure_and_unstarted_generator_are_closed(lab, engine, monkeypatch, spec, fail_at):
    models(monkeypatch)

    async def scenario():
        with pytest.raises((OSError, ClientDisconnect)):
            await request(lab[0], lab[1], "/chat/stream", spec=spec, fail_at=fail_at)
        assert tokens(engine) == [] and chat_service.conversations == {}
        with Session(engine) as session:
            assert session.scalar(select(AgentRun.status)) == "aborted"
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["create", "save"])
def test_cancel_during_database_commit_waits_then_finishes(lab, engine, monkeypatch, stage):
    async def scenario():
        lab[0].state.execution_budget = ExecutionBudget(capacity=1)
        models(monkeypatch)
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        allow = Event()
        module = chat_execution if stage == "create" else chat_service
        name = "create_agent_run" if stage == "create" else "save_conversation_turn"
        original = getattr(module, name)

        def paused(*args, **kwargs):
            result = original(*args, **kwargs)
            loop.call_soon_threadsafe(entered.set)
            if not allow.wait(8):
                raise AssertionError("test did not release database worker")
            return result

        monkeypatch.setattr(module, name, paused)
        task = asyncio.create_task(request(lab[0], lab[1], "/chat/stream"))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            for _ in range(3):
                task.cancel()
                await checkpoint()
                assert not task.done() and len(tokens(engine)) == 1
            assert lab[0].state.execution_budget.in_use == 1
            rejected = await request(lab[0], lab[2], "/chat")
            assert rejected[0]["status"] == 503
            allow.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert tokens(engine) == [] and chat_service.conversations == {}
            with Session(engine) as session:
                assert session.scalar(select(AgentRun.status)) == "aborted"
                assert session.scalar(select(func.count()).select_from(Message)) == (2 if stage == "save" else 0)
            # 再请求必须从数据库恢复，不能使用被取消协程留下的缓存。
            monkeypatch.setattr(module, name, original)
            assert (await request(lab[0], lab[1], "/chat"))[0]["status"] == 200
        finally:
            allow.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("spec", ["2.0", "2.4"])
def test_disconnect_during_real_tool_keeps_slot_until_thread_stops(lab, engine, monkeypatch, spec):
    from app.services.runtime.agent.agent_runtime import ToolAction, ModelUsage
    from app.tools.registry import TOOL_REGISTRY, ToolDefinition, GetCurrentTimeArguments
    from tests.runtime.execution.test_execution_threads import ControlledWork

    async def scenario():
        lab[0].state.execution_budget = ExecutionBudget(capacity=1)
        work = ControlledWork()
        disconnected = asyncio.Event()

        class Decision:
            def __init__(self, **kwargs):
                pass

            async def __call__(self, observations):
                return ToolAction(tool_call_id="test", tool_name="blocked", model_usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2), arguments='{"utc_offset_hours": 8}')

        monkeypatch.setattr(chat_service, "DeepSeekDecisionMaker", Decision)
        monkeypatch.setitem(TOOL_REGISTRY, "blocked", ToolDefinition(
            name="blocked", description="受控工具", arguments_model=GetCurrentTimeArguments,
            executor=lambda **kwargs: work(), timeout_seconds=5,
        ))
        task = asyncio.create_task(request(lab[0], lab[1], "/chat/stream", spec=spec, disconnect=disconnected))
        try:
            await asyncio.wait_for(work.started.wait(), 3)
            if spec == "2.0":
                disconnected.set()
            else:
                task.cancel()
            await checkpoint()
            assert not task.done() and len(tokens(engine)) == 1
            assert lab[0].state.execution_budget.in_use == 1
            rejected = await request(lab[0], lab[2], "/chat")
            assert rejected[0]["status"] == 503
            work.release.set()
            if spec == "2.0":
                await asyncio.wait_for(task, 3)
            else:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            assert work.finished.is_set() and tokens(engine) == []
            assert lab[0].state.execution_budget.in_use == 0
            with Session(engine) as session:
                assert session.scalar(select(AgentRun.status)) == "aborted"
        finally:
            work.release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
