"""真实事务核对目录绑定版本；内部解绑仅测试仓库契约，不新增产品接口。"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import (
    require_owned_workspace_for_update, set_locked_workspace_root,
)
from tests.workspace.directory.test_workspace_binding import bind, setup_binding
from tests.assertions import require_value

__all__ = ['setup_binding']


def snapshot(engine):
    with Session(engine) as session:
        row = require_value(session.scalar(select(Workspace)))
        return row.root_path, row.binding_revision


def change(engine, owner, path):
    with Session(engine) as session, session.begin():
        workspace = require_owned_workspace_for_update(session, user_id=owner, workspace_id='workspace')
        set_locked_workspace_root(workspace, path)


def test_bind_unbind_and_aba_have_distinct_committed_revisions(engine, setup_binding):
    owner, first, second = setup_binding
    assert snapshot(engine) == (None, 1)
    with Session(engine) as session:
        bind(session, owner, first)
        assert snapshot(engine) == (str(first), 2)
        bind(session, owner, first)
        assert snapshot(engine) == (str(first), 2)
    for path, revision in [(None, 3), (str(second), 4), (None, 5), (str(first), 6)]:
        change(engine, owner, path)
        assert snapshot(engine) == (path, revision)
    # 即使路径字符串回到A，原许可绑定的版本2也已失效。
    assert snapshot(engine)[1] != 2
    change(engine, owner, str(first))
    assert snapshot(engine) == (str(first), 6)


def test_flushed_change_rolls_back_path_and_revision(engine, setup_binding):
    owner, first, _ = setup_binding
    with Session(engine) as session:
        workspace = require_owned_workspace_for_update(session, user_id=owner, workspace_id='workspace')
        set_locked_workspace_root(workspace, str(first))
        session.flush()
        assert workspace.binding_revision == 2
        assert snapshot(engine) == (None, 1)
        session.rollback()
    assert snapshot(engine) == (None, 1)
    change(engine, owner, str(first))
    assert snapshot(engine) == (str(first), 2)


def test_revision_overflow_rejects_without_changing_path(engine, setup_binding):
    owner, first, _ = setup_binding
    with Session(engine) as session, session.begin():
        workspace = require_owned_workspace_for_update(session, user_id=owner, workspace_id='workspace')
        workspace.binding_revision = 2**63 - 1
    with pytest.raises(ValueError, match='revision_unavailable'):
        change(engine, owner, str(first))
    assert snapshot(engine) == (None, 2**63 - 1)
