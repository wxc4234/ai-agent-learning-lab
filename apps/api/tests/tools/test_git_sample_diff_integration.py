"""工具经真实Task授权读取临时Git；隔离数据库，不调用模型。"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.git_sample_diff import make_git_sample_diff_executor
from tests.assertions import require_value
from tests.workspace.git.test_diff_capture import baseline
from tests.workspace.git.test_status_capture import git, snapshot
from tests.workspace.git.test_task_git_samples import sample, setup, target

__all__ = ['setup', 'target']


def test_real_scopes_are_read_only(setup, target):
    manager, ids, _ = setup
    manager.bind(**ids)
    handle = sample(manager, ids)
    baseline(handle)
    file = handle.root / 'value.txt'
    file.write_bytes(b'value = 2\n')
    git(handle, 'add', 'value.txt')
    file.write_bytes(b'value = 3\n')
    before = snapshot(handle.root)
    context = ToolExecutionContext(**ids, conversation_id=target['conversation_id'])
    execute = make_git_sample_diff_executor(manager)
    for scope, expected in [('worktree', '-value = 2\n+value = 3\n'), ('staged', '-value = 1\n+value = 2\n')]:
        raw = execute(context=context, scope=scope)
        result = json.loads(raw)
        assert result['source'] == 'task_git_sample' and result['scope'] == scope
        assert expected in result['diff']
        assert str(handle.root) not in raw
    assert snapshot(handle.root) == before


@pytest.mark.parametrize('kind', ['missing', 'foreign', 'changed-owner', 'closed', 'missing-head'])
def test_real_rejection_never_creates_or_falls_back(setup, target, engine, kind):
    manager, ids, _ = setup
    if kind != 'missing':
        manager.bind(**ids)
    query = dict(ids)
    if kind == 'foreign':
        query['user_id'] = target['other_id']
    elif kind == 'changed-owner':
        with Session(engine) as session, session.begin():
            require_value(session.scalar(select(Workspace))).user_id = target['other_id']
    elif kind == 'closed':
        manager.close(**ids)
    execute = make_git_sample_diff_executor(manager)
    with pytest.raises(SafeToolExecutionError) as caught:
        execute(context=ToolExecutionContext(**query, conversation_id=target['conversation_id']), scope='staged')
    expected = {
        'changed-owner': 'workspace_not_accessible',
        'missing-head': 'git_diff_command_failed',
    }.get(kind, 'task_git_sample_unavailable')
    assert caught.value.code == expected
    if kind in ('missing', 'closed'):
        assert not manager._bindings
