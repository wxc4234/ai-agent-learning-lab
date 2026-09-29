"""真实PG和临时文件验证可信事实装配；无项目写入/应用领取。"""

from dataclasses import asdict

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FileEditProposal, ProjectWriteGrantRecord, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.files import project_file_observation as observation
from app.services.workspace.proposals import project_write_grants as service
from tests.assertions import require_value
from tests.workspace.proposals.test_project_write_grants import grants, ready, setup, saved, root, target, database

__all__ = ['database', 'grants', 'ready', 'root', 'saved', 'setup', 'target']


@pytest.fixture
def assessment_args(grants, ready):
    grant = grants.issue(**ready[0])
    return {**ready[0], 'grant_id': grant.grant_id, 'revision': grant.revision, 'apply_requested': True}


def test_valid_target_builds_verified_facts_without_executing(grants, assessment_args, ready, engine, monkeypatch):
    file = ready[1]
    before = file.read_bytes(), file.stat().st_mtime_ns
    evaluate = service.evaluate_project_write_policy
    facts = []
    def capture(value):
        facts.append(value)
        return evaluate(value)
    monkeypatch.setattr(service, 'evaluate_project_write_policy', capture)
    result = grants.assess(**assessment_args)
    assert asdict(result) == {'code': 'eligible'}
    assert result.eligible
    assert len(facts) == 1
    value = facts[0]
    assert value.target == value.observed_target
    assert value.confirmed_grant == value.current_grant
    assert value.authorized and value.filesystem_checked and value.platform_supported
    assert value.current_sha256 == value.target.baseline_sha256
    assert value.candidate_sha256 == value.target.proposed_sha256
    assert (file.read_bytes(), file.stat().st_mtime_ns) == before
    with Session(engine) as session:
        proposal = require_value(session.scalar(select(FileEditProposal)))
        assert (proposal.status, proposal.application_status, proposal.application_token) == ('approved', 'idle', None)
        assert require_value(session.scalar(select(ProjectWriteGrantRecord))).revision == 1


@pytest.mark.parametrize('field', ['user_id', 'workspace_id', 'task_id', 'proposal_id'])
def test_authorization_before_file_access(grants, assessment_args, monkeypatch, field):
    assessment_args[field] = 999999 if field == 'user_id' else 'f' * 32
    monkeypatch.setattr(grants._reader, 'read', lambda **kw: pytest.fail('must authorize first'))
    with pytest.raises(WorkspaceNotAccessibleError):
        grants.assess(**assessment_args)


@pytest.mark.parametrize('field,value', [
    ('apply_requested', 1), ('apply_requested', 'true'), ('revision', True),
    ('revision', 0), ('revision', '1'), ('grant_id', 'bad'), ('grant_id', None),
])
def test_invalid_intent_or_confirmation(grants, assessment_args, field, value):
    assessment_args[field] = value
    assert grants.assess(**assessment_args).code == 'invalid_facts'


@pytest.mark.parametrize('extra', ['authorized', 'target', 'current_sha256', 'exclusive_access_confirmed', 'runtime_id'])
def test_caller_cannot_supply_trusted_facts(grants, assessment_args, extra):
    with pytest.raises(TypeError):
        grants.assess(**assessment_args, **{extra: True})


@pytest.mark.parametrize('state,code', [
    ('intent', 'apply_not_requested'), ('missing', 'grant_missing'), ('revoked', 'grant_revoked'),
    ('id', 'grant_changed'), ('revision', 'grant_changed'), ('pending', 'proposal_not_approved'),
    ('rejected', 'proposal_not_approved'), ('running', 'application_not_idle'),
    ('applied', 'application_not_idle'), ('not_applied', 'application_not_idle'), ('uncertain', 'application_not_idle'),
])
def test_known_denials_need_no_files(grants, assessment_args, ready, engine, monkeypatch, state, code):
    with Session(engine) as session, session.begin():
        proposal = require_value(session.scalar(select(FileEditProposal)))
        record = require_value(session.scalar(select(ProjectWriteGrantRecord)))
        if state == 'intent':
            assessment_args['apply_requested'] = False
        elif state == 'missing':
            session.delete(record)
        elif state == 'revoked':
            record.enabled = False
            record.revision = 2
        elif state == 'id':
            assessment_args['grant_id'] = 'f' * 32
        elif state == 'revision':
            assessment_args['revision'] = 2
        elif state in ('pending', 'rejected'):
            proposal.status = state
        else:
            proposal.application_status = state
            proposal.application_token = 'a' * 32
    monkeypatch.setattr(grants._reader, 'read', lambda **kw: pytest.fail('no file IO'))
    assert grants.assess(**assessment_args).code == code


def test_new_host_does_not_restore_old_runtime(grants, assessment_args):
    assert service.ProjectWriteGrantService().assess(**assessment_args).code == 'target_changed'


