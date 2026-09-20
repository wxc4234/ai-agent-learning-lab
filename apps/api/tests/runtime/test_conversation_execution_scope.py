"""独立 PostgreSQL 与真实线程验证作用域的获取、取消和释放边界。"""

import asyncio
from threading import Event

import anyio
import pytest
from sqlalchemy.orm import sessionmaker

from app.services.runtime import conversation_execution_scope as scope
from app.services.runtime.conversation_execution_service import ConversationBusyError
from tests.runtime import test_conversation_execution_service as service_tests
from tests.runtime.test_conversation_execution_service import tokens
from tests.runtime.test_execution_threads import ControlledWork, checkpoint


# 注册既有本地任务夹具，仍使用公共隔离 PostgreSQL 引擎。
scope_target = service_tests.target


@pytest.fixture
def execution(engine, scope_target, monkeypatch):
    monkeypatch.setattr(scope, "SessionLocal", sessionmaker(bind=engine))
    return lambda: scope.conversation_execution(user_id=scope_target[0], session_id=scope_target[1])


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_body_outcome_releases_before_propagation(engine, execution, outcome):
    async def scenario():
        async def body():
            async with execution() as threads:
                assert len(tokens(engine)) == 1
                assert await threads.run(lambda: 42) == 42
                if outcome == "error":
                    raise ValueError("body failure")
                if outcome == "cancel":
                    raise asyncio.CancelledError
                return 42

        if outcome == "success":
            assert await body() == 42
        else:
            error = ValueError if outcome == "error" else asyncio.CancelledError
            with pytest.raises(error):
                await body()
        assert tokens(engine) == []

    asyncio.run(scenario())


def test_busy_does_not_enter_or_release_original(engine, execution):
    async def scenario():
        async with execution():
            original = tokens(engine)
            with pytest.raises(ConversationBusyError):
                async with execution():
                    pytest.fail("busy body must not run")
            assert tokens(engine) == original
        assert tokens(engine) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["before", "after"])
def test_cancel_during_acquisition_cleans_late_success(engine, execution, monkeypatch, stage):
    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        allow = Event()
        real = scope._acquire

        def paused(**kwargs):
            result = real(**kwargs) if stage == "after" else None
            loop.call_soon_threadsafe(started.set)
            if not allow.wait(5):
                raise AssertionError("acquisition not released")
            return result if stage == "after" else real(**kwargs)

        monkeypatch.setattr(scope, "_acquire", paused)

        async def body():
            async with execution():
                pytest.fail("cancelled acquisition must not enter body")

        task = asyncio.create_task(body())
        try:
            await asyncio.wait_for(started.wait(), 2)
            for _ in range(3):
                task.cancel()
                await checkpoint()
                assert not task.done()
            allow.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert tokens(engine) == []
        finally:
            allow.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("cancel_kind", ["asyncio", "anyio"])
def test_worker_must_finish_before_release(engine, execution, failure, cancel_kind):
    async def scenario():
        work = ControlledWork()
        entered = asyncio.Event()
        cancel_scopes = []

        async def body():
            with anyio.CancelScope() as cancel_scope:
                cancel_scopes.append(cancel_scope)
                async with execution() as threads:
                    entered.set()
                    await threads.run(work, failure)

        task = asyncio.create_task(body())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(work.started.wait(), 2)
            original = tokens(engine)
            assert len(original) == 1
            for _ in range(3):
                if cancel_kind == "asyncio":
                    task.cancel()
                else:
                    cancel_scopes[0].cancel()
                await checkpoint()
                assert not task.done()
                assert tokens(engine) == original
            work.release.set()
            if cancel_kind == "asyncio":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            else:
                await asyncio.wait_for(task, 3)
                assert cancel_scopes[0].cancelled_caught
            assert work.finished.is_set()
            assert tokens(engine) == []
        finally:
            work.release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_repeated_cancel_during_release_waits_for_commit(engine, execution, monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        allow = Event()
        real = scope._release

        def paused(**kwargs):
            loop.call_soon_threadsafe(started.set)
            if not allow.wait(5):
                raise AssertionError("release not resumed")
            real(**kwargs)

        monkeypatch.setattr(scope, "_release", paused)

        async def body():
            async with execution():
                pass

        task = asyncio.create_task(body())
        try:
            await asyncio.wait_for(started.wait(), 2)
            for _ in range(3):
                task.cancel()
                await checkpoint()
                assert not task.done()
                assert len(tokens(engine)) == 1
            allow.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert tokens(engine) == []
        finally:
            allow.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["acquire", "release"])
@pytest.mark.parametrize("committed", [False, True])
def test_database_confirmation_failure_preserves_actual_fact(
    engine, execution, monkeypatch, operation, committed,
):
    real = getattr(scope, "_" + operation)
    error = ConnectionError("simulated confirmation failure")

    def fail(**kwargs):
        # committed=True 在真实数据库事务完成后模拟确认丢失。
        if committed:
            real(**kwargs)
        raise error

    monkeypatch.setattr(scope, "_" + operation, fail)

    async def scenario():
        with pytest.raises(ConnectionError) as caught:
            async with execution():
                assert operation == "release"
        assert caught.value is error
        expected_slot = committed if operation == "acquire" else not committed
        assert bool(tokens(engine)) is expected_slot

    asyncio.run(scenario())


def test_release_does_not_delete_replacement_owner(engine, execution, scope_target):
    from app.services.runtime.conversation_execution_service import (
        acquire_conversation_execution,
        release_conversation_execution,
    )

    async def scenario():
        with pytest.raises(RuntimeError, match="持有者"):
            async with execution():
                with scope.SessionLocal() as session:
                    release_conversation_execution(
                        session, user_id=scope_target[0], session_id=scope_target[1],
                        owner_token=tokens(engine)[0],
                    )
                    replacement = acquire_conversation_execution(
                        session, user_id=scope_target[0], session_id=scope_target[1],
                    )
        assert tokens(engine) == [replacement.owner_token]

    asyncio.run(scenario())


def test_unauthorized_scope_never_enters(engine, execution, scope_target):
    from app.repositories.chat.conversation_repository import ConversationNotAccessibleError

    async def scenario():
        async with execution():
            original = tokens(engine)
            with pytest.raises(ConversationNotAccessibleError):
                async with scope.conversation_execution(user_id=scope_target[2], session_id=scope_target[1]):
                    pytest.fail("unauthorized body must not run")
            assert tokens(engine) == original
        assert tokens(engine) == []

    asyncio.run(scenario())
