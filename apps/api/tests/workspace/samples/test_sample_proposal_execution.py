"""真实样例登记、审批、授权门禁与文件执行；PostgreSQL独立隔离。"""

from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.samples import sample_proposal_execution as gate
from app.services.workspace.proposals import file_edit_proposal_execution as execution
from app.services.workspace.proposals import file_edit_proposal_application as application
from app.services.workspace.proposals import file_edit_proposal_preflight as preflight
from app.services.workspace.proposals import file_edit_proposal_service as proposals
from app.services.workspace.proposals import file_edit_proposal_decision as decision
from app.services.workspace.directory import workspace_path
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError, TaskSampleBindings
from tests.workspace.samples.test_task_sample_binding import setup, target

__all__ = ['setup', 'target']


@pytest.fixture
def ready(setup, engine, monkeypatch):
    bindings, scope, tracked, _ = setup
    factory = sessionmaker(bind=engine, class_=tracked)
    for module in (gate, execution, application, preflight, proposals, decision, workspace_path):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    bindings.bind(**scope)
    created = proposals.create_task_file_edit_proposal(**scope, relative_path='example.txt', old_text='old', new_text='new')
    identity = {**scope, 'proposal_id': created.proposal_id}
    decision.decide_task_file_edit_proposal(**identity, decision='approved')
    with Session(engine) as session:
        path = Path(session.scalar(select(Workspace.root_path)))
    return bindings, scope, identity, path


def state(engine):
    with Session(engine) as session:
        return session.scalar(select(FileEditProposal.application_status))


def test_real_success_preserves_lease_and_blocks_close(ready, setup, engine, monkeypatch):
    bindings, scope, identity, path = ready
    writer = execution.replace_workspace_text_file
    calls = []
    def checked(**kwargs):
        assert all(s.closed and not s.in_transaction() for s in setup[3])
        assert state(engine) == 'running'
        assert kwargs['expected_parent_identity'] == (path.stat().st_dev, path.stat().st_ino)
        with pytest.raises(TaskSampleBindingError):
            bindings.close(**scope)
        calls.append(1)
        return writer(**kwargs)
    monkeypatch.setattr(execution, 'replace_workspace_text_file', checked)
    receipt = gate.execute_sample_proposal(bindings, **identity)
    assert receipt.file_status == 'replaced' and receipt.application_status == state(engine) == 'applied'
    assert (path / 'example.txt').read_bytes() == b'new\n' and calls == [1]
    assert not list(path.glob('.agent-edit-*.tmp'))
    bindings.close(**scope)
    assert not path.exists()


@pytest.mark.parametrize('kind', ['ordinary', 'foreign', 'sibling', 'proposal', 'path', 'bound_root', 'approval'])
def test_invalid_scope_never_enters_executor(ready, target, engine, monkeypatch, kind):
    bindings, _scope, identity, path = ready
    args = dict(identity)
    if kind == 'ordinary':
        bindings = TaskSampleBindings()
    elif kind == 'foreign':
        args['user_id'] = target['other_id']
    elif kind == 'sibling':
        args['task_id'] = 'd' * 32
    elif kind == 'proposal':
        args['proposal_id'] = 'f' * 32
    else:
        with Session(engine) as session, session.begin():
            proposal = session.scalar(select(FileEditProposal))
            if kind == 'path':
                proposal.relative_path = 'other.txt'
            elif kind == 'bound_root':
                proposal.bound_root = '/other'
            else:
                proposal.status = 'pending'
    monkeypatch.setattr(gate, 'execute_task_file_edit_proposal', lambda **kwargs: pytest.fail('must not execute'))
    with pytest.raises((TaskSampleBindingError, gate.SampleExecutionError, WorkspaceNotAccessibleError)):
        gate.execute_sample_proposal(bindings, **args)
    assert state(engine) == 'idle' and (path / 'example.txt').read_bytes() == b'old\n'


@pytest.mark.parametrize('timing', ['before_commit', 'after_commit'])
def test_registration_unknown_seals_binding_and_retains_file(ready, setup, engine, monkeypatch, timing):
    bindings, scope, identity, path = ready
    finish = execution.finish_task_file_edit_proposal
    def fail(*args):
        raise RuntimeError('private commit')
    def wrapped(**kwargs):
        event.listen(setup[2], timing, fail)
        try:
            return finish(**kwargs)
        finally:
            event.remove(setup[2], timing, fail)
    monkeypatch.setattr(execution, 'finish_task_file_edit_proposal', wrapped)
    receipt = gate.execute_sample_proposal(bindings, **identity)
    assert receipt.file_status == 'replaced' and receipt.application_status == 'unknown'
    assert state(engine) == ('running' if timing == 'before_commit' else 'applied')
    assert (path / 'example.txt').read_bytes() == b'new\n'
    with pytest.raises(TaskSampleBindingError), bindings.borrow(**scope):
        pytest.fail('must remain sealed')
    with pytest.raises(TaskSampleBindingError):
        bindings.close(**scope)


