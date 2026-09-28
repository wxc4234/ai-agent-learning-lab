"""验证记录的应用所有权、容量、作用域及生命周期封闭。"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app import main
from app.services.runtime.execution import verification_recovery_store as service
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleJournalUnavailable
from app.services.runtime.verification.contracts import build_verification_command
from app.tools.context import ToolExecutionContext

CONTEXT = ToolExecutionContext(1, 'c', 'w', 't')


def test_closed_scope_retains_pending_and_accepts_one_finish():
    store = service.VerificationRecoveryStore()
    scope = store.acquire(context=CONTEXT, run_id=1)
    index = scope.journal.reserve(build_verification_command('sample_unittest_v1'))
    store.close_scope(scope)
    assert store.get(context=CONTEXT, run_id=1) is scope
    assert store.acquire(context=CONTEXT, run_id=1) is scope
    scope.journal.finish(index, status='cancelled')
    assert scope.journal.records[0].status == 'cancelled'
    with pytest.raises(TaskSampleJournalUnavailable):
        scope.journal.reserve(build_verification_command('sample_unittest_v1'))
    with pytest.raises(TaskSampleJournalUnavailable):
        store.close_scope(replace(scope))
    store.close()
    assert store.get(context=CONTEXT, run_id=1) is scope
    with pytest.raises(TaskSampleJournalUnavailable):
        store.acquire(context=CONTEXT, run_id=1)


@pytest.mark.parametrize('field,value', [('user_id', 2), ('conversation_id', 'other'), ('workspace_id', 'other'), ('task_id', 'other')])
def test_run_scope_cannot_be_rebound(field, value):
    store = service.VerificationRecoveryStore()
    store.acquire(context=CONTEXT, run_id=1)
    changed = replace(CONTEXT, **{field: value})
    assert store.get(context=changed, run_id=1) is None
    with pytest.raises(TaskSampleJournalUnavailable):
        store.acquire(context=changed, run_id=1)


def test_capacity_is_not_evicted(monkeypatch):
    monkeypatch.setattr(service, 'MAX_VERIFICATION_RECOVERY_SCOPES', 1)
    store = service.VerificationRecoveryStore()
    scope = store.acquire(context=CONTEXT, run_id=1)
    store.close_scope(scope)
    with pytest.raises(TaskSampleJournalUnavailable):
        store.acquire(context=CONTEXT, run_id=2)
    assert store.get(context=CONTEXT, run_id=1) is scope


@pytest.mark.parametrize('run_id', [0, -1, True, '1'])
def test_invalid_run(run_id):
    with pytest.raises(TaskSampleJournalUnavailable):
        service.VerificationRecoveryStore().acquire(context=CONTEXT, run_id=run_id)


def test_fork_does_not_recover_ownership(monkeypatch):
    store = service.VerificationRecoveryStore()
    monkeypatch.setattr(service.os, 'getpid', lambda: store._pid + 1)
    with pytest.raises(TaskSampleJournalUnavailable):
        store.acquire(context=CONTEXT, run_id=1)
    with pytest.raises(TaskSampleJournalUnavailable):
        store.close()


def test_lifespan_closes_and_replaces_store(monkeypatch):
    monkeypatch.setattr(main, 'check_database_ready', lambda: None)
    monkeypatch.setattr(main, 'close_cancellation_broker', AsyncMock())
    monkeypatch.setattr(main, 'start_git_samples', lambda app: None)
    monkeypatch.setattr(main, 'stop_git_samples', AsyncMock())
    app = FastAPI()
    stores = []
    async def scenario():
        for _ in range(2):
            with pytest.raises(RuntimeError, match='injected'):
                async with main.lifespan(app):
                    store = app.state.verification_recovery_store
                    stores.append(store)
                    store.acquire(context=CONTEXT, run_id=1)
                    raise RuntimeError('injected')
            assert not hasattr(app.state, 'verification_recovery_store')
            with pytest.raises(TaskSampleJournalUnavailable):
                store.acquire(context=CONTEXT, run_id=2)
        assert stores[0] is not stores[1]
    asyncio.run(scenario())
