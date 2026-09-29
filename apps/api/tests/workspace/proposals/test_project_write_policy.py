"""策略契约只消费可信宿主快照；不创建数据库、目录或执行资源。"""

from hashlib import sha256
from typing import cast

import pytest
from pydantic import ValidationError

from app.services.workspace.proposals.project_write_policy import (
    ProjectWriteDecision, ProjectWriteFacts, ProjectWriteGrant, ProjectWriteTarget,
    evaluate_project_write_policy,
)


@pytest.fixture
def facts() -> ProjectWriteFacts:
    target = ProjectWriteTarget.model_validate({
        'runtime_id': 'a' * 32, 'user_id': 1, 'workspace_id': 2, 'task_id': 3,
        'conversation_id': 4, 'proposal_id': 5, 'binding_revision': 1,
        'root_identity': {'device': 0, 'inode': 10},
        'file_identity': {'device': 0, 'inode': 20}, 'relative_path': 'src/example.txt',
        'baseline_sha256': sha256(b'old\n').hexdigest(),
        'proposed_sha256': sha256(b'new\n').hexdigest(),
    })
    grant = ProjectWriteGrant(grant_id='b' * 32, revision=1, target=target, enabled=True)
    return ProjectWriteFacts(
        target=target, observed_target=target, confirmed_grant=grant, current_grant=grant,
        authorized=True, apply_requested=True, proposal_status='approved', application_status='idle',
        diff_complete=True, current_sha256=target.baseline_sha256,
        candidate_sha256=target.proposed_sha256, filesystem_checked=True,
        platform_supported=True,
    )


