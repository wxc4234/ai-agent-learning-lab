"""隔离PostgreSQL验证文件与数据库两个提交边界，不访问开发业务表。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from threading import Barrier

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import FileEditProposal, Workspace
from app.services.workspace.proposals import file_edit_proposal_application as application
from app.services.workspace.proposals import file_edit_proposal_execution as service
from app.services.workspace.proposals import file_edit_proposal_preflight as preflight
from tests.workspace.proposals import test_file_edit_proposal_query as query_tests
from tests.workspace.metadata.metadata_support import plain_metadata

root = query_tests.root
target = query_tests.target
database = query_tests.database
setup = query_tests.setup
saved = query_tests.saved
__all__ = ['plain_metadata']


@pytest.fixture
def ready(saved, setup, engine, monkeypatch):
    # 所有新服务都使用共享隔离库及会话关闭跟踪，默认按本地产品模式验收。
    monkeypatch.setattr(settings, 'app_mode', 'local')
    factory = sessionmaker(bind=engine, class_=setup[3])
    for module in (service, application, preflight):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    with Session(engine) as session, session.begin():
        session.scalar(select(FileEditProposal)).status = 'approved'
    return saved


def state(engine):
    with Session(engine) as session:
        proposal = session.scalar(select(FileEditProposal))
        return proposal.application_status, proposal.application_token


def mutate(engine, **values):
    with Session(engine) as session, session.begin():
        proposal = session.scalar(select(FileEditProposal))
        for key, value in values.items():
            setattr(proposal, key, value)


def forbid(**args):
    pytest.fail('must not reach filesystem replacement')


def test_real_success_private_receipt_closed_transactions_and_no_retry(
    ready, engine, monkeypatch, plain_metadata,
):
    query, file, sessions = ready
    before = file.read_bytes()
    writer = service.replace_workspace_text_file
    calls = []

    def checked(**args):
        assert all(s.closed and not s.in_transaction() for s in sessions)
        assert state(engine)[0] == 'running'
        calls.append(args)
        return writer(**args)

    monkeypatch.setattr(service, 'replace_workspace_text_file', checked)
    result = service.execute_task_file_edit_proposal(**query)
    assert result.file_status == 'replaced' and result.application_status == 'applied'
    assert result.cleanup_complete and state(engine)[0] == 'applied'
    assert file.read_bytes() == before.replace(b'old', b'new')
    assert len(calls) == 1
    assert set(asdict(result)) == {
        'proposal_id', 'file_status', 'application_status', 'code', 'cleanup_complete',
    }
    assert state(engine)[1] not in repr(result) and str(file.parent) not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.code = 'changed'
    again = service.execute_task_file_edit_proposal(**query)
    assert again.file_status == 'not_attempted' and len(calls) == 1
    assert state(engine)[0] == 'applied'


@pytest.mark.parametrize('kind', ['foreign', 'missing', 'pending', 'rejected', 'binding'])
def test_claim_rejection_never_writes(ready, engine, target, monkeypatch, kind):
    query = dict(ready[0])
    if kind == 'foreign':
        query['user_id'] = target['other_id']
    elif kind == 'missing':
        query['proposal_id'] = 'missing'
    elif kind == 'binding':
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = None
    else:
        mutate(engine, status=kind)
    monkeypatch.setattr(service, 'replace_workspace_text_file', forbid)
    result = service.execute_task_file_edit_proposal(**query)
    assert result.file_status == 'not_attempted' and result.application_status == 'unknown'
    assert state(engine) == ('idle', None)


@pytest.mark.parametrize('kind', ['baseline', 'content', 'missing-file', 'preflight-error'])
def test_preparation_failure_consumes_attempt(ready, engine, monkeypatch, kind):
    query, file, _ = ready
    if kind == 'baseline':
        file.write_bytes(b'external')
    elif kind == 'content':
        mutate(engine, proposed_content='tampered')
    elif kind == 'missing-file':
        file.unlink()
    else:
        def fail(**args):
            raise OSError('PRIVATE failure')
        monkeypatch.setattr(service, 'check_task_file_edit_proposal', fail)
    monkeypatch.setattr(service, 'replace_workspace_text_file', forbid)
    result = service.execute_task_file_edit_proposal(**query)
    assert result.file_status == 'not_attempted' and result.application_status == 'not_applied'
    assert result.cleanup_complete is None and state(engine)[0] == 'not_applied'
    assert 'PRIVATE' not in repr(result)
    assert service.execute_task_file_edit_proposal(**query).application_status == 'unknown'


@pytest.mark.parametrize('kind', ['source', 'token', 'owner', 'binding', 'approval'])
def test_recheck_after_preflight_blocks_observed_changes(ready, engine, target, monkeypatch, kind):
    original = service.check_task_file_edit_proposal
    def changed(**args):
        result = original(**args)
        if kind == 'source':
            mutate(engine, relative_path='other.txt')
        elif kind == 'token':
            mutate(engine, application_token='f' * 32)
        elif kind == 'approval':
            mutate(engine, status='rejected')
        else:
            with Session(engine) as session, session.begin():
                workspace = session.scalar(select(Workspace))
                if kind == 'owner':
                    workspace.user_id = target['other_id']
                else:
                    workspace.root_path = '/changed'
        return result
    monkeypatch.setattr(service, 'check_task_file_edit_proposal', changed)
    monkeypatch.setattr(service, 'replace_workspace_text_file', forbid)
    result = service.execute_task_file_edit_proposal(**ready[0])
    assert result.file_status == 'not_attempted'
    blocked_registration = kind in ('token', 'owner')
    assert result.application_status == ('unknown' if blocked_registration else 'not_applied')
    assert state(engine)[0] == ('running' if blocked_registration else 'not_applied')


@pytest.mark.parametrize('status', ['replaced', 'not_replaced', 'uncertain'])
@pytest.mark.parametrize('clean', [True, False])
def test_result_mapping_preserves_file_evidence(ready, engine, monkeypatch, status, clean):
    monkeypatch.setattr(service, 'replace_workspace_text_file', lambda **args:
                        service.FileReplaceResult(status, 'PRIVATE-not-public', clean))
    result = service.execute_task_file_edit_proposal(**ready[0])
    outcome = {'replaced': 'applied', 'not_replaced': 'not_applied', 'uncertain': 'uncertain'}[status]
    if not clean:
        outcome = 'uncertain'
    assert result.file_status == status and result.application_status == outcome
    assert result.cleanup_complete is clean and state(engine)[0] == outcome
    assert 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('result', [None, object(),
    service.FileReplaceResult('invalid', 'bad', True),
    service.FileReplaceResult('replaced', 'bad', 1)])
def test_invalid_internal_result_is_uncertain(ready, engine, monkeypatch, result):
    monkeypatch.setattr(service, 'replace_workspace_text_file', lambda **args: result)
    receipt = service.execute_task_file_edit_proposal(**ready[0])
    assert receipt.file_status == 'uncertain' and state(engine)[0] == 'uncertain'


@pytest.mark.parametrize('after_write', [False, True])
def test_writer_exception_cannot_claim_no_write(ready, engine, monkeypatch, plain_metadata, after_write):
    writer = service.replace_workspace_text_file
    before = ready[1].read_bytes()
    def fail(**args):
        if after_write:
            assert writer(**args).status == 'replaced'
        raise OSError('PRIVATE write acknowledgement lost')
    monkeypatch.setattr(service, 'replace_workspace_text_file', fail)
    result = service.execute_task_file_edit_proposal(**ready[0])
    assert result.file_status == 'uncertain' and state(engine)[0] == 'uncertain'
    assert (ready[1].read_bytes() == before) is not after_write


@pytest.mark.parametrize('operation', ['claim', 'finish'])
@pytest.mark.parametrize('timing', ['before_commit', 'after_commit'])
def test_real_database_commit_failure_boundary(
    ready, engine, setup, monkeypatch, plain_metadata, operation, timing,
):
    original = getattr(service, f'{operation}_task_file_edit_proposal')
    def fail(*args):
        raise RuntimeError('PRIVATE commit acknowledgement')
    def wrapped(**args):
        event.listen(setup[3], timing, fail)
        try:
            return original(**args)
        finally:
            event.remove(setup[3], timing, fail)
    monkeypatch.setattr(service, f'{operation}_task_file_edit_proposal', wrapped)
    before = ready[1].read_bytes()
    result = service.execute_task_file_edit_proposal(**ready[0])
    assert result.application_status == 'unknown'
    if operation == 'claim':
        assert result.file_status == 'not_attempted' and ready[1].read_bytes() == before
        assert state(engine)[0] == ('idle' if timing == 'before_commit' else 'running')
    else:
        assert result.file_status == 'replaced' and ready[1].read_bytes() != before
        assert state(engine)[0] == ('running' if timing == 'before_commit' else 'applied')


@pytest.mark.parametrize('phase', ['preflight', 'writer', 'finish'])
def test_interrupt_propagates_without_releasing_claim(ready, engine, monkeypatch, plain_metadata, phase):
    name = {'preflight': 'check_task_file_edit_proposal',
            'writer': 'replace_workspace_text_file', 'finish': 'finish_task_file_edit_proposal'}[phase]
    def interrupt(**args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(service, name, interrupt)
    with pytest.raises(KeyboardInterrupt):
        service.execute_task_file_edit_proposal(**ready[0])
    assert state(engine)[0] == 'running'
    assert (b'new' in ready[1].read_bytes()) is (phase == 'finish')


def test_concurrent_execution_only_one_writer(ready, engine, monkeypatch, plain_metadata):
    barrier = Barrier(2)
    claim = service.claim_task_file_edit_proposal
    writer = service.replace_workspace_text_file
    calls = []
    def competing(**args):
        barrier.wait(timeout=5)
        return claim(**args)
    def tracked(**args):
        calls.append(1)
        return writer(**args)
    monkeypatch.setattr(service, 'claim_task_file_edit_proposal', competing)
    monkeypatch.setattr(service, 'replace_workspace_text_file', tracked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(service.execute_task_file_edit_proposal, **ready[0]) for _ in range(2)]
        results = [job.result(timeout=10) for job in jobs]
    assert len(calls) == 1 and state(engine)[0] == 'applied'
    assert sorted(result.file_status for result in results) == ['not_attempted', 'replaced']


def test_real_xattrs_are_applied(ready, engine):
    from app.services.workspace.metadata import workspace_file_metadata as metadata
    from tests.workspace.metadata.test_workspace_metadata_xattrs import set_attr
    with ready[1].open('rb') as file:
        set_attr(file.fileno())
        expected = metadata.read_file_metadata(file.fileno())
    before = ready[1].read_bytes()
    result = service.execute_task_file_edit_proposal(**ready[0])
    assert result.file_status == 'replaced' and result.application_status == 'applied'
    assert result.cleanup_complete and state(engine)[0] == 'applied'
    assert ready[1].read_bytes() == before.replace(b'old', b'new')
    with ready[1].open('rb') as file:
        assert metadata.read_file_metadata(file.fileno()) == expected


def test_external_edit_after_preflight_is_checked_again(ready, engine, monkeypatch, plain_metadata):
    writer = service.replace_workspace_text_file
    def changed(**args):
        ready[1].write_bytes(b'external edit')
        return writer(**args)
    monkeypatch.setattr(service, 'replace_workspace_text_file', changed)
    result = service.execute_task_file_edit_proposal(**ready[0])
    assert result.file_status == 'not_replaced' and state(engine)[0] == 'not_applied'
    assert ready[1].read_bytes() == b'external edit'
