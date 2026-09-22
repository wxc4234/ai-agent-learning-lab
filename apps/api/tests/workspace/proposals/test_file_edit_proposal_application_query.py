"""应用状态的授权只读查询；真实隔离PostgreSQL与受控异常。"""

from dataclasses import FrozenInstanceError, asdict

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import FileEditProposal, Workspace
from app.repositories.workspace.file_edit_proposal_repository import read_owned_proposal_application_status
from app.services.workspace.proposals import file_edit_proposal_application_query as service
from tests.workspace.proposals import test_file_edit_proposal_query as query_tests

root = query_tests.root
target = query_tests.target
database = query_tests.database
setup = query_tests.setup
saved = query_tests.saved


@pytest.fixture
def ready(saved, setup, engine, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'local')
    monkeypatch.setattr(service, 'SessionLocal', sessionmaker(bind=engine, class_=setup[3]))
    return saved


@pytest.mark.parametrize('status', ['idle', 'running', 'applied', 'not_applied', 'uncertain'])
def test_five_states_are_private_readonly_snapshots(ready, setup, engine, database, monkeypatch, status):
    query, file, sessions = ready
    with Session(engine) as session, session.begin():
        row = session.scalar(select(FileEditProposal))
        row.status = 'approved'
        row.application_status = status
        row.application_token = None if status == 'idle' else '0123456789abcdef' * 2
    before = file.read_bytes()
    statements = database[1]
    statements.clear()
    monkeypatch.setattr(setup[3], 'commit', lambda self: pytest.fail('query must not commit'))
    result = service.get_task_file_edit_proposal_application_status(**query)
    assert asdict(result) == {key: query[key] for key in ('proposal_id', 'workspace_id', 'task_id')} | {
        'application_status': status,
    }
    assert len(statements) == 1
    sql = statements[0].lower()
    assert sql.lstrip().startswith('select') and 'for update' not in sql
    for private in ('application_token', 'bound_root', 'root_path', 'proposed_content', 'diff', 'sha256'):
        assert private not in sql
    assert sessions[-1].closed and not sessions[-1].in_transaction()
    assert file.read_bytes() == before
    assert '0123456789abcdef' * 2 not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.application_status = 'idle'
    with Session(engine) as session:
        row = session.scalar(select(FileEditProposal))
        assert row.application_status == status
        assert row.application_token == (None if status == 'idle' else '0123456789abcdef' * 2)


@pytest.mark.parametrize('kind', [
    'foreign-user', 'missing-workspace', 'missing-task', 'missing-proposal',
    'sibling-task', 'wrong-workspace', 'foreign-conversation', 'missing-conversation',
    'changed-owner', 'deleted-task',
])
def test_each_query_reauthorizes_and_uses_safe_error(ready, engine, target, monkeypatch, kind):
    # 复用归属破坏场景；实际执行的是本课新查询，不运行旧查询服务。
    monkeypatch.setattr(query_tests.service, 'get_task_file_edit_proposal',
                        service.get_task_file_edit_proposal_application_status)
    query_tests.test_all_inaccessible_resources_use_same_safe_error(ready, engine, target, kind)


@pytest.mark.parametrize('status', [None, '', 'unknown', 'APPLIED', 'PRIVATE', 1, True, [], {}])
def test_unknown_protocol_rejected_without_defaulting(ready, monkeypatch, status):
    read = service.read_owned_proposal_application_status
    def invalid(*args, **kwargs):
        return dict(read(*args, **kwargs)) | {'application_status': status}
    monkeypatch.setattr(service, 'read_owned_proposal_application_status', invalid)
    with pytest.raises(service.ProposalApplicationQueryError) as caught:
        service.get_task_file_edit_proposal_application_status(**ready[0])
    assert caught.value.code == 'proposal_application_status_invalid'
    assert str(caught.value) == '提案应用状态无法识别'
    assert ready[2][-1].closed


@pytest.mark.parametrize('binding', [None, '/changed'])
def test_history_survives_binding_change_and_missing_file(ready, engine, binding):
    before = service.get_task_file_edit_proposal_application_status(**ready[0])
    ready[1].unlink()
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = binding
    assert service.get_task_file_edit_proposal_application_status(**ready[0]) == before
    assert not ready[1].exists()


def test_repository_does_not_flush_unrelated_changes(ready, engine):
    with Session(engine) as session:
        workspace = session.scalar(select(Workspace))
        workspace.name = 'not committed'
        def forbidden(*args, **kwargs):
            pytest.fail('repository query must not autoflush')
        session.flush = forbidden
        row = read_owned_proposal_application_status(session, **ready[0])
        assert row['application_status'] == 'idle' and workspace in session.dirty
    with Session(engine) as session:
        assert session.scalar(select(Workspace)).name != 'not committed'


def test_database_error_closes_session_without_inventing_state(ready, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('controlled database failure')
    monkeypatch.setattr(service, 'read_owned_proposal_application_status', fail)
    with pytest.raises(RuntimeError, match='controlled database failure'):
        service.get_task_file_edit_proposal_application_status(**ready[0])
    assert ready[2][-1].closed and not ready[2][-1].in_transaction()


def test_second_query_observes_committed_state_without_cache(ready, engine):
    assert service.get_task_file_edit_proposal_application_status(**ready[0]).application_status == 'idle'
    with Session(engine) as session, session.begin():
        row = session.scalar(select(FileEditProposal))
        row.status = 'approved'
        row.application_status = 'running'
        row.application_token = '0123456789abcdef' * 2
    assert service.get_task_file_edit_proposal_application_status(**ready[0]).application_status == 'running'
