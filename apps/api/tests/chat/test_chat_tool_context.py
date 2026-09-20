"""聊天上下文装配：执行顺序、取消跟踪及隔离数据库到真实文件的链路。"""

import asyncio
import json
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import AgentRun, AgentRunEvent, Message, Workspace
from app.repositories.chat import conversation_repository
from app.repositories.chat.conversation_repository import ConversationNotAccessibleError
from app.repositories.runtime import run_repository
from app.services.chat import chat_service as service
from app.services.runtime.agent import tool_execution_context
from app.services.runtime.agent.agent_runtime import AgentLoopCompleted, AgentLoopResult
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.workspace import workspace_path
from app.tools.context import ToolExecutionContext
from tests.model.test_model_decision import build_text_response, build_tool_response
from tests.tasks import test_task_deletion_service as task_fixtures


target = task_fixtures.target


@pytest.fixture(autouse=True)
def isolated_history_and_monitor(monkeypatch):
    async def never(*args):
        await asyncio.Future()

    monkeypatch.setattr(service, "wait_for_run_cancellation", never)
    service.conversations.clear()
    yield
    service.conversations.clear()


async def collect(user_id=1, session_id="c", run_id=1):
    tracker = ExecutionThreads()
    monitors = []
    try:
        return [json.loads(line) async for line in service.stream_chat_reply(
            user_id=user_id, session_id=session_id, prompt="read", run_id=run_id,
            execution_threads=tracker, monitors=monitors,
        )]
    finally:
        for monitor in monitors:
            monitor.cancel()
        await asyncio.gather(*monitors, return_exceptions=True)
        await tracker.wait_closed()


@pytest.fixture
def unit(monkeypatch):
    records = []
    monkeypatch.setattr(service, "record_run_event", lambda *args: None)
    monkeypatch.setattr(service, "finish_agent_run", lambda *args: records.append(args))
    monkeypatch.setattr(service, "save_conversation_turn", lambda **kwargs: None)
    monkeypatch.setattr(service, "get_run_cancellation_reason", lambda *args: "user")
    return records


@pytest.mark.parametrize("mode", ["local", "account"])
def test_order_thread_and_identical_context(unit, monkeypatch, mode):
    monkeypatch.setattr(settings, "app_mode", mode)
    expected = ToolExecutionContext(1, "c", "w", "t") if mode == "local" else None
    order = []
    main_thread = get_ident()

    def load(**kwargs):
        assert mode == "local"
        assert kwargs == {"user_id": 1, "conversation_id": "c"}
        assert get_ident() != main_thread
        order.append("context")
        return expected

    async def prepare(**kwargs):
        order.append("history")
        return [{"role": "user", "content": "read"}], []

    def decision(**kwargs):
        assert kwargs["tool_context"] is expected
        order.append("model")
        return object()

    async def loop(decide, **kwargs):
        assert kwargs["tool_context"] is expected
        assert isinstance(kwargs["execution_threads"], ExecutionThreads)
        order.append("runtime")
        yield AgentLoopCompleted(AgentLoopResult("completed", "done", 1, ()))

    monkeypatch.setattr(service, "load_tool_execution_context", load)
    monkeypatch.setattr(service, "_prepare_chat_messages", prepare)
    monkeypatch.setattr(service, "DeepSeekDecisionMaker", decision)
    monkeypatch.setattr(service, "stream_agent_loop", loop)
    events = asyncio.run(collect())
    assert order == (["context"] if mode == "local" else []) + ["history", "model", "runtime"]
    assert events[-1]["type"] == "RUN_FINISHED"


@pytest.mark.parametrize("error", [ConversationNotAccessibleError(), RuntimeError("PRIVATE database")])
def test_context_failure_before_history_or_model(unit, monkeypatch, error):
    monkeypatch.setattr(settings, "app_mode", "local")

    def fail(**kwargs):
        raise error

    def forbidden(**kwargs):
        pytest.fail("must not prepare history or call model")

    monkeypatch.setattr(service, "load_tool_execution_context", fail)
    monkeypatch.setattr(service, "_prepare_chat_messages", forbidden)
    monkeypatch.setattr(service, "DeepSeekDecisionMaker", forbidden)
    events = asyncio.run(collect())
    assert len(events) == 1 and events[0]["type"] == "RUN_ERROR"
    assert "PRIVATE" not in json.dumps(events)
    assert len(unit) == 1 and unit[0][1] == "error"
    assert not service.conversations


def test_cancel_context_query_waits_for_actual_thread(unit, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "local")
    started, release, finished = Event(), Event(), Event()

    def load(**kwargs):
        started.set()
        try:
            assert release.wait(3)
            return ToolExecutionContext(1, "c", "w", "t")
        finally:
            finished.set()

    monkeypatch.setattr(service, "load_tool_execution_context", load)
    monkeypatch.setattr(service, "DeepSeekDecisionMaker", lambda **kwargs: pytest.fail("model called"))

    async def scenario():
        task = asyncio.create_task(collect())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            # 等待流取消处理触发终态，collect的finally仍应等待查询线程。
            async with asyncio.timeout(2):
                while not unit:
                    await asyncio.sleep(0)
            assert not task.done() and not finished.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert finished.is_set()

    asyncio.run(scenario())
    assert unit[0][1] == "aborted"


@pytest.mark.parametrize("bound", [True, False])
def test_real_context_file_and_persistence(engine, target, tmp_path, monkeypatch, bound):
    monkeypatch.setattr(settings, "app_mode", "local")
    # 本测试不验证Token计费，模型使用受控响应，仅关闭该无关预算门槛。
    monkeypatch.setattr(settings, "agent_max_total_tokens", None)
    factory = sessionmaker(bind=engine)
    for module in (conversation_repository, run_repository, tool_execution_context, workspace_path):
        monkeypatch.setattr(module, "SessionLocal", factory)
    directory = tmp_path.resolve()
    (directory / "notes.txt").write_text("真实项目内容\n", encoding="utf-8")
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = str(directory) if bound else None
    run_id = run_repository.create_agent_run(user_id=target["user_id"], session_id=target["conversation_id"])
    create = AsyncMock(side_effect=[
        build_tool_response(("file-call", "read_text_file", '{"relative_path":"notes.txt"}')),
        build_text_response("已处理"),
    ])
    monkeypatch.setattr(service, "client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    events = asyncio.run(collect(target["user_id"], target["conversation_id"], run_id))
    assert events[-1]["type"] == "RUN_FINISHED"
    assert any(event["type"] == ("TOOL_CALL_RESULT" if bound else "TOOL_CALL_ERROR") for event in events)
    sent = create.call_args.kwargs
    assert "read_text_file" in {tool["function"]["name"] for tool in sent["tools"]}
    observation = next(message for message in sent["messages"] if message["role"] == "tool")
    content = json.loads(observation["content"])
    if bound:
        assert content == {"relative_path": "notes.txt", "content": "真实项目内容\n", "byte_count": 19}
    else:
        assert content["error"]["details"] == "workspace_directory_unbound"
    assert str(directory) not in observation["content"]
    with Session(engine) as session:
        assert session.get(AgentRun, run_id).status == "done"
        messages = session.scalars(select(Message).where(Message.conversation_id == target["conversation_pk"]).order_by(Message.id)).all()
        assert [(message.role, message.content) for message in messages] == [("user", "read"), ("assistant", "已处理")]
        types = session.scalars(select(AgentRunEvent.event_type).where(AgentRunEvent.run_id == run_id)).all()
        assert ("TOOL_CALL_RESULT" if bound else "TOOL_CALL_ERROR") in types
