"""隔离PostgreSQL与真实文件验证只读核对及跨阶段失效。"""

from dataclasses import FrozenInstanceError, asdict
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal, Workspace
from app.services.workspace.proposals import file_edit_proposal_preflight as service
from app.services.workspace.files import workspace_file
from app.services.workspace.directory import workspace_path
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


def mutate(engine, **values):
    with Session(engine) as session, session.begin():
        proposal = session.scalar(select(FileEditProposal))
        for key, value in values.items():
            setattr(proposal, key, value)


def test_snapshot_is_readonly_private_and_closes_transactions(ready, database, monkeypatch):
    query, file, sessions = ready
    statements = database[1]
    statements.clear()
    monkeypatch.setattr(sessions[0].__class__, 'commit', lambda self: pytest.fail('no commit'))
    read = service.read_task_text_file

    def checked(**args):
        assert all(s.closed and not s.in_transaction() for s in sessions)
        return read(**args)

    monkeypatch.setattr(service, 'read_task_text_file', checked)
    before = file.read_bytes()
    result = service.check_task_file_edit_proposal(**query)
    assert result.baseline_sha256 == sha256(before).hexdigest()
    assert result.current_byte_count == len(before)
    assert result.proposed_sha256 == sha256(before.replace(b'old', b'new')).hexdigest()
    assert result.proposed_byte_count == len(before)
    assert set(asdict(result)) == {'proposal_id', 'workspace_id', 'task_id', 'relative_path',
                                  'baseline_sha256', 'proposed_sha256', 'current_byte_count', 'proposed_byte_count'}
    assert all(sql.lstrip().lower().startswith('select') and 'for update' not in sql.lower() for sql in statements)
    assert file.read_bytes() == before
    with pytest.raises(FrozenInstanceError):
        result.proposal_id = 'changed'


@pytest.mark.parametrize('kind', [
    'foreign-user', 'missing-workspace', 'missing-task', 'missing-proposal',
    'sibling-task', 'wrong-workspace', 'foreign-conversation', 'missing-conversation',
    'changed-owner', 'deleted-task',
])
def test_reauthorizes_before_any_file_read(ready, engine, target, kind, monkeypatch):
    monkeypatch.setattr(service, 'read_task_text_file', lambda **args: pytest.fail('must authorize first'))
    monkeypatch.setattr(query_tests.service, 'get_task_file_edit_proposal', service.check_task_file_edit_proposal)
    query_tests.test_all_inaccessible_resources_use_same_safe_error(ready, engine, target, kind)


@pytest.mark.parametrize('status', ['pending', 'rejected'])
def test_unapproved_rejected_before_read(ready, engine, status, monkeypatch):
    mutate(engine, status=status)
    monkeypatch.setattr(service, 'read_task_text_file', lambda **args: pytest.fail('no file read'))
    with pytest.raises(service.ProposalPreflightError, match='尚未批准'):
        service.check_task_file_edit_proposal(**ready[0])


@pytest.mark.parametrize('binding', [None, '/changed'])
def test_binding_change_rejected_before_read(ready, engine, binding, monkeypatch):
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = binding
    monkeypatch.setattr(service, 'read_task_text_file', lambda **args: pytest.fail('no file read'))
    with pytest.raises(ProposalBindingChangedError):
        service.check_task_file_edit_proposal(**ready[0])


@pytest.mark.parametrize('values', [
    {'proposed_content': 'tampered'}, {'proposed_sha256': 'a' * 64},
])
def test_invalid_saved_content_cannot_reach_files(ready, engine, values, monkeypatch):
    mutate(engine, **values)
    monkeypatch.setattr(service, 'read_task_text_file', lambda **args: pytest.fail('no file read'))
    with pytest.raises(service.ProposalPreflightError) as caught:
        service.check_task_file_edit_proposal(**ready[0])
    assert caught.value.code == 'proposal_content_invalid'


def test_empty_proposed_file_valid(ready, engine):
    mutate(engine, proposed_content='', proposed_sha256=sha256(b'').hexdigest())
    assert service.check_task_file_edit_proposal(**ready[0]).proposed_byte_count == 0


@pytest.mark.parametrize('content', [b'changed', b'\xef\xbb\xbfold\n', b'old\r\n'])
def test_byte_exact_baseline_required(ready, content):
    ready[1].write_bytes(content)
    with pytest.raises(service.ProposalPreflightError) as caught:
        service.check_task_file_edit_proposal(**ready[0])
    assert caught.value.code == 'proposal_baseline_changed'
    assert ready[1].read_bytes() == content


@pytest.mark.parametrize('kind', ['missing', 'outside-link', 'directory', 'binary', 'oversize'])
def test_real_file_failures(ready, tmp_path, kind):
    file = ready[1]
    if kind in ('missing', 'outside-link', 'directory'):
        file.unlink()
    if kind == 'outside-link':
        outside = tmp_path / 'outside.txt'
        outside.write_bytes(b'\xef\xbb\xbfold\r\n')
        file.symlink_to(outside)
    elif kind == 'directory':
        file.mkdir()
    elif kind == 'binary':
        file.write_bytes(b'\x00\xff')
    elif kind == 'oversize':
        file.write_bytes(b'x' * (256 * 1024 + 1))
    with pytest.raises((workspace_file.WorkspaceFileError, workspace_path.WorkspacePathError)):
        service.check_task_file_edit_proposal(**ready[0])


