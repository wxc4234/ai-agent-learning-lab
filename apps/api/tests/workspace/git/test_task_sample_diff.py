"""真实隔离数据库、应用服务和Git；Docker闭环入口在scripts中单独执行。"""

from dataclasses import replace
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Workspace, WorkspaceSampleOrigin
from tests.assertions import require_value

from app.services.runtime.agent import tool_execution_context as contexts
from app.services.workspace.git import task_sample_diff as service
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError
from tests.workspace.samples.test_patch_sample_application import sample, save, setup, target
from tests.workspace.samples.test_sample_proposal_execution import gate
from tests.workspace.samples.test_patch_sample_application import decision

__all__ = ['sample', 'setup', 'target']


@pytest.fixture
def loop(sample, setup, engine, target, monkeypatch):
    monkeypatch.setattr(contexts, 'SessionLocal', sessionmaker(bind=engine, class_=setup[2]))
    context = contexts.load_tool_execution_context(user_id=target['user_id'], conversation_id=target['conversation_id'])
    return *sample, context


def fingerprint(file):
    info = file.stat()
    return file.read_bytes(), info.st_ino, info.st_mtime_ns


def exercise(loop, mode, verify=None):
    bindings, scope, root, context = loop
    file = root / 'example.txt'
    original = fingerprint(file)
    before = service.read_task_sample_diff(context=context, bindings=bindings)
    assert before.data == b'' and before.content_sha256 == sha256(b'old\n').hexdigest()
    if verify:
        verify('failed')
    identity = save(scope)
    assert fingerprint(file) == original
    decision.decide_task_file_edit_proposal(**identity, decision='rejected' if mode == 'rejected' else 'approved')
    assert fingerprint(file) == original
    if mode == 'stale':
        file.write_bytes(b'external edit\n')
    before_application = fingerprint(file)
    if mode == 'rejected':
        # 明确拒绝后不调用应用服务；拒绝不是可执行的授权。
        assert fingerprint(file) == before_application
    else:
        receipt = gate.execute_sample_proposal(bindings, **identity)
        assert receipt.application_status == ('applied' if mode == 'approved' else 'not_applied')
        if mode == 'stale':
            assert receipt.file_status == 'not_attempted' and fingerprint(file) == before_application
    after = fingerprint(file)
    diff = service.read_task_sample_diff(context=context, bindings=bindings)
    assert diff.content_sha256 == sha256(after[0]).hexdigest()
    assert diff.baseline_sha256 == before.baseline_sha256
    if mode == 'rejected':
        assert diff.data == b''
    else:
        assert b'-old\n+' + after[0] in diff.data
    if verify:
        verify('passed' if mode == 'approved' else 'failed')
    assert fingerprint(file) == after and not (root / '.git').exists()
    bindings.close(**scope)
    assert not root.exists()


@pytest.mark.parametrize('mode', ['approved', 'rejected', 'stale'])
def test_service_loop(loop, mode):
    exercise(loop, mode)


@pytest.mark.parametrize('kind', ['context', 'busy', 'sealed', 'missing'])
def test_unavailable_never_starts_git(loop, monkeypatch, kind):
    bindings, scope, _root, context = loop
    if kind == 'context':
        context = replace(context, task_id='f' * 32)
    elif kind == 'missing':
        bindings.close(**scope)
    elif kind == 'sealed':
        next(iter(bindings._bindings.values())).state = 'uncertain'
    monkeypatch.setattr(service.git, 'temporary_git_status_sample', lambda: pytest.fail('must not start Git'))
    if kind == 'busy':
        with bindings.borrow(**scope), pytest.raises(TaskSampleBindingError):
            service.read_task_sample_diff(context=context, bindings=bindings)
    else:
        with pytest.raises(ValueError):
            service.read_task_sample_diff(context=context, bindings=bindings)


def test_git_failure_releases_copy_without_sealing_source(loop, setup, monkeypatch):
    bindings, scope, root, context = loop
    original = fingerprint(root / 'example.txt')
    paths = []
    def fail(sample, **kwargs):
        paths.append(sample.root)
        assert bindings.read_status(**scope).status == 'ready'
        assert all(s.closed and not s.in_transaction() for s in setup[3])
        raise service.git.GitStatusCaptureError('git_status_unavailable')
    monkeypatch.setattr(service, 'collect_sample_git_diff', fail)
    with pytest.raises(service.git.GitStatusCaptureError):
        service.read_task_sample_diff(context=context, bindings=bindings)
    assert paths and all(not path.exists() for path in paths)
    assert fingerprint(root / 'example.txt') == original
    assert bindings.read_status(**scope).status == 'ready'


def test_rejected_execution_seals_and_stops_followup(loop):
    bindings, scope, root, context = loop
    identity = save(scope)
    decision.decide_task_file_edit_proposal(**identity, decision='rejected')
    before = fingerprint(root / 'example.txt')
    with pytest.raises(gate.SampleExecutionError):
        gate.execute_sample_proposal(bindings, **identity)
    assert fingerprint(root / 'example.txt') == before
    assert bindings.read_status(**scope).status == 'sealed'
    with pytest.raises(TaskSampleBindingError):
        service.read_task_sample_diff(context=context, bindings=bindings)
    # 保留封锁来源供原所有者诊断；测试夹具最终只清理自己创建的句柄。


@pytest.mark.parametrize('kind', ['root', 'origin'])
def test_source_mismatch_stops_before_git(loop, engine, monkeypatch, kind):
    bindings, scope, root, context = loop
    before = fingerprint(root / 'example.txt')
    with Session(engine) as session, session.begin():
        if kind == 'root':
            require_value(session.scalar(select(Workspace))).root_path = '/unavailable'
        else:
            require_value(session.scalar(select(WorkspaceSampleOrigin))).lifecycle_state = 'cleanup_pending'
    monkeypatch.setattr(service.git, 'temporary_git_status_sample', lambda: pytest.fail('must not start Git'))
    with pytest.raises(TaskSampleBindingError):
        service.read_task_sample_diff(context=context, bindings=bindings)
    assert fingerprint(root / 'example.txt') == before
    assert bindings.read_status(**scope).status == 'sealed'
