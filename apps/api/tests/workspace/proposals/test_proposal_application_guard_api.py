"""真实local HTTP拒绝应用占用中的资源变更。"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FileEditProposal, Task, Workspace
from app.routers.workspace import directories as workspace
from tests.local.test_local_mode import HEADERS
from tests.tasks.test_task_workspace import task
from tests.workspace.directory import test_workspace_binding_api as binding

local_client = binding.local_client
target = binding.target


@pytest.mark.parametrize('state', ['running', 'uncertain'])
@pytest.mark.parametrize('operation', ['delete', 'bind', 'picker'])
def test_busy_http_is_safe_and_keeps_resources(local_client, target, engine, tmp_path, monkeypatch, state, operation):
    created, path = task(local_client, target)
    binding.safe(binding.put(local_client, target), 200)
    with Session(engine) as session, session.begin():
        row = session.scalar(select(Task).where(Task.external_id == created['external_id']))
        session.add(FileEditProposal(external_id='c' * 32, task_id=row.id, bound_root=target[1],
            relative_path='file.txt', baseline_sha256='a' * 64, proposed_sha256='b' * 64,
            proposed_content='PRIVATE', diff='PRIVATE', diff_truncated=False, status='approved',
            application_status=state, application_token='f' * 32))
    other = tmp_path / 'other'
    other.mkdir()
    if operation == 'delete':
        response = local_client.delete(path, headers=HEADERS)
    elif operation == 'bind':
        response = binding.put(local_client, (target[0], str(other)))
    else:
        # 选择窗口打开时未绑定；等待用户选择期间另一执行已绑定并领取。
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = None
            proposal = session.scalar(select(FileEditProposal))
            proposal.application_status = 'idle'
            proposal.application_token = None

        def concurrent_selection():
            with Session(engine) as session, session.begin():
                session.scalar(select(Workspace)).root_path = target[1]
                proposal = session.scalar(select(FileEditProposal))
                proposal.application_status = state
                proposal.application_token = 'f' * 32
            return str(other)

        monkeypatch.setattr(workspace, 'select_directory', concurrent_selection)
        response = local_client.post(f'/workspaces/{target[0]}/directory/select', headers=HEADERS, json={})
    binding.safe(response, 409, 'proposal_application_busy')
    with Session(engine) as session:
        assert session.scalar(select(Task.id).where(Task.external_id == created['external_id']))
        assert session.scalar(select(FileEditProposal.application_status)) == state
    assert binding.stored(engine, target[0]) == target[1]