def test_binding_changed_between_snapshot_and_path_resolution(ready, engine, monkeypatch):
    read = service.read_task_text_file

    def changed(**args):
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = '/changed'
        return read(**args)

    monkeypatch.setattr(service, 'read_task_text_file', changed)
    monkeypatch.setattr(workspace_file, '_read_resolved_file', lambda path: pytest.fail('no wrong-root read'))
    with pytest.raises(workspace_path.WorkspacePathError) as caught:
        service.check_task_file_edit_proposal(**ready[0])
    assert caught.value.code == 'workspace_directory_changed'


@pytest.mark.parametrize('change', ['binding', 'content', 'status', 'owner'])
def test_changes_during_read_are_rechecked(ready, engine, target, change, monkeypatch):
    read = service.read_task_text_file

    def changed(**args):
        result = read(**args)
        with Session(engine) as session, session.begin():
            if change == 'binding':
                session.scalar(select(Workspace)).root_path = None
            elif change == 'owner':
                session.scalar(select(Workspace)).user_id = target['other_id']
            else:
                row = session.scalar(select(FileEditProposal))
                if change == 'content':
                    row.proposed_content = 'changed'
                else:
                    row.status = 'rejected'
        return result

    monkeypatch.setattr(service, 'read_task_text_file', changed)
    from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
    with pytest.raises(WorkspaceNotAccessibleError if change == 'owner' else service.ProposalPreflightError):
        service.check_task_file_edit_proposal(**ready[0])


def test_snapshot_does_not_claim_to_stop_later_file_edits(ready, monkeypatch):
    read = service.read_task_text_file

    def changed(**args):
        result = read(**args)
        ready[1].write_bytes(b'later edit')
        return result

    monkeypatch.setattr(service, 'read_task_text_file', changed)
    result = service.check_task_file_edit_proposal(**ready[0])
    assert result.baseline_sha256 != sha256(ready[1].read_bytes()).hexdigest()
    assert ready[1].read_bytes() == b'later edit'


@pytest.mark.parametrize('values,code', [
    ({'diff_truncated': True}, 'proposal_diff_incomplete'),
    ({'proposed_content': '\x00'}, 'proposal_content_invalid'),
    ({'proposed_content': '\ud800'}, 'proposal_content_invalid'),
    ({'proposed_content': None}, 'proposal_content_invalid'),
])
def test_invalid_internal_snapshot_fails_closed(ready, monkeypatch, values, code):
    # 数据库约束通常阻止这些值；仍验证内部边界不凭approved跳过内容校验。
    read = service.read_owned_proposal_application_source

    def changed(*args, **kwargs):
        return {**read(*args, **kwargs), **values}

    monkeypatch.setattr(service, 'read_owned_proposal_application_source', changed)
    monkeypatch.setattr(service, 'read_task_text_file', lambda **args: pytest.fail('no read'))
    with pytest.raises(service.ProposalPreflightError) as caught:
        service.check_task_file_edit_proposal(**ready[0])
    assert caught.value.code == code


@pytest.mark.parametrize('phase', [1, 2])
def test_query_failure_closes_session_without_success(ready, monkeypatch, phase):
    read = service.read_owned_proposal_application_source
    calls = 0

    def failing(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == phase:
            raise RuntimeError('controlled database failure')
        return read(*args, **kwargs)

    monkeypatch.setattr(service, 'read_owned_proposal_application_source', failing)
    with pytest.raises(RuntimeError, match='controlled database failure'):
        service.check_task_file_edit_proposal(**ready[0])
    assert all(s.closed and not s.in_transaction() for s in ready[2])


@pytest.mark.parametrize('values', [
    {'baseline_sha256': 'invalid'},
    {'proposed_content': 'x' * (256 * 1024 + 1)},
    {'proposed_content': '中' * 100000},
])
def test_storage_constraint_defense_at_internal_boundary(ready, monkeypatch, values):
    # 这些值会被真实数据库约束拒绝，用读取边界注入验证服务自身的防御分支。
    read = service.read_owned_proposal_application_source
    monkeypatch.setattr(service, 'read_owned_proposal_application_source',
                        lambda *args, **kwargs: {**read(*args, **kwargs), **values})
    monkeypatch.setattr(service, 'read_task_text_file', lambda **args: pytest.fail('no read'))
    with pytest.raises(service.ProposalPreflightError) as caught:
        service.check_task_file_edit_proposal(**ready[0])
    assert caught.value.code == 'proposal_content_invalid'


def test_internal_repository_does_not_flush_pending_changes(ready, engine, target):
    from app.models import Task
    with Session(engine) as session:
        task = session.get(Task, target['task_pk'])
        task.title = 'uncommitted'
        result = service.read_owned_proposal_application_source(session, **ready[0])
        assert result['status'] == 'approved' and task in session.dirty
        with Session(engine) as other:
            assert other.get(Task, target['task_pk']).title == '空任务'
        session.rollback()
