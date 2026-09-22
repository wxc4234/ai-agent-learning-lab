"""真实事务验证单次领取、执行令牌隔离与终态不可重试。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal, Workspace
from app.services.workspace.proposals import file_edit_proposal_application as service
from app.services.workspace.proposals.file_edit_proposal_service import ProposalBindingChangedError
from tests.workspace.proposals import test_file_edit_proposal_query as query_tests

root = query_tests.root
target = query_tests.target
database = query_tests.database
setup = query_tests.setup
saved = query_tests.saved


@pytest.fixture
def ready(saved, setup, engine, monkeypatch):
    monkeypatch.setattr(service, 'SessionLocal', sessionmaker(bind=engine, class_=setup[3]))
    with Session(engine) as session, session.begin():
        session.scalar(select(FileEditProposal)).status = 'approved'
    return saved


def state(engine):
    with Session(engine) as session:
        p = session.scalar(select(FileEditProposal))
        return p.application_status, p.application_token


@pytest.mark.parametrize('outcome', ['applied', 'not_applied', 'uncertain'])
def test_claim_finish_and_no_reentry(ready, engine, outcome):
    query, file, _ = ready
    before = file.read_bytes()
    claim = service.claim_task_file_edit_proposal(**query)
    assert state(engine) == ('running', claim.application_token)
    assert claim.application_token not in repr(claim)
    with pytest.raises(service.ProposalApplicationError):
        service.claim_task_file_edit_proposal(**query)
    result = service.finish_task_file_edit_proposal(**query, application_token=claim.application_token, outcome=outcome)
    assert state(engine) == (outcome, claim.application_token) and result.application_status == outcome
    with pytest.raises(service.ProposalApplicationError):
        service.finish_task_file_edit_proposal(**query, application_token=claim.application_token, outcome=outcome)
    with pytest.raises(service.ProposalApplicationError):
        service.claim_task_file_edit_proposal(**query)
    assert file.read_bytes() == before


@pytest.mark.parametrize('kind', [
    'foreign-user', 'missing-workspace', 'missing-task', 'missing-proposal', 'sibling-task',
    'wrong-workspace', 'foreign-conversation', 'missing-conversation', 'changed-owner', 'deleted-task',
])
@pytest.mark.parametrize('operation', ['claim', 'finish'])
def test_both_operations_reauthorize(ready, engine, target, monkeypatch, kind, operation):
    call = service.claim_task_file_edit_proposal
    if operation == 'finish':
        claim = call(**ready[0])
        call = lambda **args: service.finish_task_file_edit_proposal(
            **args, application_token=claim.application_token, outcome='uncertain')
    if operation == 'finish' and kind == 'deleted-task':
        # 应用占用保护已接入，合法删除入口现在必须保留该任务。
        from app.repositories.workspace.proposal_application_guard import ProposalApplicationBusyError
        from app.services.tasks.task_deletion_service import delete_workspace_task
        with Session(engine) as session, pytest.raises(ProposalApplicationBusyError):
            delete_workspace_task(session, **{k: ready[0][k] for k in ('user_id', 'workspace_id', 'task_id')})
        assert state(engine)[0] == 'running'
        return
    monkeypatch.setattr(query_tests.service, 'get_task_file_edit_proposal', call)
    query_tests.test_all_inaccessible_resources_use_same_safe_error(ready, engine, target, kind)


@pytest.mark.parametrize('status', ['pending', 'rejected'])
def test_only_approved_can_claim(ready, engine, status):
    with Session(engine) as session, session.begin():
        session.scalar(select(FileEditProposal)).status = status
    with pytest.raises(service.ProposalApplicationError):
        service.claim_task_file_edit_proposal(**ready[0])
    assert state(engine) == ('idle', None)


@pytest.mark.parametrize('binding', [None, '/changed'])
def test_binding_checked_for_claim_but_not_result_recording(ready, engine, binding):
    query = ready[0]
    with Session(engine) as session, session.begin():
        w = session.scalar(select(Workspace))
        previous = w.root_path
        w.root_path = binding
    with pytest.raises(ProposalBindingChangedError):
        service.claim_task_file_edit_proposal(**query)
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = previous
    claim = service.claim_task_file_edit_proposal(**query)
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = binding
    service.finish_task_file_edit_proposal(**query, application_token=claim.application_token, outcome='uncertain')
    assert state(engine)[0] == 'uncertain'


@pytest.mark.parametrize('token,outcome', [('x', 'applied'), ('a' * 32, 'applied'), (None, 'applied'), ('a' * 32, 'failed')])
def test_invalid_completion_keeps_running(ready, engine, token, outcome):
    claim = service.claim_task_file_edit_proposal(**ready[0])
    with pytest.raises(service.ProposalApplicationError):
        service.finish_task_file_edit_proposal(**ready[0], application_token=token, outcome=outcome)
    assert state(engine) == ('running', claim.application_token)


@pytest.mark.parametrize('operation', ['claim', 'finish'])
def test_commit_failure_rolls_back(ready, setup, engine, operation):
    query = ready[0]
    claim = service.claim_task_file_edit_proposal(**query) if operation == 'finish' else None
    before = state(engine)

    def fail(*args):
        raise RuntimeError('controlled commit failure')

    event.listen(setup[3], 'before_commit', fail)
    try:
        with pytest.raises(RuntimeError, match='controlled'):
            if claim:
                service.finish_task_file_edit_proposal(**query, application_token=claim.application_token, outcome='applied')
            else:
                service.claim_task_file_edit_proposal(**query)
    finally:
        event.remove(setup[3], 'before_commit', fail)
    assert state(engine) == before


def test_real_competing_claims_only_one_wins(ready, engine):
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        try:
            return service.claim_task_file_edit_proposal(**ready[0])
        except service.ProposalApplicationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(claim) for _ in range(2)]
        results = [j.result(timeout=8) for j in jobs]
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert state(engine) == ('running', winners[0].application_token)


def test_concurrent_finish_does_not_overwrite_terminal(ready, engine):
    claim = service.claim_task_file_edit_proposal(**ready[0])
    barrier = Barrier(2)

    def finish(outcome):
        barrier.wait(timeout=5)
        try:
            return service.finish_task_file_edit_proposal(**ready[0], application_token=claim.application_token, outcome=outcome)
        except service.ProposalApplicationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(finish, value) for value in ('applied', 'uncertain')]
        results = [job.result(timeout=8) for job in jobs]
    winners = [r for r in results if r]
    assert len(winners) == 1 and state(engine)[0] == winners[0].application_status


@pytest.mark.parametrize('operation', ['claim', 'finish'])
def test_failure_after_commit_keeps_durable_unknown_result(ready, setup, engine, operation):
    query = ready[0]
    claim = service.claim_task_file_edit_proposal(**query) if operation == 'finish' else None

    def fail(*args):
        raise RuntimeError('response lost after commit')

    event.listen(setup[3], 'after_commit', fail)
    try:
        with pytest.raises(RuntimeError, match='response lost'):
            if claim:
                service.finish_task_file_edit_proposal(**query, application_token=claim.application_token, outcome='applied')
            else:
                service.claim_task_file_edit_proposal(**query)
    finally:
        event.remove(setup[3], 'after_commit', fail)
    assert state(engine)[0] == ('applied' if claim else 'running')
    with pytest.raises(service.ProposalApplicationError):
        service.claim_task_file_edit_proposal(**query)
