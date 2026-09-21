"""请求关闭后保留恢复证据；应用级存储有界且检查归属。"""

import asyncio
from dataclasses import replace

import pytest
from fastapi import FastAPI

from app import main
from app.routers.chat import chat_execution as chat
from app.schemas import ChatRequest
from app.services.runtime.execution.command_recovery_store import CommandRecoveryStore
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.runtime.sandbox.command_recovery_journal import CommandRecoveryJournalUnavailable
from app.services.runtime.command.command_contracts import CommandRequest
from app.tools.run_command import CommandToolExecutionError


def acquire(store, run_id=1, **kwargs):
    return store.acquire(**({"user_id": 1, "session_id": "session", "run_id": run_id} | kwargs))


@pytest.mark.parametrize("status", ["empty", "completed", "pending", "unconfirmed", "cancelled"])
def test_close_retains_only_unresolved_records(status):
    store = CommandRecoveryStore()
    scope = acquire(store)
    if status != "empty":
        index = scope.journal.reserve(CommandRequest(argv=["/bin/true"]))
        if status != "pending":
            scope.journal.finish(index, status=status)
    store.close_scope(scope)
    found = store.get(user_id=1, session_id="session", run_id=1)
    assert found is (None if status in ("empty", "completed") else scope)
    with pytest.raises(CommandRecoveryJournalUnavailable):
        scope.journal.reserve(CommandRequest(argv=["/bin/true"]))


@pytest.mark.parametrize("field,value", [("user_id", 2), ("session_id", "other")])
def test_wrong_owner_cannot_read_or_reacquire(field, value):
    store = CommandRecoveryStore()
    scope = acquire(store)
    args = {"user_id": 1, "session_id": "session", "run_id": 1, field: value}
    assert store.get(**args) is None
    with pytest.raises(ValueError):
        store.acquire(**args)
    assert acquire(store) is scope


@pytest.mark.parametrize("field,value", [("user_id", True), ("user_id", 0), ("run_id", False),
                                        ("run_id", -1), ("session_id", ""), ("session_id", None)])
def test_invalid_identity_rejected(field, value):
    with pytest.raises(ValueError):
        acquire(CommandRecoveryStore(), **{field: value})


def test_capacity_reuse_release_and_foreign_scope():
    store = CommandRecoveryStore()
    scopes = [acquire(store, run_id=i) for i in range(1, 33)]
    assert acquire(store) is scopes[0]
    with pytest.raises(CommandRecoveryJournalUnavailable):
        acquire(store, run_id=33)
    with pytest.raises(ValueError):
        store.close_scope(replace(scopes[0]))
    with pytest.raises(ValueError):
        CommandRecoveryStore().close_scope(scopes[0])
    store.close_scope(scopes[0])
    assert acquire(store, run_id=33).run_id == 33
    assert store.get(user_id=1, session_id="session", run_id=100) is None