def test_complete_facts_are_eligible_without_executing(facts, monkeypatch):
    import os
    from pathlib import Path
    import subprocess
    from app.services.workspace.directory import workspace_path

    def forbidden(*args, **kwargs):
        pytest.fail('pure policy must not acquire resources')
    monkeypatch.setattr(workspace_path, 'SessionLocal', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    before = facts.model_dump()
    result = evaluate_project_write_policy(facts)
    assert result == ProjectWriteDecision('eligible') and result.eligible
    assert facts.model_dump() == before
    assert 'example.txt' not in repr(result) and facts.target.baseline_sha256 not in repr(result)


def test_default_facts_and_default_grant_deny(facts):
    assert not evaluate_project_write_policy(ProjectWriteFacts(target=facts.target)).eligible
    grant = ProjectWriteGrant(grant_id='b' * 32, revision=1, target=facts.target)
    value = facts.model_copy(update={'confirmed_grant': grant, 'current_grant': grant})
    assert evaluate_project_write_policy(value).code == 'grant_revoked'


@pytest.mark.parametrize(('field', 'value', 'code'), [
    ('authorized', False, 'not_authorized'),
    ('apply_requested', False, 'apply_not_requested'),
    ('confirmed_grant', None, 'grant_missing'),
    ('current_grant', None, 'grant_missing'),
    ('observed_target', None, 'target_changed'),
    ('diff_complete', False, 'diff_incomplete'),
    ('current_sha256', None, 'baseline_changed'),
    ('current_sha256', 'c' * 64, 'baseline_changed'),
    ('candidate_sha256', None, 'candidate_changed'),
    ('candidate_sha256', 'c' * 64, 'candidate_changed'),
    ('filesystem_checked', False, 'filesystem_unconfirmed'),
    ('platform_supported', False, 'platform_unsupported'),
])
def test_each_necessary_fact_denies_when_missing_or_changed(facts, field, value, code):
    result = evaluate_project_write_policy(facts.model_copy(update={field: value}))
    assert result.code == code and not result.eligible


@pytest.mark.parametrize('status', ['pending', 'rejected', 'unknown'])
def test_write_permission_does_not_approve_proposal(facts, status):
    assert evaluate_project_write_policy(facts.model_copy(update={'proposal_status': status})).code == 'proposal_not_approved'


@pytest.mark.parametrize('status', ['running', 'applied', 'not_applied', 'uncertain', 'unknown'])
def test_new_permission_never_restores_consumed_or_unknown_attempt(facts, status):
    grant = ProjectWriteGrant(grant_id='d' * 32, revision=2, target=facts.target, enabled=True)
    value = facts.model_copy(update={
        'confirmed_grant': grant, 'current_grant': grant, 'application_status': status,
    })
    assert evaluate_project_write_policy(value).code == 'application_not_idle'


@pytest.mark.parametrize(('change', 'code'), [
    ({'enabled': False}, 'grant_revoked'),
    ({'revision': 2}, 'grant_changed'),
    ({'grant_id': 'd' * 32}, 'grant_changed'),
])
def test_revocation_regrant_and_replacement_require_new_confirmation(facts, change, code):
    assert facts.current_grant is not None
    latest = facts.current_grant.model_copy(update=change)
    assert evaluate_project_write_policy(facts.model_copy(update={'current_grant': latest})).code == code


@pytest.mark.parametrize(('field', 'replacement'), [
    ('runtime_id', 'e' * 32), ('user_id', 11), ('workspace_id', 12), ('task_id', 13),
    ('conversation_id', 14), ('proposal_id', 15), ('binding_revision', 2),
    ('root_identity', {'device': 1, 'inode': 10}),
    ('root_identity', {'device': 0, 'inode': 11}),
    ('file_identity', {'device': 0, 'inode': 21}),
    ('relative_path', 'src/other.txt'),
    ('baseline_sha256', 'c' * 64), ('proposed_sha256', 'd' * 64),
])
def test_same_external_name_or_bytes_cannot_transfer_permission(facts, field, replacement):
    changed = ProjectWriteTarget.model_validate(facts.target.model_dump() | {field: replacement})
    assert evaluate_project_write_policy(facts.model_copy(update={'observed_target': changed})).code == 'target_changed'
    # 即使确认快照与当前许可都一致，也不能借另一份提案的许可执行当前目标。
    grant = ProjectWriteGrant(grant_id='b' * 32, revision=1, target=changed, enabled=True)
    assert evaluate_project_write_policy(facts.model_copy(update={
        'confirmed_grant': grant, 'current_grant': grant,
    })).code == 'target_changed'


@pytest.mark.parametrize('path', ['', '.', './a.txt', 'a//b', '../a', 'a/../b', '/a',
                                  'C:a', 'a\\b', 'NUL', 'a\x00b', 'a\nb', 'a.' ])
def test_invalid_or_noncanonical_path_never_becomes_target(facts, path):
    with pytest.raises(ValidationError):
        ProjectWriteTarget.model_validate(facts.target.model_dump() | {'relative_path': path})


@pytest.mark.parametrize(('field', 'value'), [
    ('authorized', 'true'), ('apply_requested', 1), ('application_status', 'retry'),
    ('proposal_status', 'auto_approved'), ('current_sha256', 'A' * 64),
    ('candidate_sha256', 'f' * 64 + '\n'), ('exclusive_access_confirmed', None),
])
def test_construct_and_copy_cannot_bypass_validation(facts, field, value):
    invalid = facts.model_copy(update={field: value})
    assert evaluate_project_write_policy(invalid).code == 'invalid_facts'


def test_nested_tampering_unknown_fields_and_frozen_models(facts):
    bad_target = facts.target.model_copy(update={'user_id': True})
    assert evaluate_project_write_policy(facts.model_copy(update={'target': bad_target})).code == 'invalid_facts'
    assert evaluate_project_write_policy(ProjectWriteFacts.model_construct()).code == 'invalid_facts'
    assert evaluate_project_write_policy(cast(ProjectWriteFacts, {})).code == 'invalid_facts'
    with pytest.raises(ValidationError):
        ProjectWriteFacts.model_validate(facts.model_dump() | {'skip_checks': True})
    with pytest.raises(ValidationError):
        facts.target.relative_path = 'other.txt'


def test_old_eligible_result_does_not_override_current_revocation(facts):
    old = evaluate_project_write_policy(facts)
    assert facts.current_grant is not None
    revoked = facts.current_grant.model_copy(update={'enabled': False, 'revision': 2})
    now = evaluate_project_write_policy(facts.model_copy(update={'current_grant': revoked}))
    assert old.eligible and not now.eligible
