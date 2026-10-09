"""请求级能力隔离、发送预算、关闭和取消；服务授权使用受控依赖。"""

import asyncio
from dataclasses import replace
import json
from threading import Event, get_ident

from openai.types.chat import ChatCompletionMessageParam

import pytest

from app.services.model.model_decision import ModelDecisionError
from app.services.runtime.agent import code_search_binding as service
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.tools.errors import SafeToolExecutionError
from tests.assertions import require_value
from tests.model.test_code_embeddings import config
from tests.tools.test_search_code_tool import CONTEXT


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(service, "load_embedding_config", config)
    monkeypatch.setattr(service, "load_tool_execution_context", lambda **kwargs: CONTEXT)


def test_exact_budget_and_one_byte_over(setup, monkeypatch):
    async def run():
        threads = ExecutionThreads()
        try:
            binding = require_value(service.bind_code_search(context=CONTEXT, threads=threads, is_closed=lambda: False))
            messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": "中"}]
            size = len(json.dumps({"messages": messages, "tools": []}, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())
            monkeypatch.setattr(service, "MAX_CODE_MODEL_REQUEST_BYTES", size)
            await binding.before_send(messages, [])
            monkeypatch.setattr(service, "MAX_CODE_MODEL_REQUEST_BYTES", size - 1)
            with pytest.raises(ModelDecisionError) as caught:
                await binding.before_send(messages, [])
            assert caught.value.reason == "model_request_budget_exceeded"
        finally:
            await threads.wait_closed()
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["closed", "foreign-context", "changed-context", "db-error"])
def test_request_context_closed_and_unknown_fail_closed(setup, monkeypatch, kind):
    async def run():
        threads = ExecutionThreads()
        closed = False
        binding = require_value(service.bind_code_search(context=CONTEXT, threads=threads, is_closed=lambda: closed))
        try:
            if kind == "closed":
                closed = True
            if kind == "changed-context":
                monkeypatch.setattr(service, "load_tool_execution_context", lambda **kwargs: replace(CONTEXT, task_id="f" * 32))
            if kind == "db-error":
                def fail(**kwargs):
                    raise RuntimeError("PRIVATE SQL")
                monkeypatch.setattr(service, "load_tool_execution_context", fail)
            with pytest.raises(SafeToolExecutionError) as caught:
                request_context = replace(CONTEXT) if kind == "foreign-context" else CONTEXT
                await require_value(binding.definition.async_executor)(context=request_context, query="query")
            assert caught.value.code == "workspace_not_accessible" and "PRIVATE" not in str(caught.value)
        finally:
            await threads.wait_closed()
    asyncio.run(run())


def test_cancel_guard_retains_running_sync_work_until_cleanup(setup, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    main_thread = get_ident()

    def block(**kwargs):
        assert get_ident() != main_thread
        started.set()
        assert release.wait(5)
        finished.set()
        return CONTEXT

    monkeypatch.setattr(service, "load_tool_execution_context", block)

    async def run():
        threads = ExecutionThreads()
        try:
            binding = require_value(service.bind_code_search(context=CONTEXT, threads=threads, is_closed=lambda: False))
            task = asyncio.ensure_future(binding.before_send([], []))
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not finished.is_set()
        finally:
            release.set()
            await threads.wait_closed()
        assert finished.is_set()
    asyncio.run(run())
