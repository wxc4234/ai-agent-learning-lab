"""资源变更与应用领取在真实PostgreSQL中串行。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import FileEditProposal, Task, Workspace
from app.repositories.workspace.proposal_application_guard import ProposalApplicationBusyError
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.tasks import task_deletion_service as deletion
from app.services.workspace.directory import workspace_binding as binding
from app.services.workspace.proposals import file_edit_proposal_application as application
from tests.workspace.proposals import test_file_edit_proposal_application as source

root = source.root
target = source.target
database = source.database
setup = source.setup
saved = source.saved
ready = source.ready


def mutate(engine, query, operation, path):
    with Session(engine) as session:
        args = {k: query[k] for k in ('user_id', 'workspace_id')}
        if operation == 'delete':
            return deletion.delete_workspace_task(session, **args, task_id=query['task_id'])
        return binding.bind_workspace_directory(session, **args, root_path=str(path))


@pytest.mark.parametrize('state', ['running', 'uncertain'])
@pytest.mark.parametrize('operation', ['delete', 'bind'])
def test_active_blocks_without_changes(ready, engine, root, state, operation):
    query, file, _ = ready
    claim = application.claim_task_file_edit_proposal(**query)
    if state == 'uncertain':
        application.finish_task_file_edit_proposal(**query, application_token=claim.application_token, outcome=state)
    other = root / 'other'
    other.mkdir()
    before = file.read_bytes()
    with pytest.raises(ProposalApplicationBusyError):
        mutate(engine, query, operation, other)
    with Session(engine) as session:
        assert session.scalar(select(Task.id)) is not None
        assert session.scalar(select(Workspace.root_path)) == str(root)
        assert session.scalar(select(FileEditProposal.application_status)) == state
    assert file.read_bytes() == before
    # 同一规范路径不改变绑定，执行期间仍可重复调用。
    assert mutate(engine, query, 'bind', root).root_path == str(root)


@pytest.mark.parametrize('state', ['idle', 'applied', 'not_applied'])
def test_settled_proposal_does_not_block_deletion(ready, engine, root, state):
    query = ready[0]
    if state != 'idle':
        claim = application.claim_task_file_edit_proposal(**query)
        application.finish_task_file_edit_proposal(**query, application_token=claim.application_token, outcome=state)
    mutate(engine, query, 'delete', root)
    with Session(engine) as session:
        assert session.scalar(select(FileEditProposal.id)) is None


@pytest.mark.parametrize('operation', ['delete', 'bind'])
def test_auth_failure_precedes_occupancy_information(ready, engine, root, target, operation):
    application.claim_task_file_edit_proposal(**ready[0])
    with pytest.raises(WorkspaceNotAccessibleError):
        mutate(engine, {**ready[0], 'user_id': target['other_id']}, operation, root)


def test_sibling_task_can_be_deleted_but_workspace_binding_is_blocked(ready, engine, root):
    query = ready[0]
    application.claim_task_file_edit_proposal(**query)
    # 夹具中的另一任务与会话归属相同，不应被项目内其他任务的占用阻止删除。
    mutate(engine, {**query, 'task_id': 'd' * 32}, 'delete', root)
    other = root / 'other'
    other.mkdir()
    with pytest.raises(ProposalApplicationBusyError):
        mutate(engine, query, 'bind', other)


@pytest.mark.parametrize('operation', ['delete', 'bind'])
@pytest.mark.parametrize('first', ['claim', 'mutation'])
def test_real_lock_wait_orders_claim_and_mutation(ready, engine, root, monkeypatch, operation, first):
    query = ready[0]
    other = root / 'other'
    other.mkdir()
    held, release = Event(), Event()
    blocker = []
    # 仅拦截领先操作的取锁函数；数据库事实确认另一连接确实在等待。
    module = application if first == 'claim' else (deletion if operation == 'delete' else binding)
    name = 'lock_owned_proposal_task' if first == 'claim' else 'require_owned_workspace_for_update'
    original = getattr(module, name)

    def hold(*args, **kwargs):
        result = original(*args, **kwargs)
        session = args[0] if args else kwargs['session']
        blocker.append(session.scalar(text('SELECT pg_backend_pid()')))
        held.set()
        assert release.wait(8)
        return result

    monkeypatch.setattr(module, name, hold)
    claim_call = lambda: application.claim_task_file_edit_proposal(**query)
    mutation_call = lambda: mutate(engine, query, operation, other)
    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(claim_call if first == 'claim' else mutation_call)
        try:
            assert held.wait(5)
            follower = pool.submit(mutation_call if first == 'claim' else claim_call)
            with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                deadline = monotonic() + 5
                blocked = False
                while monotonic() < deadline:
                    blocked = observer.scalar(text('SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid)))'), {'pid': blocker[0]})
                    if blocked:
                        break
            assert blocked and not follower.done()
        finally:
            release.set()
        if first == 'claim':
            assert leader.result(timeout=5).application_status == 'running'
            with pytest.raises(ProposalApplicationBusyError):
                follower.result(timeout=5)
        elif operation == 'delete':
            leader.result(timeout=5)
            with pytest.raises(WorkspaceNotAccessibleError):
                follower.result(timeout=5)
        else:
            with pytest.raises(binding.WorkspaceAlreadyBoundError):
                leader.result(timeout=5)
            assert follower.result(timeout=5).application_status == 'running'
