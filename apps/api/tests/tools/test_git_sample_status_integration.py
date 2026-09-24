"""工具→真实任务授权→自建Git→公开结果，使用隔离PostgreSQL。"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.git_sample_status import make_git_sample_status_executor
from tests.workspace.git import test_task_git_samples as existing

setup = existing.setup
target = existing.target


def context(scope, target):
    return ToolExecutionContext(**scope, conversation_id=target['conversation_id'])


def test_real_tool_result_preserves_scope_and_file(setup, target, engine):
    manager, scope, _ = setup
    manager.bind(**scope)
    root = existing.sample(manager, scope).root
    path = root / '中文\nfile.txt'
    path.write_text('original')
    before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
    execute = make_git_sample_status_executor(manager)
    raw = execute(context=context(scope, target))
    result = json.loads(raw)
    assert result['source'] == 'task_git_sample' and result['status'] == 'complete'
    assert result['entries'] == [{'xy': '??', 'kind': 'untracked', 'path': '中文\nfile.txt',
                                  'original_path': None, 'index_status': None, 'worktree_status': None}]
    assert str(root) not in raw
    assert (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) == before
    with Session(engine) as session:
        assert session.scalar(select(Workspace.root_path)) == '/preserved'


@pytest.mark.parametrize('kind', ['missing', 'foreign', 'changed-owner', 'closed'])
def test_actual_rejection_never_creates_or_falls_back(setup, target, engine, kind):
    manager, scope, _ = setup
    if kind != 'missing':
        manager.bind(**scope)
    query_scope = dict(scope)
    if kind == 'foreign':
        query_scope['user_id'] = target['other_id']
    elif kind == 'changed-owner':
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).user_id = target['other_id']
    elif kind == 'closed':
        manager.close(**scope)
    execute = make_git_sample_status_executor(manager)
    with pytest.raises(SafeToolExecutionError) as caught:
        execute(context=context(query_scope, target))
    assert caught.value.code == ('workspace_not_accessible' if kind == 'changed-owner' else 'task_git_sample_unavailable')
    if kind in ('missing', 'closed'):
        assert not manager._bindings