@pytest.mark.parametrize("outcome", ["completed", "unconfirmed", "cancelled"])
def test_request_close_preserves_or_releases_application_owned_scope(monkeypatch, outcome):
    async def scenario():
        store = CommandRecoveryStore()
        execution = chat.ChatExecution(user_id=1, body=ChatRequest(session_id="session", prompt="hello"),
                                       threads=ExecutionThreads(), command_recovery_store=store)
        async def created():
            return 1
        execution.creation = asyncio.create_task(created())
        await execution.creation
        calls = []
        async def command(*, argv, working_directory, recovery_journal):
            calls.append(recovery_journal)
            index = recovery_journal.reserve(CommandRequest(argv=argv, working_directory=working_directory))
            recovery_journal.finish(index, status=outcome)
            return "result"
        monkeypatch.setattr(chat, "run_command", command)
        monkeypatch.setattr(chat, "finish_agent_run", lambda *args: None)
        assert await execution.execute_command(argv=["/bin/true"]) == "result"
        assert await execution.execute_command(argv=["/bin/true"]) == "result"
        scope = execution.command_scope
        assert calls == [scope.journal, scope.journal]
        await execution.close()
        with pytest.raises(CommandToolExecutionError):
            await execution.execute_command(argv=["/bin/true"])
        del execution
        found = store.get(user_id=1, session_id="session", run_id=1)
        assert found is (None if outcome == "completed" else scope)
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["missing_store", "missing_run", "pending_run", "cancelled_run", "failed_run", "full"])
def test_request_preconditions_block_command(monkeypatch, kind):
    async def scenario():
        store = CommandRecoveryStore()
        execution = chat.ChatExecution(user_id=1, body=ChatRequest(session_id="session", prompt="hello"),
                                       threads=ExecutionThreads(), command_recovery_store=store)
        async def created():
            if kind == "pending_run":
                await asyncio.Future()
            if kind == "failed_run":
                raise ValueError("PRIVATE")
            return 100
        if kind != "missing_run":
            execution.creation = asyncio.create_task(created())
            if kind == "cancelled_run":
                execution.creation.cancel()
            if kind != "pending_run":
                await asyncio.gather(execution.creation, return_exceptions=True)
        if kind == "missing_store":
            execution.command_recovery_store = None
        if kind == "full":
            for i in range(1, 33):
                acquire(store, run_id=i)
        async def forbidden(**kwargs):
            pytest.fail("command must not start")
        monkeypatch.setattr(chat, "run_command", forbidden)
        try:
            with pytest.raises(CommandToolExecutionError) as caught:
                await execution.execute_command(argv=["/bin/true"])
            assert caught.value.code == "command_recovery_unavailable"
        finally:
            if execution.creation is not None:
                execution.creation.cancel()
                await asyncio.gather(execution.creation, return_exceptions=True)
            await execution.threads.wait_closed()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["none", "body", "broker", "database"])
def test_application_store_lifecycle(monkeypatch, failure):
    application = FastAPI()
    stores = []
    def check():
        if failure == "database":
            raise RuntimeError("database")
    async def close():
        if failure == "broker":
            raise RuntimeError("broker")
    monkeypatch.setattr(main, "check_database_ready", check)
    monkeypatch.setattr(main, "close_cancellation_broker", close)
    async def scenario():
        for _ in range(2):
            try:
                async with main.lifespan(application):
                    stores.append(application.state.command_recovery_store)
                    assert isinstance(stores[-1], CommandRecoveryStore)
                    if failure == "body":
                        raise RuntimeError("body")
            except RuntimeError:
                assert failure != "none"
            assert not hasattr(application.state, "command_recovery_store")
        if failure != "database":
            assert stores[0] is not stores[1]
    asyncio.run(scenario())


@pytest.mark.parametrize("configured", [True, False])
def test_dependency_injects_application_store_or_refuses_missing(monkeypatch, configured):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from starlette.requests import Request
    from app.services.runtime.execution.execution_budget import ExecutionBudget

    application = FastAPI()
    application.state.execution_budget = ExecutionBudget(capacity=1)
    if configured:
        application.state.command_recovery_store = CommandRecoveryStore()
    monkeypatch.setattr(chat.settings, "app_mode", "local")
    monkeypatch.setattr(chat, "_check_local_conversation", lambda **kwargs: None)
    entered = []
    @asynccontextmanager
    async def scope(**kwargs):
        entered.append(True)
        threads = ExecutionThreads()
        try:
            yield threads
        finally:
            await threads.wait_closed()
    monkeypatch.setattr(chat, "conversation_execution", scope)
    async def scenario():
        dependency = chat.require_chat_execution(
            request=Request({"type": "http", "app": application}),
            body=ChatRequest(session_id="session", prompt="hello"),
            current_user=SimpleNamespace(id=1),
        )
        try:
            if configured:
                execution = await anext(dependency)
                assert execution.command_recovery_store is application.state.command_recovery_store
                assert execution.command_scope is None
            else:
                with pytest.raises(RuntimeError, match="命令恢复存储"):
                    await anext(dependency)
                assert not entered
        finally:
            await dependency.aclose()
        assert application.state.execution_budget.in_use == 0
    asyncio.run(scenario())
