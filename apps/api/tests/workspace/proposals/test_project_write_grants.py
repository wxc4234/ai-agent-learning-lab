"""隔离PostgreSQL验证许可事务；真实只读快照，不执行项目替换。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal, ProjectWriteGrantRecord, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.proposals import project_write_grants as service
from app.services.workspace.proposals import project_write_snapshot as snapshots
from tests.assertions import require_value
from tests.workspace.proposals.test_file_edit_proposal_preflight import ready, setup, saved, root, target, database

__all__ = ['database', 'ready', 'root', 'saved', 'setup', 'target']


@pytest.fixture
def grants(ready, setup, engine, monkeypatch):
    factory = sessionmaker(bind=engine, class_=setup[3])
    monkeypatch.setattr(service, 'SessionLocal', factory)
    monkeypatch.setattr(snapshots, 'SessionLocal', factory)
    return service.ProjectWriteGrantService()


def test_issue_read_revoke_and_no_reissue(grants, ready, engine):
    scope, file, _ = ready
    before = file.read_bytes(), file.stat().st_mtime_ns
    assert grants.read(**scope) is None  # approved本身不生成许可。
    grant = grants.issue(**scope)
    assert grant.enabled and grant.revision == 1
    assert service.ProjectWriteGrantService().read(**scope) == grant
    assert str(file) not in grant.model_dump_json()
    with pytest.raises(service.ProjectWriteGrantError):
        grants.issue(**scope)
    revoked = grants.revoke(**scope, grant_id=grant.grant_id, revision=1)
    assert not revoked.enabled and revoked.revision == 2
    assert revoked.target == grant.target and revoked != grant
    assert grants.read(**scope) == revoked
    with pytest.raises(service.ProjectWriteGrantError):
        grants.issue(**scope)
    with pytest.raises(service.ProjectWriteGrantError):
        grants.revoke(**scope, grant_id=grant.grant_id, revision=1)
    with Session(engine) as session:
        assert require_value(session.scalar(select(FileEditProposal))).application_status == 'idle'
    assert (file.read_bytes(), file.stat().st_mtime_ns) == before


@pytest.mark.parametrize('operation', ['issue', 'read', 'revoke'])
@pytest.mark.parametrize('field', ['user_id', 'workspace_id', 'task_id', 'proposal_id'])
def test_every_operation_reauthorizes(grants, ready, operation, field):
    grant = grants.issue(**ready[0])
    scope = dict(ready[0])
    scope[field] = 999999 if field == 'user_id' else 'f' * 32
    args = {'grant_id': grant.grant_id, 'revision': 1} if operation == 'revoke' else {}
    with pytest.raises(WorkspaceNotAccessibleError):
        getattr(grants, operation)(**scope, **args)
    assert grants.read(**ready[0]) == grant


@pytest.mark.parametrize('status', ['running', 'applied', 'not_applied', 'uncertain'])
def test_consumed_application_cannot_gain_grant(grants, ready, engine, status):
    with Session(engine) as session, session.begin():
        proposal = require_value(session.scalar(select(FileEditProposal)))
        proposal.application_status = status
        proposal.application_token = 'a' * 32
    with pytest.raises(service.ProjectWriteGrantError):
        grants.issue(**ready[0])
    assert grants.read(**ready[0]) is None


@pytest.mark.parametrize('change', ['revision', 'content', 'consumed', 'approval', 'owner'])
def test_locked_recheck_after_snapshot(grants, ready, engine, monkeypatch, change):
    read = grants._reader.read
    def changed(**scope):
        result = read(**scope)
        with Session(engine) as session, session.begin():
            proposal = require_value(session.scalar(select(FileEditProposal)))
            workspace = require_value(session.scalar(select(Workspace)))
            if change == 'revision':
                workspace.binding_revision += 2
            elif change == 'owner':
                workspace.user_id += 1
            elif change == 'content':
                proposal.proposed_content = 'bad content'
            elif change == 'approval':
                proposal.status = 'rejected'
            else:
                proposal.application_status = 'uncertain'
                proposal.application_token = 'b' * 32
        return result
    monkeypatch.setattr(grants._reader, 'read', changed)
    with pytest.raises((service.ProjectWriteGrantError, WorkspaceNotAccessibleError)):
        grants.issue(**ready[0])
    with Session(engine) as session:
        assert session.scalar(select(ProjectWriteGrantRecord)) is None


def test_revoke_without_files_and_without_restoring_consumed_attempt(grants, ready, engine, monkeypatch):
    grant = grants.issue(**ready[0])
    ready[1].unlink()
    with Session(engine) as session, session.begin():
        proposal = require_value(session.scalar(select(FileEditProposal)))
        proposal.application_status = 'uncertain'
        proposal.application_token = 'c' * 32
    monkeypatch.setattr(grants._reader, 'read', lambda **kw: pytest.fail('revoke/read need no file'))
    assert not grants.revoke(**ready[0], grant_id=grant.grant_id, revision=1).enabled
    assert not require_value(grants.read(**ready[0])).enabled
    with Session(engine) as session:
        assert require_value(session.scalar(select(FileEditProposal))).application_status == 'uncertain'


@pytest.mark.parametrize('grant_id,revision', [('f' * 32, 1), (None, 1), ('same', 2), ('same', True)])
def test_stale_or_invalid_revoke_is_noop(grants, ready, grant_id, revision):
    grant = grants.issue(**ready[0])
    with pytest.raises(service.ProjectWriteGrantError):
        grants.revoke(**ready[0], grant_id=grant.grant_id if grant_id == 'same' else grant_id, revision=revision)
    assert grants.read(**ready[0]) == grant


@pytest.mark.parametrize('operation', ['issue', 'revoke'])
@pytest.mark.parametrize('committed', [False, True])
def test_unknown_commit_is_not_retried(grants, ready, engine, monkeypatch, operation, committed):
    grant = grants.issue(**ready[0]) if operation == 'revoke' else None
    class FailingCommit(Session):
        def commit(self):
            # 模拟提交前失败或服务端已提交但确认丢失。
            if committed:
                super().commit()
            raise OSError('private database message')
    calls = []
    from contextlib import contextmanager
    class Factory:
        @staticmethod
        @contextmanager
        def begin():
            calls.append(1)
            with FailingCommit(bind=engine) as session:
                try:
                    yield session
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise
    with monkeypatch.context() as patch:
        patch.setattr(service, 'SessionLocal', Factory)
        with pytest.raises(service.ProjectWriteGrantError, match='^project_write_grant_unavailable$'):
            if grant:
                grants.revoke(**ready[0], grant_id=grant.grant_id, revision=1)
            else:
                grants.issue(**ready[0])
    assert calls == [1]
    observed = grants.read(**ready[0])
    if operation == 'issue':
        assert (observed is not None) == committed
        if committed:
            with pytest.raises(service.ProjectWriteGrantError):
                grants.issue(**ready[0])
    else:
        assert require_value(observed).enabled is not committed


def test_concurrent_issue_has_single_winner(grants, ready):
    def issue():
        try:
            return grants.issue(**ready[0])
        except service.ProjectWriteGrantError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: issue(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert grants.read(**ready[0]) in results


def test_corrupt_persisted_target_is_unknown(grants, ready, engine):
    grants.issue(**ready[0])
    with Session(engine) as session, session.begin():
        record = require_value(session.scalar(select(ProjectWriteGrantRecord)))
        record.target = {**record.target, 'user_id': 999999}
    with pytest.raises(service.ProjectWriteGrantError):
        grants.read(**ready[0])


def test_file_io_finishes_before_grant_transaction(grants, ready, monkeypatch):
    from app.services.workspace.files import project_file_observation as observation
    verify = observation._verify_original
    def checked(*args):
        assert all(session.closed and not session.in_transaction() for session in ready[2])
        return verify(*args)
    monkeypatch.setattr(observation, '_verify_original', checked)
    grants.issue(**ready[0])


def test_old_confirmation_denied_after_persisted_revocation(grants, ready):
    from app.services.workspace.proposals.project_write_policy import ProjectWriteFacts, evaluate_project_write_policy
    original = grants.issue(**ready[0])
    current = grants.revoke(**ready[0], grant_id=original.grant_id, revision=1)
    facts = ProjectWriteFacts(
        target=original.target, observed_target=original.target,
        confirmed_grant=original, current_grant=current, authorized=True, apply_requested=True,
        proposal_status='approved', application_status='idle', diff_complete=True,
        current_sha256=original.target.baseline_sha256, candidate_sha256=original.target.proposed_sha256,
        filesystem_checked=True, platform_supported=True, exclusive_access_confirmed=True,
    )
    # 其余条件均由测试设为真，仅隔离验证数据库撤销使旧确认失效。
    assert not evaluate_project_write_policy(facts).eligible


def test_concurrent_revoke_has_single_winner(grants, ready):
    grant = grants.issue(**ready[0])
    def revoke():
        try:
            return grants.revoke(**ready[0], grant_id=grant.grant_id, revision=1)
        except service.ProjectWriteGrantError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: revoke(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert require_value(grants.read(**ready[0])).revision == 2


def test_snapshot_failure_never_inserts(grants, ready, engine):
    ready[1].write_text('external edit')
    with pytest.raises(service.ProjectWriteGrantError):
        grants.issue(**ready[0])
    with Session(engine) as session:
        assert session.scalar(select(ProjectWriteGrantRecord)) is None
