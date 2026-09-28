"""Task diff的真实授权、只读采集与跨操作借用边界。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import task_git_samples as service
from app.services.workspace.git.diff_capture import GitDiffCaptureError
from tests.assertions import require_value
from tests.workspace.git.test_diff_capture import baseline
from tests.workspace.git.test_status_capture import git, snapshot
from tests.workspace.git.test_task_git_samples import sample, setup, target

# 显式导入共享隔离数据库夹具，避免重复建立另一套数据库边界。
__all__ = ['setup', 'target']


def test_real_diff_scopes_close_transaction_and_preserve_source(setup, monkeypatch):
    manager, ids, sessions = setup
    manager.bind(**ids)
    handle = sample(manager, ids)
    baseline(handle)
    file = handle.root / 'value.txt'
    file.write_bytes(b'value = 2\n')
    git(handle, 'add', 'value.txt')
    file.write_bytes(b'value = 3\n')
    before = snapshot(handle.root)
    collect = service.collect_sample_git_diff

    def checked(handle, *, scope):
        assert all(s.closed and not s.in_transaction() for s in sessions)
        return collect(handle, scope=scope)

    monkeypatch.setattr(service, 'collect_sample_git_diff', checked)
    worktree = manager.read_diff(**ids, scope='worktree')
    staged = manager.read_diff(**ids, scope='staged')
    assert worktree.scope == 'worktree'
    assert b'-value = 2\n+value = 3\n' in worktree.data
    assert staged.scope == 'staged'
    assert b'-value = 1\n+value = 2\n' in staged.data
    assert snapshot(handle.root) == before
    manager.close(**ids)
    assert not handle.root.exists()


@pytest.mark.parametrize('comparison', ['worktree', 'staged'])
def test_empty_success(setup, comparison):
    manager, ids, _ = setup
    manager.bind(**ids)
    baseline(sample(manager, ids))
    assert manager.read_diff(**ids, scope=comparison).data == b''


@pytest.mark.parametrize('kind', ['unbound', 'foreign', 'task', 'workspace', 'new-manager', 'closed', 'stopped'])
def test_unavailable_never_collects(setup, target, monkeypatch, kind):
    manager, ids, _ = setup
    if kind != 'unbound':
        manager.bind(**ids)
    wrong = dict(ids)
    if kind == 'foreign':
        wrong['user_id'] = target['other_id']
    elif kind in ('task', 'workspace'):
        wrong[kind + '_id'] = 'f' * 32
    elif kind == 'new-manager':
        manager = service.TaskGitSamples()
    elif kind == 'closed':
        manager.close(**ids)
    elif kind == 'stopped':
        manager.stop_accepting()
    monkeypatch.setattr(service, 'collect_sample_git_diff', lambda *a, **k: pytest.fail('must not collect'))
    with pytest.raises(service.TaskGitSampleError):
        manager.read_diff(**wrong, scope='worktree')


@pytest.mark.parametrize('kind', ['workspace-owner', 'conversation-owner', 'deleted', 'recreated'])
def test_reauthorize_and_release_on_failure(setup, engine, target, monkeypatch, kind):
    manager, ids, _ = setup
    manager.bind(**ids)
    with Session(engine) as session, session.begin():
        conversation = require_value(session.get(Conversation, target['conversation_pk']))
        if kind == 'workspace-owner':
            require_value(session.scalar(select(Workspace))).user_id = target['other_id']
        elif kind == 'conversation-owner':
            conversation.user_id = target['other_id']
        else:
            session.delete(conversation)
            if kind == 'recreated':
                session.flush()
                session.add(Conversation(
                    external_id=target['conversation_id'], user_id=target['user_id'],
                    task_id=target['task_pk'],
                ))
    monkeypatch.setattr(service, 'collect_sample_git_diff', lambda *a, **k: pytest.fail('must not collect'))
    expected = service.TaskGitSampleError if kind == 'recreated' else WorkspaceNotAccessibleError
    with pytest.raises(expected):
        manager.read_diff(**ids, scope='worktree')
    root = sample(manager, ids).root
    manager.shutdown()
    assert not root.exists()


@pytest.mark.parametrize('first', ['status', 'diff'])
def test_status_diff_and_close_share_borrow(setup, monkeypatch, first):
    manager, ids, _ = setup
    manager.bind(**ids)
    root = sample(manager, ids).root
    entered, release = Event(), Event()
    name = 'collect_sample_git_' + first
    original = getattr(service, name)

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, name, held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.read_status, **ids) if first == 'status' else pool.submit(
            manager.read_diff, **ids, scope='worktree',
        )
        try:
            assert entered.wait(5)
            for action in (
                lambda: manager.read_status(**ids),
                lambda: manager.read_diff(**ids, scope='staged'),
                lambda: manager.close(**ids),
                manager.shutdown,
            ):
                with pytest.raises(service.TaskGitSampleError):
                    action()
            assert root.exists()
            manager.stop_accepting()
            assert root.exists()
        finally:
            release.set()
        future.result(timeout=5)
    manager.shutdown()
    assert not root.exists()


@pytest.mark.parametrize('error', [
    GitDiffCaptureError('git_diff_timeout'),
    GitDiffCaptureError('git_diff_output_limit'),
    GitDiffCaptureError('git_diff_command_failed'),
    KeyboardInterrupt(),
])
def test_capture_failure_is_not_retried_and_releases_borrow(setup, monkeypatch, error):
    manager, ids, _ = setup
    manager.bind(**ids)
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise error

    monkeypatch.setattr(service, 'collect_sample_git_diff', fail)
    with pytest.raises(type(error)) as caught:
        manager.read_diff(**ids, scope='worktree')
    assert caught.value is error and len(calls) == 1
    manager.close(**ids)


@pytest.mark.parametrize('comparison', ['HEAD', '--cached', '', None, []])
def test_invalid_scope_fails_and_releases_borrow(setup, comparison):
    manager, ids, _ = setup
    manager.bind(**ids)
    with pytest.raises(GitDiffCaptureError) as caught:
        manager.read_diff(**ids, scope=comparison)
    assert caught.value.code == 'git_diff_invalid_scope'
    manager.close(**ids)


@pytest.mark.parametrize('kind', ['missing-head', 'expired'])
def test_real_capture_failure_never_falls_back(setup, kind):
    manager, ids, _ = setup
    manager.bind(**ids)
    if kind == 'expired':
        manager._bindings[tuple(ids.values())].lifetime.__exit__(None, None, None)
    with pytest.raises(GitDiffCaptureError) as caught:
        manager.read_diff(**ids, scope='staged')
    assert caught.value.code == ('git_sample_unavailable' if kind == 'expired' else 'git_diff_command_failed')
    manager.close(**ids)


def test_sealed_cleanup_rejects_diff(setup, monkeypatch):
    manager, ids, _ = setup
    manager.bind(**ids)
    binding = manager._bindings[tuple(ids.values())]
    lifetime = binding.lifetime

    class FailedCleanup:
        def __exit__(self, *args):
            raise OSError('injected cleanup failure')

    binding.lifetime = FailedCleanup()
    try:
        with pytest.raises(OSError):
            manager.close(**ids)
        monkeypatch.setattr(service, 'collect_sample_git_diff', lambda *a, **k: pytest.fail('must not collect'))
        with pytest.raises(service.TaskGitSampleError):
            manager.read_diff(**ids, scope='worktree')
    finally:
        # 只撤销测试注入来回收自有资源，生产服务没有解除sealed的入口。
        binding.lifetime = lifetime
        binding.sealed = False
