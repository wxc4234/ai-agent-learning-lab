"""真实PG/目录验证整组审批、文件生命周期、故障恢复和重放拒绝。"""
import pytest
from sqlalchemy.orm import sessionmaker
from app.services.workspace.proposals import change_sets as service
from app.services.workspace.files import change_set_files as files
from tests.workspace.proposals.test_project_write_execution import database, setup, root, target, saved, ready
__all__ = ['database', 'ready', 'root', 'saved', 'setup', 'target']

@pytest.fixture
def scope(engine, setup, ready, monkeypatch):
    monkeypatch.setattr(service, 'SessionLocal', sessionmaker(bind=engine, class_=setup[3]))
    return {key: value for key, value in ready[0].items() if key != 'proposal_id'}


def create(scope, root):
    (root / 'edit.txt').write_text('old\n')
    (root / 'delete.txt').write_text('delete\n')
    (root / 'move.txt').write_text('move\n')
    return service.create_change_set(**scope, operations=[
        {'kind': 'create', 'path': 'new.txt', 'content': 'new\n'},
        {'kind': 'update', 'path': 'edit.txt', 'patch': '--- a/edit.txt\n+++ b/edit.txt\n@@ -1 +1 @@\n-old\n+updated\n'},
        {'kind': 'delete', 'path': 'delete.txt'},
        {'kind': 'move', 'path': 'move.txt', 'destination': 'moved.txt'},
    ])


def test_all_operations_and_explicit_restore(scope, root):
    change = create(scope, root)
    change_id = change['change_id']
    with pytest.raises(ValueError): service.execute_change_set(**scope, change_id=change_id)
    assert service.decide_change_set(**scope, change_id=change_id, action='approve')['status'] == 'approved'
    result = service.execute_change_set(**scope, change_id=change_id)
    assert result['status'] == 'applied', result
    assert (root / 'new.txt').read_text() == 'new\n'
    assert (root / 'edit.txt').read_text() == 'updated\n'
    assert (root / 'moved.txt').read_text() == 'move\n'
    assert not (root / 'delete.txt').exists() and not (root / 'move.txt').exists()
    with pytest.raises(ValueError): service.execute_change_set(**scope, change_id=change_id)
    assert service.execute_change_set(**scope, change_id=change_id, restore=True)['status'] == 'rolled_back'
    assert (root / 'edit.txt').read_text() == 'old\n'
    assert (root / 'delete.txt').read_text() == 'delete\n'
    assert (root / 'move.txt').read_text() == 'move\n'
    assert not (root / 'new.txt').exists() and not (root / 'moved.txt').exists()
    assert service.execute_change_set(**scope, change_id=change_id, restore=True)['status'] == 'rolled_back'


@pytest.mark.parametrize('at', range(5))
def test_failure_rolls_back_entire_group(scope, root, monkeypatch, at):
    change = create(scope, root)
    service.decide_change_set(**scope, change_id=change['change_id'], action='approve')
    apply = files.apply_files
    def fail(*args, **kw):
        def checkpoint(index):
            if index == at: raise OSError('injected')
        return apply(*args, **{**kw, 'checkpoint': checkpoint})
    monkeypatch.setattr(files, 'apply_files', fail)
    assert service.execute_change_set(**scope, change_id=change['change_id'])['status'] == 'rolled_back'
    assert (root / 'edit.txt').read_text() == 'old\n'
    assert (root / 'move.txt').read_text() == 'move\n'
    assert (root / 'delete.txt').read_text() == 'delete\n'
    assert not (root / 'new.txt').exists()


def test_external_edit_prevents_restore(scope, root):
    change = create(scope, root)
    service.decide_change_set(**scope, change_id=change['change_id'], action='approve')
    assert service.execute_change_set(**scope, change_id=change['change_id'])['status'] == 'applied'
    (root / 'edit.txt').write_text('external')
    assert service.execute_change_set(**scope, change_id=change['change_id'], restore=True)['status'] == 'uncertain'
    assert (root / 'edit.txt').read_text() == 'external'
    assert (root / 'new.txt').exists()


@pytest.mark.parametrize('kind', ['path', 'symlink', 'duplicate', 'destination', 'rejected'])
def test_unsafe_candidate_or_decision_refused(scope, root, kind):
    (root / 'file').write_text('old')
    if kind == 'symlink': (root / 'link').symlink_to(root / 'file')
    operations = {
        'path': [{'kind': 'create', 'path': '../escape', 'content': 'bad'}],
        'symlink': [{'kind': 'delete', 'path': 'link'}],
        'duplicate': [{'kind': 'delete', 'path': 'file'}] * 2,
        'destination': [{'kind': 'move', 'path': 'file', 'destination': 'file'}],
    }
    if kind != 'rejected':
        with pytest.raises(ValueError): service.create_change_set(**scope, operations=operations[kind])
    else:
        change = service.create_change_set(**scope, operations=[{'kind': 'delete', 'path': 'file'}])
        service.decide_change_set(**scope, change_id=change['change_id'], action='reject')
        with pytest.raises(ValueError): service.execute_change_set(**scope, change_id=change['change_id'])
    assert (root / 'file').read_text() == 'old'


