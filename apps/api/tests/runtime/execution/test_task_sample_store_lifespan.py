"""应用生命周期装配，不运行模型、数据库检查或资源清理。"""

import asyncio

import pytest
from fastapi import FastAPI

from app import main
from app.services.runtime.execution.task_sample_recovery_store import TaskSampleRecoveryStore
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleJournalUnavailable
from tests.runtime.sandbox.test_sandbox_creation import request


@pytest.mark.parametrize('failure', ['none', 'body', 'broker'])
def test_new_store_per_start_and_shutdown_seals_without_erasing(monkeypatch, failure):
    monkeypatch.setattr(main, 'check_database_ready', lambda: None)
    async def close_broker():
        if failure == 'broker':
            raise RuntimeError('broker failure')
    monkeypatch.setattr(main, 'close_cancellation_broker', close_broker)
    application = FastAPI()
    stores = []
    async def cycle():
        try:
            async with main.lifespan(application):
                store = application.state.task_sample_recovery_store
                stores.append(store)
                assert isinstance(store, TaskSampleRecoveryStore)
                scope = store.acquire(user_id=1, conversation_id='conversation', run_id=10)
                scope.journal.reserve(request())
                if failure == 'body':
                    raise RuntimeError('body failure')
        except RuntimeError:
            assert failure != 'none'
        else:
            assert failure == 'none'
        assert not hasattr(application.state, 'task_sample_recovery_store')
        assert not hasattr(application.state, 'command_recovery_store')
        assert not hasattr(application.state, 'execution_budget')
        # 保留的 Python 引用仍有证据；退出只关闭新登记，没有伪造终态。
        assert scope.journal.records[0].status == 'pending'
        with pytest.raises(TaskSampleJournalUnavailable):
            scope.journal.reserve(request())
    async def scenario():
        await cycle()
        await cycle()
    asyncio.run(scenario())
    assert stores[0] is not stores[1]


def test_readiness_failure_creates_no_store(monkeypatch):
    def fail():
        raise RuntimeError('database not ready')
    monkeypatch.setattr(main, 'check_database_ready', fail)
    application = FastAPI()
    async def scenario():
        with pytest.raises(RuntimeError):
            async with main.lifespan(application):
                pytest.fail('startup must fail')
    asyncio.run(scenario())
    assert not hasattr(application.state, 'task_sample_recovery_store')
