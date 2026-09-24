"""真实文件与隔离 PostgreSQL 验证工具保存及提交不确定语义。"""

import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import FileEditProposal, Workspace
from app.tools import create_file_patch_proposal as adapter
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from tests.workspace.proposals import test_file_patch_proposal_service as existing
from tests.workspace.proposals.test_file_edit_proposal_service import count

root = existing.root
target = existing.target
database = existing.database
setup = existing.setup
patch_setup = existing.patch_setup


def invoke(args, target):
    context = ToolExecutionContext(
        user_id=args['user_id'], workspace_id=args['workspace_id'],
        task_id=args['task_id'], conversation_id=target['conversation_id'],
    )
    return adapter.create_file_patch_proposal(
        context=context, relative_path=args['relative_path'], patch=args['patch'],
    )


@pytest.mark.parametrize('after', ['new\n', '', 'x' * 20000 + '\n'])
def test_real_saved_receipt_and_unchanged_file(patch_setup, engine, target, after):
    args, file, _, _ = patch_setup
    args['patch'] = existing.make_patch('old\n', after)
    result = json.loads(invoke(args, target))
    with Session(engine) as session:
        row = session.scalar(select(FileEditProposal))
        assert row.external_id == result['proposal_id']
        assert row.status == result['status'] == 'pending'
        assert row.proposed_content == after
        assert row.baseline_sha256 == result['baseline_sha256'] == sha256(b'old\n').hexdigest()
        assert row.proposed_sha256 == result['proposed_sha256'] == sha256(after.encode()).hexdigest()
        assert row.diff_truncated == result['diff_truncated'] == (len(after) > 20000)
    assert set(result) == {
        'proposal_id', 'status', 'relative_path', 'baseline_sha256',
        'proposed_sha256', 'diff_truncated', 'created_at',
    }
    assert count(engine) == 1
    assert file.read_bytes() == b'old\n'


@pytest.mark.parametrize('kind,code', [
    ('foreign', 'workspace_not_accessible'), ('conflict', 'patch_context_mismatch'),
    ('binding', 'proposal_binding_changed'),
])
def test_rejection_never_saves(patch_setup, engine, target, monkeypatch, kind, code):
    args, file, _, _ = patch_setup
    if kind == 'foreign':
        args['user_id'] = target['other_id']
    elif kind == 'conflict':
        args['patch'] = existing.make_patch('wrong\n', 'new\n')
    else:
        original = existing.service.preview_task_file_patch
        def preview(**kwargs):
            result = original(**kwargs)
            with Session(engine) as session, session.begin():
                session.scalar(select(Workspace)).root_path = '/changed'
            return result
        monkeypatch.setattr(existing.service, 'preview_task_file_patch', preview)
    with pytest.raises(SafeToolExecutionError) as caught:
        invoke(args, target)
    assert caught.value.code == code
    assert count(engine) == 0
    assert file.read_bytes() == b'old\n'


@pytest.mark.parametrize('stage', ['before_commit', 'after_commit', 'projection'])
def test_unconfirmed_may_already_be_saved(patch_setup, engine, target, monkeypatch, stage):
    args, file, _, session_class = patch_setup
    def fail(*args, **kwargs):
        raise RuntimeError('/PRIVATE connection or receipt lost')
    if stage == 'projection':
        # 真实提交之后才破坏序列化，不伪造保存成功。
        monkeypatch.setattr(adapter, 'json', SimpleNamespace(dumps=fail))
    else:
        event.listen(session_class, stage, fail)
    try:
        with pytest.raises(SafeToolExecutionError) as caught:
            invoke(args, target)
    finally:
        if stage != 'projection':
            event.remove(session_class, stage, fail)
    assert caught.value.code == 'proposal_save_unconfirmed'
    assert 'PRIVATE' not in str(caught.value)
    assert count(engine) == (0 if stage == 'before_commit' else 1)
    assert file.read_bytes() == b'old\n'