def test_process_interruption_can_be_recovered_after_restart(scope, root, monkeypatch):
    change = create(scope, root)
    service.decide_change_set(**scope, change_id=change['change_id'], action='approve')
    apply = files.apply_files
    def interrupted(*args, **kw):
        def checkpoint(index):
            if index == 2: raise SystemExit('simulated process loss')
        return apply(*args, **{**kw, 'checkpoint': checkpoint})
    monkeypatch.setattr(files, 'apply_files', interrupted)
    with pytest.raises(SystemExit): service.execute_change_set(**scope, change_id=change['change_id'])
    assert service.list_change_sets(**scope)[0]['status'] == 'running'
    assert service.execute_change_set(**scope, change_id=change['change_id'], restore=True)['status'] == 'rolled_back'
    assert (root / 'edit.txt').read_text() == 'old\n'


def test_interruption_between_link_and_unlink_recovers(scope, root, monkeypatch):
    change = create(scope, root)
    service.decide_change_set(**scope, change_id=change['change_id'], action='approve')
    unlink = files.os.unlink
    def interrupted(name, **kw):
        if name == 'new-0': raise SystemExit('simulated process loss')
        return unlink(name, **kw)
    with monkeypatch.context() as patch:
        patch.setattr(files.os, 'unlink', interrupted)
        with pytest.raises(SystemExit): service.execute_change_set(**scope, change_id=change['change_id'])
    assert service.execute_change_set(**scope, change_id=change['change_id'], restore=True)['status'] == 'rolled_back'
    assert not (root / 'new.txt').exists()


def test_active_executor_blocks_concurrent_restore(scope, root, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    change = create(scope, root)
    service.decide_change_set(**scope, change_id=change['change_id'], action='approve')
    entered, release = Event(), Event()
    apply = files.apply_files
    def waiting(*args, **kw):
        entered.set()
        assert release.wait(5)
        return apply(*args, **kw)
    monkeypatch.setattr(files, 'apply_files', waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.execute_change_set, **scope, change_id=change['change_id'])
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError, match='busy'):
                service.execute_change_set(**scope, change_id=change['change_id'], restore=True)
        finally: release.set()
        assert future.result()['status'] == 'applied'


@pytest.mark.parametrize('field', ['user_id', 'workspace_id', 'task_id'])
def test_every_action_reauthorizes(scope, root, field):
    from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
    change = create(scope, root)
    wrong = {**scope, field: 999999 if field == 'user_id' else 'f' * 32}
    for action in ('approve', 'apply', 'restore'):
        with pytest.raises(WorkspaceNotAccessibleError):
            if action == 'approve': service.decide_change_set(**wrong, change_id=change['change_id'], action=action)
            else: service.execute_change_set(**wrong, change_id=change['change_id'], restore=action == 'restore')


def test_external_deletion_not_overridden_by_repeated_restore(scope, root):
    change = create(scope, root)
    service.decide_change_set(**scope, change_id=change['change_id'], action='approve')
    assert service.execute_change_set(**scope, change_id=change['change_id'])['status'] == 'applied'
    (root / 'edit.txt').unlink()
    for _ in range(2):
        assert service.execute_change_set(**scope, change_id=change['change_id'], restore=True)['status'] == 'uncertain'
        assert not (root / 'edit.txt').exists()


def test_task_and_binding_guards_keep_recovery_evidence(scope, root, engine):
    from app.models import TaskChangeSet
    from app.repositories.workspace.proposal_application_guard import require_no_active_proposal_application, ProposalApplicationBusyError
    from app.services.tasks.task_deletion_service import delete_workspace_task, TaskArtifactRetainedError
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    change = create(scope, root)
    with Session(engine) as session, pytest.raises(TaskArtifactRetainedError):
        delete_workspace_task(session, **scope)
    with Session(engine) as session, session.begin():
        row = session.scalar(select(TaskChangeSet).where(TaskChangeSet.external_id == change['change_id']))
        row.status = 'running'
        workspace, task = service.lock_owned_proposal_task(session, **scope)
        with pytest.raises(ProposalApplicationBusyError):
            require_no_active_proposal_application(session, workspace_id=workspace.id, task_id=task.id)


def test_baseline_changed_before_prepare_remains_uncertain(scope, root):
    change = create(scope, root)
    service.decide_change_set(**scope, change_id=change['change_id'], action='approve')
    (root / 'edit.txt').write_text('external\n')
    result = service.execute_change_set(**scope, change_id=change['change_id'])
    assert result['status'] == 'uncertain'
    assert (root / 'edit.txt').read_text() == 'external\n'
    assert not (root / 'new.txt').exists()
