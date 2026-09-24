"""应用拥有者、作用域归属与真实请求记录组合，不访问 Docker/数据库。"""

import asyncio
from dataclasses import replace

import pytest

from app.services.runtime.execution import task_sample_recovery_store as stores
from app.services.runtime.execution import scoped_task_sample_command as service
from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandUnconfirmed, SampleCommandCancelled
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleJournalUnavailable
from tests.runtime.sandbox.test_sandbox_creation import request
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414
from tests.runtime.sandbox.test_sandbox_sample_command import lab as lab  # noqa: PLC0414
from tests.runtime.sandbox.test_task_sample_command import factory as factory  # noqa: PLC0414

SCOPE = {'user_id': 1, 'conversation_id': 'conversation', 'run_id': 10}


def execute(store, **changes):
    return service.run_scoped_task_sample_command(
        request=request(), bindings=None, store=store, **(SCOPE | changes),
    )


def test_same_run_reuses_and_closed_scope_never_reopens():
    store = stores.TaskSampleRecoveryStore()
    scope = store.acquire(**SCOPE)
    assert store.acquire(**SCOPE) is scope
    index = scope.journal.reserve(request())
    store.close_scope(scope)
    store.close_scope(scope)
    scope.journal.finish(index, status='unconfirmed')
    assert store.get(**SCOPE) is scope and store.acquire(**SCOPE) is scope
    with pytest.raises(TaskSampleJournalUnavailable):
        scope.journal.reserve(request())
    assert scope.journal.records[0].status == 'unconfirmed'


@pytest.mark.parametrize('changes', [{'user_id': 2}, {'conversation_id': 'other'}])
def test_wrong_owner_cannot_acquire_or_read(changes):
    store = stores.TaskSampleRecoveryStore()
    scope = store.acquire(**SCOPE)
    assert store.get(**(SCOPE | changes)) is None
    with pytest.raises(TaskSampleJournalUnavailable):
        store.acquire(**(SCOPE | changes))
    assert store.get(**SCOPE) is scope


@pytest.mark.parametrize('field,value', [('user_id', True), ('user_id', 0), ('run_id', True), ('run_id', -1), ('conversation_id', ''), ('conversation_id', None)])
def test_invalid_identity_never_allocates(field, value):
    store = stores.TaskSampleRecoveryStore()
    for action in (store.acquire, store.get):
        with pytest.raises(TaskSampleJournalUnavailable):
            action(**(SCOPE | {field: value}))
    assert store.get(**SCOPE) is None


def test_unknown_read_does_not_allocate_and_capacity_does_not_evict(monkeypatch):
    store = stores.TaskSampleRecoveryStore()
    monkeypatch.setattr(stores, 'MAX_TASK_SAMPLE_RECOVERY_SCOPES', 1)
    assert store.get(**SCOPE) is None
    scope = store.acquire(**SCOPE)
    store.close_scope(scope)
    with pytest.raises(TaskSampleJournalUnavailable):
        store.acquire(**(SCOPE | {'run_id': 11}))
    assert store.get(**SCOPE) is scope


def test_forged_or_foreign_scope_cannot_close_original():
    store = stores.TaskSampleRecoveryStore()
    scope = store.acquire(**SCOPE)
    for invalid in (replace(scope), stores.TaskSampleRecoveryStore().acquire(**SCOPE), None):
        with pytest.raises(TaskSampleJournalUnavailable):
            store.close_scope(invalid)
    assert scope.journal.reserve(request()) == 0


def test_store_close_preserves_records_and_allows_pending_finish():
    store = stores.TaskSampleRecoveryStore()
    scope = store.acquire(**SCOPE)
    index = scope.journal.reserve(request())
    store.close()
    store.close()
    with pytest.raises(TaskSampleJournalUnavailable):
        store.acquire(**SCOPE)
    with pytest.raises(TaskSampleJournalUnavailable):
        scope.journal.reserve(request())
    scope.journal.finish(index, status='cancelled')
    assert store.get(**SCOPE).journal.records[0].status == 'cancelled'


def test_forked_store_rejected():
    store = stores.TaskSampleRecoveryStore()
    scope = store.acquire(**SCOPE)
    store._pid += 1
    for action in (lambda: store.acquire(**SCOPE), lambda: store.get(**SCOPE), lambda: store.close_scope(scope), store.close):
        with pytest.raises(TaskSampleJournalUnavailable):
            action()


@pytest.mark.parametrize('kind', ['full', 'closed', 'wrong_owner', 'closed_request'])
def test_execution_gate_precedes_snapshot(lab, factory, monkeypatch, kind):
    store = stores.TaskSampleRecoveryStore()
    scope = store.acquire(**SCOPE)
    changes = {}
    if kind == 'full':
        monkeypatch.setattr(stores, 'MAX_TASK_SAMPLE_RECOVERY_SCOPES', 1)
        changes['run_id'] = 11
    elif kind == 'closed':
        store.close()
    elif kind == 'closed_request':
        store.close_scope(scope)
    else:
        changes['user_id'] = 2
    with pytest.raises(TaskSampleJournalUnavailable):
        asyncio.run(execute(store, **changes))
    assert lab.calls == [] and lab.samples == []


@pytest.mark.parametrize('kind', ['success', 'nonzero', 'failure', 'cancel'])
def test_request_exit_retains_complete_evidence(lab, factory, kind):
    store = stores.TaskSampleRecoveryStore()
    if kind == 'nonzero':
        lab.code = 7
    if kind in ('failure', 'cancel'):
        lab.failure = 'execute'
        lab.error = asyncio.CancelledError() if kind == 'cancel' else OSError('failure')
    async def scenario():
        if kind in ('failure', 'cancel'):
            error_type = SampleCommandCancelled if kind == 'cancel' else SampleCommandUnconfirmed
            with pytest.raises(error_type), store.request_scope(**SCOPE):
                await execute(store)
        else:
            with store.request_scope(**SCOPE):
                await execute(store)
    asyncio.run(scenario())
    saved = store.get(**SCOPE).journal.records[0]
    assert saved.status == {'success': 'completed', 'nonzero': 'completed', 'failure': 'unconfirmed', 'cancel': 'cancelled'}[kind]
    if kind in ('failure', 'cancel'):
        assert saved.recovery.sample is lab.samples[0] and saved.recovery.sample.root.exists()
    with pytest.raises(TaskSampleJournalUnavailable):
        store.get(**SCOPE).journal.reserve(request())


def test_request_close_while_inflight_allows_terminal(lab, factory, monkeypatch):
    store = stores.TaskSampleRecoveryStore()
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        # 暂停在 create 边界：此时实际 journal 已预留且样例已生成。
        import app.services.runtime.sandbox.sandbox_sample_command as owner
        real_create = owner.create_sandbox_container
        async def delayed(**kwargs):
            entered.set()
            await release.wait()
            return await real_create(**kwargs)
        monkeypatch.setattr(owner, 'create_sandbox_container', delayed)
        with store.request_scope(**SCOPE) as scope:
            task = asyncio.create_task(execute(store))
            await entered.wait()
            assert scope.journal.records[0].status == 'pending'
        assert store.get(**SCOPE) is scope
        release.set()
        assert (await task).sample_cleaned
        assert scope.journal.records[0].status == 'completed'
    asyncio.run(scenario())
