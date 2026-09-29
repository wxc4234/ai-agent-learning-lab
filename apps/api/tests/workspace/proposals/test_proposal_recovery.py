"""恢复请求只产生可审阅反向提案；真实文件副作用仍通过原审批应用链。"""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.proposals import proposal_recovery as recovery
from app.services.workspace.proposals.file_edit_proposal_decision import decide_task_file_edit_proposal
from tests.workspace.proposals.test_project_write_execution import (
    database, execution_database, grants, ready, root, saved, setup, target,
)

__all__ = ['database', 'execution_database', 'grants', 'ready', 'root', 'saved', 'setup', 'target']


@pytest.fixture(autouse=True)
def recovery_database(engine, setup, monkeypatch):
    from app.services.workspace.proposals import file_edit_proposal_decision as decision
    factory = sessionmaker(bind=engine, class_=setup[3])
    monkeypatch.setattr(recovery, 'SessionLocal', factory)
    monkeypatch.setattr(decision, 'SessionLocal', factory)


def applied(grants, ready):
    scope, file, _ = ready
    original = file.read_bytes()
    grant = grants.issue(**scope)
    assert grants.apply(**scope, grant_id=grant.grant_id, revision=1).application_status == 'applied'
    return scope, file, original


def test_restore_requires_fresh_approval_and_grant(grants, ready, engine):
    scope, file, original = applied(grants, ready)
    after = file.read_bytes()
    reverse = recovery.create_restore_proposal(**scope)
    assert recovery.create_restore_proposal(**scope) == reverse
    assert file.read_bytes() == after
    with Session(engine) as session:
        row = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == reverse))
        assert row is not None and row.status == 'pending' and row.application_status == 'idle'
    reverse_scope = {**scope, 'proposal_id': reverse}
    with pytest.raises(ValueError):
        grants.issue(**reverse_scope)
    decide_task_file_edit_proposal(**reverse_scope, decision='approved')
    grant = grants.issue(**reverse_scope)
    assert grants.apply(**reverse_scope, grant_id=grant.grant_id, revision=1).application_status == 'applied'
    assert file.read_bytes() == original
    audit = recovery.read_proposal_audit(**scope)
    assert audit[-1]['event'] == 'restore_requested' and audit[-1]['restore_proposal_id'] == reverse
    assert all(set(item) == {'event', 'created_at', 'restore_proposal_id'} for item in audit)


def test_concurrent_restore_creates_one_reverse(grants, ready):
    scope, _, _ = applied(grants, ready)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: recovery.create_restore_proposal(**scope), range(2)))
    assert len(set(ids)) == 1


@pytest.mark.parametrize('kind', ['changed', 'running', 'uncertain', 'missing_backup', 'tampered_backup'])
def test_recovery_refuses_unsafe_source(grants, ready, engine, kind):
    scope, file, _ = applied(grants, ready)
    if kind == 'changed':
        file.write_text('external edit')
    else:
        with Session(engine) as session, session.begin():
            row = session.scalar(select(FileEditProposal))
            assert row is not None
            if kind in {'running', 'uncertain'}:
                row.application_status = kind
            else:
                row.baseline_content = None if kind == 'missing_backup' else 'tampered'
    before = file.read_bytes()
    with pytest.raises(ValueError):
        recovery.create_restore_proposal(**scope)
    assert file.read_bytes() == before


@pytest.mark.parametrize('operation', [recovery.create_restore_proposal, recovery.read_proposal_audit])
@pytest.mark.parametrize('field', ['user_id', 'workspace_id', 'task_id', 'proposal_id'])
def test_recovery_and_audit_reauthorize(grants, ready, operation, field):
    scope, _, _ = applied(grants, ready)
    invalid = {**scope, field: 999999 if field == 'user_id' else 'f' * 32}
    with pytest.raises(WorkspaceNotAccessibleError):
        operation(**invalid)