@pytest.mark.parametrize('phase', ['before_claim', 'before_preflight'])
def test_database_root_switch_cannot_redirect_execution(ready, engine, tmp_path, monkeypatch, phase):
    bindings, _, identity, path = ready
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'example.txt').write_bytes(b'old\n')
    def mutate():
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = str(other)
            session.scalar(select(FileEditProposal)).bound_root = str(other)
    module = gate if phase == 'before_claim' else execution
    name = 'execute_task_file_edit_proposal' if phase == 'before_claim' else 'check_task_file_edit_proposal'
    original = getattr(module, name)
    def switched(**kwargs):
        mutate()
        return original(**kwargs)
    monkeypatch.setattr(module, name, switched)
    receipt = gate.execute_sample_proposal(bindings, **identity)
    assert receipt.file_status == 'not_attempted' and receipt.application_status == 'not_applied'
    assert (other / 'example.txt').read_bytes() == (path / 'example.txt').read_bytes() == b'old\n'


def test_directory_replacement_after_preflight_rejected(ready, engine, monkeypatch):
    bindings, _, identity, path = ready
    writer = execution.replace_workspace_text_file
    moved = path.with_name('moved')
    def switched(**kwargs):
        path.rename(moved)
        path.mkdir(mode=0o700)
        (path / 'example.txt').write_bytes(b'old\n')
        return writer(**kwargs)
    monkeypatch.setattr(execution, 'replace_workspace_text_file', switched)
    receipt = gate.execute_sample_proposal(bindings, **identity)
    assert receipt.file_status == 'not_replaced' and state(engine) == 'not_applied'
    assert (path / 'example.txt').read_bytes() == (moved / 'example.txt').read_bytes() == b'old\n'


def test_interrupt_keeps_running_and_seals_sample(ready, engine, monkeypatch):
    bindings, scope, identity, path = ready
    def stop(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(execution, 'replace_workspace_text_file', stop)
    with pytest.raises(KeyboardInterrupt):
        gate.execute_sample_proposal(bindings, **identity)
    assert state(engine) == 'running' and path.exists()
    with pytest.raises(TaskSampleBindingError), bindings.borrow(**scope):
        pytest.fail('sealed')


@pytest.mark.parametrize('result', [None, execution.ProposalExecutionResult(
    'f' * 32, 'replaced', 'applied', 'proposal_application_applied', True),
    execution.ProposalExecutionResult('placeholder', 'not_replaced', 'applied',
                                      'proposal_application_applied', True)])
def test_invalid_receipt_seals_without_inventing_result(ready, monkeypatch, result):
    from dataclasses import replace
    bindings, scope, identity, path = ready
    if result is not None and result.proposal_id == 'placeholder':
        result = replace(result, proposal_id=identity['proposal_id'])
    monkeypatch.setattr(gate, 'execute_task_file_edit_proposal', lambda **kwargs: result)
    with pytest.raises(gate.SampleExecutionError):
        gate.execute_sample_proposal(bindings, **identity)
    assert path.exists()
    with pytest.raises(TaskSampleBindingError), bindings.borrow(**scope):
        pytest.fail('invalid receipt')


def test_duplicate_execution_does_not_write_again(ready, monkeypatch):
    bindings, scope, identity, path = ready
    assert gate.execute_sample_proposal(bindings, **identity).application_status == 'applied'
    monkeypatch.setattr(execution, 'replace_workspace_text_file', lambda **kwargs: pytest.fail('second write'))
    receipt = gate.execute_sample_proposal(bindings, **identity)
    assert receipt.file_status == 'not_attempted' and receipt.application_status == 'unknown'
    assert (path / 'example.txt').read_bytes() == b'new\n'
    with pytest.raises(TaskSampleBindingError), bindings.borrow(**scope):
        pytest.fail('unknown receipt sealed')


def test_uncertain_file_result_is_registered_and_sample_sealed(ready, engine, monkeypatch):
    bindings, scope, identity, path = ready
    monkeypatch.setattr(execution, 'replace_workspace_text_file', lambda **kwargs:
                        execution.FileReplaceResult('uncertain', 'file_replace_uncertain', True))
    receipt = gate.execute_sample_proposal(bindings, **identity)
    assert receipt.file_status == 'uncertain' and receipt.application_status == state(engine) == 'uncertain'
    assert path.exists()
    with pytest.raises(TaskSampleBindingError), bindings.borrow(**scope):
        pytest.fail('uncertain sealed')
