"""本地聊天HTTP→请求工具表→真实PG检索→受控模型回答及发送前拒绝。"""

from copy import deepcopy
import json
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AgentRun, ConversationExecutionSlot, Message, Workspace
from app.services.chat import chat_service
from app.services.model.embedding_config import EmbeddingError
from app.services.runtime.agent import code_search_binding as binding
from app.services.workspace.files import code_batch_summaries, code_query_search, code_vector_search, code_vector_storage
from app.routers.chat import chat_execution
from app.tools.registry import TOOL_REGISTRY
from tests.assertions import require_value
from tests.chat.test_vault_search_chat import api, database, isolated_history_and_monitor, root, target
from tests.model.response_stream import build_text_response, build_tool_response
from tests.model.test_code_embeddings import config
from tests.workspace.files.test_code_query_search import query_response
from tests.workspace.files.test_code_vector_storage import save
from tests.workspace.files.test_vault_api import HEADERS

__all__ = ["api", "database", "isolated_history_and_monitor", "root", "target"]


@pytest.fixture
def code_api(api, engine, target, monkeypatch):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    for module in (code_batch_summaries, code_query_search, code_vector_search, code_vector_storage):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(chat_execution.ChatExecution, "bind_vault_search_tool", lambda self, context: None)
    monkeypatch.setattr(binding, "load_embedding_config", config)
    context = code_vector_storage.capture_code_embedding_target(**{key: target[key] for key in ("user_id", "workspace_id", "task_id")})
    return api, context


@pytest.mark.parametrize("mode", ["match", "empty", "unconfigured", "invalid_config", "extra", "version", "binding-after-result", "budget-first", "budget-after-result"])
def test_chat_request_snapshot_observation_final_guard_and_persistence(code_api, engine, target, monkeypatch, mode):
    api, context = code_api
    if mode not in ("empty", "unconfigured", "invalid_config"):
        save(context)
    if mode in ("unconfigured", "invalid_config"):
        def unavailable():
            raise EmbeddingError("embedding_not_configured" if mode == "unconfigured" else "embedding_config_invalid", "PRIVATE")
        monkeypatch.setattr(binding, "load_embedding_config", unavailable)
    if mode == "budget-first":
        monkeypatch.setattr(binding, "MAX_CODE_MODEL_REQUEST_BYTES", 1)
    embedding_calls, sent, streams = [], [], []
    original_factory = binding.make_search_code_definition

    def embedding(request):
        assert engine.pool.checkedout() == 0
        embedding_calls.append(request)
        return query_response(model="changed" if mode == "version" else "fixture-model-v1")

    monkeypatch.setattr(binding, "make_search_code_definition", lambda **kwargs: original_factory(**kwargs, transport=httpx.MockTransport(embedding)))
    original_record = chat_service.record_run_event

    def record(run_id, event_type, payload):
        result = original_record(run_id, event_type, payload)
        if event_type == "TOOL_CALL_RESULT" and payload.get("tool_name") == "search_code":
            if mode == "binding-after-result":
                with Session(engine) as session, session.begin():
                    require_value(session.scalar(select(Workspace))).binding_revision += 1
            elif mode == "budget-after-result":
                monkeypatch.setattr(binding, "MAX_CODE_MODEL_REQUEST_BYTES", 1)
        return result

    monkeypatch.setattr(chat_service, "record_run_event", record)
    original_preflight = code_query_search._preflight_batch

    def preflight(**kwargs):
        assert get_ident() != sent[0][1]  # 查询SQL在跟踪线程，不阻塞模型事件循环。
        return original_preflight(**kwargs)

    monkeypatch.setattr(code_query_search, "_preflight_batch", preflight)

    async def respond(**kwargs):
        assert engine.pool.checkedout() == 0
        sent.append((deepcopy(kwargs), get_ident()))
        names = {item["function"]["name"] for item in kwargs["tools"]}
        if mode in ("unconfigured", "invalid_config"):
            assert "search_code" not in names
            stream = build_text_response("普通聊天可用")
        elif len(sent) == 1:
            assert "search_code" in names and "search_code" not in TOOL_REGISTRY
            assert "EMBED_PRIVATE" not in json.dumps(kwargs["tools"])
            args = {"query": "查找取消"}
            if mode == "extra":
                args["batch_id"] = "PRIVATE"
            stream = build_tool_response(("code-call", "search_code", json.dumps(args)))
        else:
            message = next(item for item in kwargs["messages"] if item["role"] == "tool")
            payload = json.loads(message["content"])
            if mode == "empty":
                assert payload["status"] == "not_found_in_window"
            elif mode in ("extra", "version"):
                assert payload["type"] == "tool_error"
            else:
                assert payload["matches"] and "space_id" not in message["content"]
            stream = build_text_response("参考 sample.py:1" if mode == "match" else "本次检索结果已说明")
        streams.append(stream)
        return stream

    monkeypatch.setattr(chat_service, "client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=respond)))))
    response = api[0].post("/chat/stream", headers=HEADERS, json={"session_id": target["conversation_id"], "prompt": "查找代码并引用来源"})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    rejected = mode in ("binding-after-result", "budget-first", "budget-after-result")
    assert events[-1]["type"] == ("RUN_ERROR" if rejected else "RUN_FINISHED")
    assert sum(event["type"] in ("RUN_ERROR", "RUN_ABORTED", "RUN_FINISHED") for event in events) == 1
    expected_calls = 0 if mode == "budget-first" else 1 if rejected or mode in ("unconfigured", "invalid_config") else 2
    assert len(sent) == expected_calls and all(stream.closed for stream in streams)
    assert len(embedding_calls) == (0 if mode in ("empty", "unconfigured", "invalid_config", "extra", "budget-first") else 1)
    with Session(engine) as session:
        messages = session.scalars(select(Message).where(Message.conversation_id == target["conversation_pk"])).all()
        assert len(messages) == (0 if rejected else 2)
        assert require_value(session.scalar(select(AgentRun))).status == ("error" if rejected else "done")
        assert session.scalar(select(ConversationExecutionSlot)) is None
    assert api[1].state.execution_budget.in_use == 0


def test_bound_async_tool_cancellation_closes_embedding_and_drains_threads(code_api, monkeypatch):
    import asyncio
    from app.services.runtime.execution.execution_threads import ExecutionThreads
    from app.tools.context import ToolExecutionContext
    from tests.model.test_code_embeddings import TrackedStream

    _, context = code_api
    save(context)
    stream = TrackedStream(b"{", delay=20)
    original_factory = binding.make_search_code_definition
    monkeypatch.setattr(binding, "make_search_code_definition", lambda **kwargs: original_factory(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, stream=stream)),
    ))
    current = binding.load_tool_execution_context(user_id=context.user_id, conversation_id="c" * 32)
    assert isinstance(current, ToolExecutionContext)

    async def run():
        threads = ExecutionThreads()
        try:
            bound = require_value(binding.bind_code_search(context=current, threads=threads, is_closed=lambda: False))
            arguments = bound.definition.validate_arguments('{"query":"取消"}')
            task = asyncio.create_task(bound.definition.execute_async(arguments, context=current))
            await asyncio.wait_for(stream.started.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await threads.wait_closed()
        assert stream.closed

    asyncio.run(run())
