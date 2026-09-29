"""纯计划测试：不连接数据库、读取用户目录或运行Git。"""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from app.services.workspace.git import project_status_plan as plans
from app.services.workspace.git.status_parser import MAX_STATUS_BYTES, parse_git_status


def request() -> dict[str, object]:
    return {'plan_id': 'project_git_status_v1', 'user_id': 1, 'workspace_id': 'a' * 32,
            'task_id': 'b' * 32, 'binding_revision': 1}


def test_fixed_plan_preserves_reference_without_host_paths_or_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure planning must not access filesystem or start processes')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(Path, 'stat', forbidden)
    plan = plans.build_project_git_status_plan(request())
    assert plan.request.workspace_id == 'a' * 32
    assert plan.request.binding_revision == 1
    assert plan.argv[0] == '/usr/bin/git'
    assert plan.working_directory == '/workspace'
    assert plan.argv[-6:] == ('status', '--porcelain=v1', '-z', '--untracked-files=all',
                              '--ignore-submodules=all', '--no-renames')
    assert '--git-dir=/workspace/.git' in plan.argv
    assert '--work-tree=/workspace' in plan.argv
    assert plan.max_stdout_bytes == MAX_STATUS_BYTES
    assert plan.max_stderr_bytes == 16 * 1024
    assert plan.timeout_seconds == 5.0
    assert plan.result_scope == 'isolated_snapshot_without_submodules'
    assert 'validate_self_contained_repository_metadata_and_config' in plan.required_controls
    assert 'independent_read_only_snapshot_no_host_project_mount' in plan.required_controls
    assert 'reauthorize_task_workspace_and_binding_revision' in plan.required_controls
    assert not hasattr(plan, 'ready')


@pytest.mark.parametrize('key,value', [
    ('plan_id', 'status'), ('plan_id', None), ('user_id', True), ('user_id', 0),
    ('user_id', '1'), ('user_id', 2**63), ('workspace_id', 'A' * 32),
    ('workspace_id', '../private'), ('workspace_id', 'a' * 32 + '\n'),
    ('workspace_id', b'a' * 32), ('task_id', ''), ('task_id', 'b' * 33),
    ('binding_revision', False), ('binding_revision', 0), ('binding_revision', -1),
    ('binding_revision', 1.0), ('binding_revision', '1'), ('binding_revision', 2**63),
    ('root', '/private/secret'), ('argv', ['sh', '-c', 'touch marker']),
    ('env', {'GIT_CONFIG_COUNT': '1'}), ('timeout_seconds', 1000),
    ('authorized', True), ('required_controls', []),
])
def test_invalid_values_and_policy_overrides_rejected_without_echo(key, value):
    raw = request()
    raw[key] = value
    with pytest.raises(plans.ProjectGitStatusPlanError) as error:
        plans.build_project_git_status_plan(raw)
    assert error.value.code == 'project_git_status_plan_invalid'
    assert str(error.value) == '普通项目Git状态计划参数无效'


@pytest.mark.parametrize('missing', tuple(request()))
def test_every_reference_field_required(missing):
    raw = request()
    del raw[missing]
    with pytest.raises(plans.ProjectGitStatusPlanError):
        plans.build_project_git_status_plan(raw)


@pytest.mark.parametrize('raw', [None, [], 'project_git_status_v1', True])
def test_non_object_rejected(raw):
    with pytest.raises(plans.ProjectGitStatusPlanError):
        plans.build_project_git_status_plan(raw)


def test_no_inherited_environment_or_mutable_policy_leak(monkeypatch):
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_CONFIG_COUNT', 'GIT_INDEX_FILE', 'LD_PRELOAD', 'SSH_AUTH_SOCK'):
        monkeypatch.setenv(key, 'private-secret')
    monkeypatch.setenv('PATH', '/untrusted')
    plan = plans.build_project_git_status_plan(request())
    environment = dict(plan.environment)
    assert 'private-secret' not in repr(plan)
    assert environment['PATH'] == '/usr/bin:/bin'
    assert environment['GIT_ALLOW_PROTOCOL'] == ''
    assert environment['GIT_OPTIONAL_LOCKS'] == '0'
    assert environment['GIT_CONFIG_GLOBAL'] == '/dev/null'
    environment['GIT_DIR'] = '/untrusted'
    assert 'GIT_DIR' not in dict(plans.build_project_git_status_plan(request()).environment)
    assert os.environ['GIT_DIR'] == 'private-secret'
    pytest.raises(FrozenInstanceError, setattr, plan, 'argv', ('sh',))
    with pytest.raises(ValidationError):
        plan.request.binding_revision = 2


def test_revalidates_constructed_or_copied_models():
    reference = plans.ProjectGitStatusRequest.model_validate(request())
    assert plans.build_project_git_status_plan(reference).request == reference
    invalid = reference.model_copy(update={'binding_revision': -1})
    with pytest.raises(plans.ProjectGitStatusPlanError):
        plans.build_project_git_status_plan(invalid)


def test_reference_is_snapshot_and_max_revision_not_incremented():
    raw = request()
    raw['binding_revision'] = 2**63 - 1
    plan = plans.build_project_git_status_plan(raw)
    raw['workspace_id'] = 'c' * 32
    assert plan.request.workspace_id == 'a' * 32
    assert plan.request.binding_revision == 2**63 - 1


def test_output_contract_matches_existing_parser_without_rename_inference():
    # 固定协议可被现有解析器消费；模拟字节不证明Git或沙箱已实际执行。
    snapshot = parse_git_status(b' D old.txt\0?? new name.txt\0 M changed.txt\0', source_truncated=False)
    assert [entry.path for entry in snapshot.entries] == ['old.txt', 'new name.txt', 'changed.txt']
    assert all(entry.original_path is None for entry in snapshot.entries)