@pytest.mark.parametrize('change', ['binding_revision', 'inode', 'root_inode'])
def test_changed_target_is_not_a_current_permission(grants, assessment_args, ready, engine, change):
    file = ready[1]
    if change == 'binding_revision':
        with Session(engine) as session, session.begin():
            require_value(session.scalar(select(Workspace))).binding_revision += 2
    elif change == 'inode':
        original = file.with_suffix('.saved')
        file.rename(original)
        file.write_bytes(original.read_bytes())
    else:
        root = file.parent.parent
        original = root.with_name(root.name + '-saved')
        root.rename(original)
        file.parent.mkdir(parents=True)
        file.write_bytes((original / 'src' / file.name).read_bytes())
    assert grants.assess(**assessment_args).code == 'target_changed'


@pytest.mark.parametrize('change', ['file', 'missing', 'candidate', 'corrupt_grant', 'platform'])
def test_unknown_observation_does_not_become_success_or_missing(grants, assessment_args, ready, engine, monkeypatch, change):
    if change == 'file':
        ready[1].write_text('external change')
    elif change == 'missing':
        ready[1].unlink()
    elif change == 'platform':
        def fail():
            raise OSError('private platform error')
        monkeypatch.setattr(observation, '_require_supported_platform', fail)
    else:
        with Session(engine) as session, session.begin():
            if change == 'candidate':
                require_value(session.scalar(select(FileEditProposal))).proposed_content = 'corrupt'
            else:
                require_value(session.scalar(select(ProjectWriteGrantRecord))).target = {}
    with pytest.raises(service.ProjectWriteAssessmentError, match='^project_write_assessment_unavailable$'):
        grants.assess(**assessment_args)


@pytest.mark.parametrize('change', ['revoke', 'consume', 'binding', 'owner', 'candidate'])
def test_final_database_recheck_after_file_observation(grants, assessment_args, ready, engine, monkeypatch, change):
    read = grants._reader.read
    def changed(**scope):
        result = read(**scope)
        with Session(engine) as session, session.begin():
            if change == 'revoke':
                record = require_value(session.scalar(select(ProjectWriteGrantRecord)))
                record.enabled = False
                record.revision = 2
            elif change == 'consume':
                proposal = require_value(session.scalar(select(FileEditProposal)))
                proposal.application_status = 'uncertain'
                proposal.application_token = 'b' * 32
            elif change == 'candidate':
                require_value(session.scalar(select(FileEditProposal))).proposed_content = 'changed'
            else:
                workspace = require_value(session.scalar(select(Workspace)))
                if change == 'binding':
                    workspace.binding_revision += 2
                else:
                    workspace.user_id += 1
        return result
    monkeypatch.setattr(grants._reader, 'read', changed)
    if change == 'revoke':
        assert grants.assess(**assessment_args).code == 'grant_revoked'
    else:
        with pytest.raises((service.ProjectWriteAssessmentError, WorkspaceNotAccessibleError)):
            grants.assess(**assessment_args)


def test_assessment_sql_is_readonly_and_file_io_has_no_transaction(grants, assessment_args, ready, database, monkeypatch):
    statements = database[1]
    statements.clear()
    verify = observation._verify_original
    def checked(*args):
        assert all(session.closed and not session.in_transaction() for session in ready[2])
        return verify(*args)
    monkeypatch.setattr(observation, '_verify_original', checked)
    grants.assess(**assessment_args)
    assert statements and all(sql.lstrip().lower().startswith('select') for sql in statements)


@pytest.mark.parametrize('call', [1, 2])
def test_database_failure_at_either_authorization_is_unknown(grants, assessment_args, monkeypatch, call):
    authorize = service._authorize
    calls = []
    def fail(session, scope):
        calls.append(1)
        if len(calls) == call:
            raise OSError('private database failure')
        return authorize(session, scope)
    monkeypatch.setattr(service, '_authorize', fail)
    with pytest.raises(service.ProjectWriteAssessmentError, match='^project_write_assessment_unavailable$'):
        grants.assess(**assessment_args)
    assert len(calls) == call  # 未知不自动重试。


def test_interruption_propagates_without_claiming_attempt(grants, assessment_args, engine, monkeypatch):
    def interrupt(**scope):
        raise KeyboardInterrupt()
    monkeypatch.setattr(grants._reader, 'read', interrupt)
    with pytest.raises(KeyboardInterrupt):
        grants.assess(**assessment_args)
    with Session(engine) as session:
        assert require_value(session.scalar(select(FileEditProposal))).application_status == 'idle'


def test_inherited_host_refuses_observation(grants, assessment_args):
    grants._reader._pid = -1  # 模拟fork继承宿主，不修改真实进程身份。
    with pytest.raises(service.ProjectWriteAssessmentError):
        grants.assess(**assessment_args)
