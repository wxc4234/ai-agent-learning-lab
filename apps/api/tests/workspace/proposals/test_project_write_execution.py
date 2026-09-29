"""普通项目许可到文件副作用的真实PG闭环。"""
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy.orm import sessionmaker
from app.services.workspace.proposals import file_edit_proposal_application as application
from app.services.workspace.proposals import file_edit_proposal_execution as execution
from app.services.workspace.proposals.project_write_grants import ProjectWriteGrantService
from tests.workspace.proposals.test_project_write_grants import grants, ready, setup, saved, root, target, database
__all__ = ['database', 'grants', 'ready', 'root', 'saved', 'setup', 'target']

@pytest.fixture(autouse=True)
def execution_database(engine, setup, monkeypatch):
    factory = sessionmaker(bind=engine, class_=setup[3])
    monkeypatch.setattr(application, 'SessionLocal', factory)
    monkeypatch.setattr(execution, 'SessionLocal', factory)


def test_real_project_apply_once(grants, ready):
    scope, file, _ = ready
    original = file.read_bytes()
    grant = grants.issue(**scope)
    receipt = grants.apply(**scope, grant_id=grant.grant_id, revision=1)
    assert receipt.application_status == 'applied'
    assert receipt.file_status == 'replaced'
    assert file.read_bytes() == original.replace(b'old', b'new')
    with pytest.raises(ValueError):
        grants.apply(**scope, grant_id=grant.grant_id, revision=1)


@pytest.mark.parametrize('kind', ['revoked', 'revision', 'grant', 'restart', 'changed'])
def test_invalid_grant_never_writes(grants, ready, kind):
    scope, file, _ = ready
    grant = grants.issue(**scope)
    if kind == 'revoked':
        grants.revoke(**scope, grant_id=grant.grant_id, revision=1)
    if kind == 'changed':
        file.write_text('external edit')
    before = file.read_bytes()
    service = ProjectWriteGrantService() if kind == 'restart' else grants
    try:
        result = service.apply(**scope, grant_id='f' * 32 if kind == 'grant' else grant.grant_id,
                               revision=2 if kind == 'revision' else 1)
        assert result.file_status == 'not_attempted'
    except ValueError:
        pass
    assert file.read_bytes() == before


def test_concurrent_apply_has_one_side_effect(grants, ready, monkeypatch):
    scope, _file, _ = ready
    grant = grants.issue(**scope)
    calls = []
    replace = execution.replace_workspace_text_file
    def counted(**kw):
        calls.append(1)
        return replace(**kw)
    monkeypatch.setattr(execution, 'replace_workspace_text_file', counted)
    def run():
        try:
            return grants.apply(**scope, grant_id=grant.grant_id, revision=1)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert calls == [1]
    assert sum(r is not None and r.application_status == 'applied' for r in results) == 1


def test_external_change_after_claim_is_preserved(grants, ready, monkeypatch):
    scope, file, _ = ready
    grant = grants.issue(**scope)
    preflight = execution.check_task_file_edit_proposal
    def changed(**kw):
        result = preflight(**kw)
        file.write_text('external edit')
        return result
    monkeypatch.setattr(execution, 'check_task_file_edit_proposal', changed)
    result = grants.apply(**scope, grant_id=grant.grant_id, revision=1)
    assert result.application_status == 'not_applied'
    assert file.read_text() == 'external edit'


def test_registration_failure_never_reexecutes(grants, ready, monkeypatch):
    scope, file, _ = ready
    grant = grants.issue(**scope)
    def failed(**kw):
        raise OSError('lost receipt')
    monkeypatch.setattr(execution, 'finish_task_file_edit_proposal', failed)
    result = grants.apply(**scope, grant_id=grant.grant_id, revision=1)
    assert result.application_status == 'unknown' and result.file_status == 'replaced'
    before = file.read_bytes()
    with pytest.raises(ValueError):
        grants.apply(**scope, grant_id=grant.grant_id, revision=1)
    assert file.read_bytes() == before


def test_backup_and_audit_committed_before_write(grants, ready, engine, monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.models import FileEditProposal, ProposalAuditEvent
    scope, file, _ = ready
    original = file.read_bytes().decode('utf-8')
    grant = grants.issue(**scope)
    replace = execution.replace_workspace_text_file
    def checked(**kw):
        with Session(engine) as session:
            assert session.scalar(select(FileEditProposal.baseline_content)) == original
            assert 'application_started' in session.scalars(select(ProposalAuditEvent.event)).all()
        return replace(**kw)
    monkeypatch.setattr(execution, 'replace_workspace_text_file', checked)
    assert grants.apply(**scope, grant_id=grant.grant_id, revision=1).application_status == 'applied'
    with Session(engine) as session:
        assert session.scalars(select(ProposalAuditEvent.event).order_by(ProposalAuditEvent.id)).all() == [
            'created', 'grant_issued', 'application_started', 'applied',
        ]


def test_same_bytes_replaced_inode_is_not_authorized(grants, ready, monkeypatch):
    scope, file, _ = ready
    grant = grants.issue(**scope)
    preflight = execution.check_task_file_edit_proposal
    def changed(**kw):
        result = preflight(**kw)
        replacement = file.with_suffix('.replacement')
        replacement.write_bytes(file.read_bytes())
        replacement.replace(file)
        return result
    monkeypatch.setattr(execution, 'check_task_file_edit_proposal', changed)
    before = file.read_bytes()
    assert grants.apply(**scope, grant_id=grant.grant_id, revision=1).application_status == 'not_applied'
    assert file.read_bytes() == before
