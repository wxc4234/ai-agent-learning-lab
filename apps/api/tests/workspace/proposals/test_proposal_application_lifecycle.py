"""真实服务串联、隔离 PostgreSQL 与自有临时文件；不修改审批数据库状态。"""

import os
import pwd
import subprocess

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.services.workspace.proposals import file_edit_proposal_application as application
from app.services.workspace.proposals import file_edit_proposal_application_query as query
from app.services.workspace.proposals import file_edit_proposal_decision as decision
from app.services.workspace.proposals import file_edit_proposal_execution as execution
from app.services.workspace.proposals import file_edit_proposal_preflight as preflight
from app.services.workspace.proposals import file_edit_proposal_service as proposals
from app.services.workspace.metadata.workspace_file_metadata import read_file_metadata
from tests.workspace.proposals import test_file_edit_proposal_service as fixtures
from tests.workspace.metadata.test_workspace_metadata_xattrs import set_attr

root = fixtures.root
target = fixtures.target
database = fixtures.database
setup = fixtures.setup


@pytest.fixture
def scenario(setup, engine, monkeypatch):
    # 所有独立事务都连接本轮私有 schema，退出时由共用夹具清理。
    monkeypatch.setattr(settings, 'app_mode', 'local')
    factory = sessionmaker(bind=engine, class_=setup[3], expire_on_commit=False)
    for module in (application, query, decision, execution, preflight):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    return setup


def record(identity):
    return query.get_task_file_edit_proposal_application_status(**identity).application_status


def create(scenario):
    args, file, _, _ = scenario
    before = file.read_bytes()
    created = proposals.create_task_file_edit_proposal(**args)
    identity = {key: args[key] for key in ('user_id', 'workspace_id', 'task_id')}
    identity['proposal_id'] = created.proposal_id
    assert proposals.get_task_file_edit_proposal(**identity).status == 'pending'
    assert record(identity) == 'idle'
    assert file.read_bytes() == before
    return identity


def approve(identity, file):
    before = file.read_bytes()
    result = decision.decide_task_file_edit_proposal(**identity, decision='approved')
    assert result.status == 'approved'
    assert proposals.get_task_file_edit_proposal(**identity).status == 'approved'
    assert record(identity) == 'idle' and file.read_bytes() == before


def no_temps(file):
    assert not list(file.parent.glob('.agent-edit-*.tmp'))


@pytest.mark.parametrize('with_acl', [False, True])
def test_real_lifecycle_and_second_execution_cannot_write(scenario, monkeypatch, with_acl):
    _, file, sessions, _ = scenario
    os.chmod(file, 0o640)
    if with_acl:
        user = pwd.getpwuid(os.getuid()).pw_name
        subprocess.run(['chmod', '+a', f'{user} allow read', str(file)], check=True)
    with file.open('rb') as stream:
        set_attr(stream.fileno())
        set_attr(stream.fileno(), b'com.example.empty', b'')
        metadata = read_file_metadata(stream.fileno())
    original = file.read_bytes()
    original_ino = file.stat().st_ino
    identity = create(scenario)
    approve(identity, file)
    writer = execution.replace_workspace_text_file
    calls = []
    def tracked(**kwargs):
        # 数据库事务在文件 I/O 前已结束，领取的提交结果可由新会话观察。
        assert all(session.closed and not session.in_transaction() for session in sessions)
        assert record(identity) == 'running'
        calls.append(1)
        return writer(**kwargs)
    monkeypatch.setattr(execution, 'replace_workspace_text_file', tracked)
    receipt = execution.execute_task_file_edit_proposal(**identity)
    assert (receipt.file_status, receipt.application_status, receipt.cleanup_complete) == ('replaced', 'applied', True)
    assert record(identity) == 'applied'
    assert proposals.get_task_file_edit_proposal(**identity).status == 'approved'
    assert file.read_bytes() == original.replace(b'old', b'new')
    assert file.stat().st_ino != original_ino
    with file.open('rb') as stream:
        # 不过滤 provenance，比较完整可见元数据。
        assert read_file_metadata(stream.fileno()) == metadata
    no_temps(file)
    stable = (file.read_bytes(), file.stat().st_ino, file.stat().st_ctime_ns)
    again = execution.execute_task_file_edit_proposal(**identity)
    assert again.file_status == 'not_attempted'
    assert again.application_status == 'unknown'
    assert record(identity) == 'applied' and len(calls) == 1
    assert (file.read_bytes(), file.stat().st_ino, file.stat().st_ctime_ns) == stable


def test_approved_stale_baseline_preserves_external_edit(scenario, monkeypatch):
    file = scenario[1]
    identity = create(scenario)
    approve(identity, file)
    file.write_bytes(b'external edit')
    monkeypatch.setattr(execution, 'replace_workspace_text_file', lambda **kwargs: pytest.fail('stale baseline reached writer'))
    receipt = execution.execute_task_file_edit_proposal(**identity)
    assert receipt.file_status == 'not_attempted'
    assert receipt.application_status == record(identity) == 'not_applied'
    assert file.read_bytes() == b'external edit'
    assert proposals.get_task_file_edit_proposal(**identity).status == 'approved'
    no_temps(file)
    assert execution.execute_task_file_edit_proposal(**identity).file_status == 'not_attempted'
    assert record(identity) == 'not_applied'


@pytest.mark.parametrize('timing,stored', [('before_commit', 'running'), ('after_commit', 'applied')])
def test_file_success_and_registration_confirmation_are_separate(scenario, monkeypatch, timing, stored):
    _, file, _, session_class = scenario
    identity = create(scenario)
    approve(identity, file)
    original = file.read_bytes()
    finish = execution.finish_task_file_edit_proposal
    calls = []
    def lost(*args):
        raise RuntimeError('private registration failure')
    def register(**kwargs):
        calls.append(1)
        # 仅向终态登记事务注入故障；创建、审批和领取均真实提交。
        event.listen(session_class, timing, lost)
        try:
            return finish(**kwargs)
        finally:
            event.remove(session_class, timing, lost)
    monkeypatch.setattr(execution, 'finish_task_file_edit_proposal', register)
    receipt = execution.execute_task_file_edit_proposal(**identity)
    assert receipt.file_status == 'replaced' and receipt.application_status == 'unknown'
    assert receipt.cleanup_complete is True and len(calls) == 1
    assert file.read_bytes() == original.replace(b'old', b'new')
    assert record(identity) == stored
    assert 'private' not in repr(receipt)
    no_temps(file)
    # 查询不会释放占用；再次显式调用也不能第二次进入写入器。
    monkeypatch.setattr(execution, 'replace_workspace_text_file', lambda **kwargs: pytest.fail('must not retry file write'))
    assert execution.execute_task_file_edit_proposal(**identity).file_status == 'not_attempted'
    assert record(identity) == stored and len(calls) == 1


@pytest.mark.parametrize('approval', ['pending', 'rejected'])
def test_unapproved_lifecycle_never_enters_writer(scenario, monkeypatch, approval):
    file = scenario[1]
    identity = create(scenario)
    if approval == 'rejected':
        decision.decide_task_file_edit_proposal(**identity, decision='rejected')
    before = file.read_bytes()
    monkeypatch.setattr(execution, 'replace_workspace_text_file', lambda **kwargs: pytest.fail('unapproved write'))
    receipt = execution.execute_task_file_edit_proposal(**identity)
    assert receipt.file_status == 'not_attempted'
    assert record(identity) == 'idle'
    assert proposals.get_task_file_edit_proposal(**identity).status == approval
    assert file.read_bytes() == before
    no_temps(file)
