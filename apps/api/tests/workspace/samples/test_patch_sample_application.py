"""补丁提案经真实审批进入受限样例执行，不开放普通项目写入。"""

from difflib import unified_diff
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal, Workspace
from app.services.workspace.proposals import file_edit_proposal_decision as decision
from app.services.workspace.proposals import file_edit_proposal_service as proposals
from tests.workspace.samples import test_sample_proposal_execution as existing

setup = existing.setup
target = existing.target


@pytest.fixture
def sample(setup, engine, monkeypatch):
    bindings, scope, tracked, _ = setup
    factory = sessionmaker(bind=engine, class_=tracked)
    for module in (existing.gate, existing.execution, existing.application,
                   existing.preflight, proposals, decision, existing.workspace_path):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    bindings.bind(**scope)
    with Session(engine) as session:
        path = Path(session.scalar(select(Workspace.root_path)))
    return bindings, scope, path


def save(scope, before='old\n', after='new\n'):
    patch = ''.join(unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile='a/example.txt', tofile='b/example.txt', n=0,
    ))
    created = proposals.create_task_file_patch_proposal(
        **scope, relative_path='example.txt', patch=patch,
    )
    return {**scope, 'proposal_id': created.proposal_id}


@pytest.fixture
def ready(sample):
    bindings, scope, path = sample
    identity = save(scope)
    decision.decide_task_file_edit_proposal(**identity, decision='approved')
    return bindings, scope, identity, path


@pytest.mark.parametrize('before,after', [
    ('old\n', 'new\n'), ('\ufeffold\n', '\ufeffnew\n'), ('old\n', ''),
    ('', 'new\n'), ('a\nb\nc\nd\ne\n', 'A\nb\nc\nd\nE\n'),
])
def test_complete_patch_approval_application(sample, setup, engine, monkeypatch, before, after):
    bindings, scope, path = sample
    file = path / 'example.txt'
    file.write_bytes(before.encode())
    identity = save(scope, before, after)
    # 保存只持久化候选，批准也只更新决策；两个事务均不能提前写文件。
    assert file.read_bytes() == before.encode()
    assert existing.state(engine) == 'idle'
    decision.decide_task_file_edit_proposal(**identity, decision='approved')
    assert file.read_bytes() == before.encode()
    writer = existing.execution.replace_workspace_text_file
    calls = []

    def checked(**kwargs):
        assert all(session.closed and not session.in_transaction() for session in setup[3])
        assert existing.state(engine) == 'running'
        with pytest.raises(existing.TaskSampleBindingError):
            bindings.close(**scope)
        calls.append(kwargs)
        return writer(**kwargs)

    monkeypatch.setattr(existing.execution, 'replace_workspace_text_file', checked)
    receipt = existing.gate.execute_sample_proposal(bindings, **identity)
    assert receipt.file_status == 'replaced' and receipt.application_status == 'applied'
    assert receipt.cleanup_complete and existing.state(engine) == 'applied'
    assert file.read_bytes() == after.encode()
    assert len(calls) == 1 and calls[0]['proposed_content'] == after
    assert not list(path.glob('.agent-edit-*.tmp'))
    with Session(engine) as session:
        row = session.scalar(select(FileEditProposal))
        assert row.status == 'approved'  # 审批状态与应用状态不是同一个字段。
        assert row.proposed_content == after
        assert row.baseline_sha256 == sha256(before.encode()).hexdigest()
        assert row.proposed_sha256 == sha256(file.read_bytes()).hexdigest()
    bindings.close(**scope)
    assert not path.exists()


@pytest.mark.parametrize('kind', ['pending', 'rejected', 'truncated'])
def test_no_approval_never_enters_writer(sample, engine, monkeypatch, kind):
    bindings, scope, path = sample
    identity = save(scope, after='x' * 20000 + '\n' if kind == 'truncated' else 'new\n')
    if kind == 'rejected':
        decision.decide_task_file_edit_proposal(**identity, decision='rejected')
    elif kind == 'truncated':
        with pytest.raises(decision.ProposalDecisionError) as caught:
            decision.decide_task_file_edit_proposal(**identity, decision='approved')
        assert caught.value.code == 'proposal_diff_incomplete'
    monkeypatch.setattr(existing.execution, 'replace_workspace_text_file', lambda **kwargs: pytest.fail('must not write'))
    with pytest.raises(existing.gate.SampleExecutionError):
        existing.gate.execute_sample_proposal(bindings, **identity)
    assert existing.state(engine) == 'idle'
    assert (path / 'example.txt').read_bytes() == b'old\n'


@pytest.mark.parametrize('kind', ['baseline', 'candidate'])
def test_preflight_refuses_stale_or_corrupt_candidate(ready, engine, monkeypatch, kind):
    bindings, _, identity, path = ready
    if kind == 'baseline':
        (path / 'example.txt').write_bytes(b'external edit\n')
    else:
        with Session(engine) as session, session.begin():
            session.scalar(select(FileEditProposal)).proposed_content = 'tampered\n'
    before = (path / 'example.txt').read_bytes()
    monkeypatch.setattr(existing.execution, 'replace_workspace_text_file', lambda **kwargs: pytest.fail('must not write'))
    receipt = existing.gate.execute_sample_proposal(bindings, **identity)
    assert receipt.file_status == 'not_attempted'
    assert receipt.application_status == existing.state(engine) == 'not_applied'
    assert (path / 'example.txt').read_bytes() == before


@pytest.mark.parametrize('kind', ['ordinary', 'foreign', 'sibling', 'proposal', 'path', 'bound_root'])
def test_patch_scope_gate(ready, target, engine, monkeypatch, kind):
    # 将补丁生成的真实提案送入既有边界契约；不修改生产门禁。
    existing.test_invalid_scope_never_enters_executor(ready, target, engine, monkeypatch, kind)


@pytest.mark.parametrize('phase', ['before_claim', 'before_preflight'])
def test_patch_binding_switch(ready, engine, tmp_path, monkeypatch, phase):
    existing.test_database_root_switch_cannot_redirect_execution(ready, engine, tmp_path, monkeypatch, phase)


def test_patch_duplicate_application(ready, monkeypatch):
    existing.test_duplicate_execution_does_not_write_again(ready, monkeypatch)


@pytest.mark.parametrize('timing', ['before_commit', 'after_commit'])
def test_patch_finish_unconfirmed_retains_written_file(ready, setup, engine, monkeypatch, timing):
    # 此故障发生在文件替换后，不能用数据库异常推断文件未写。
    existing.test_registration_unknown_seals_binding_and_retains_file(ready, setup, engine, monkeypatch, timing)


def test_patch_directory_replacement(ready, engine, monkeypatch):
    existing.test_directory_replacement_after_preflight_rejected(ready, engine, monkeypatch)
