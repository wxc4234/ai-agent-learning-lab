"""隔离API启动夹具：只通过内部流程创建样例，无公开登记接口。"""

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from uuid import uuid4

if not os.environ.get('DATABASE_URL', '').split('?')[0].rsplit('/', 1)[-1].startswith('agent_lab_test_'):
    raise RuntimeError('Requires generated isolated database')

from app.database import SessionLocal
from app.main import app
from app.models import Conversation, FileEditProposal, Task, Workspace, WorkspaceSampleOrigin
from sqlalchemy import select
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError, TaskSampleBindings
from app.services.workspace.samples import sample_execution_runtime
from app.services.auth.local_identity import resolve_local_identity
from app.services.workspace.samples.sample_execution_runtime import get_sample_bindings
from app.services.workspace.proposals.file_edit_proposal_service import create_task_file_edit_proposal
from app.services.workspace.proposals.file_edit_proposal_decision import decide_task_file_edit_proposal

original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(application):
    async with original_lifespan(application):
        with SessionLocal() as session:
            user = resolve_local_identity(session)
        workspace_id, task_id = uuid4().hex, uuid4().hex
        # 夹具资源在独立事务内提交，文件生命周期不跨数据库事务。
        with SessionLocal() as session, session.begin():
            workspace = Workspace(external_id=workspace_id, name='隔离样例', user_id=user.id)
            task = Task(external_id=task_id, title='真实应用验收', workspace=workspace)
            conversation = Conversation(external_id=uuid4().hex, user_id=user.id, task=task)
            session.add_all([workspace, task, conversation])
        scope = {'user_id': user.id, 'workspace_id': workspace_id, 'task_id': task_id}
        bindings = get_sample_bindings()
        bindings.bind(**scope)
        cleanup_pending = os.environ.get('STATUS_CLEANUP_PENDING') == '1'
        try:
            proposal = create_task_file_edit_proposal(**scope, relative_path='example.txt', old_text='old', new_text='new')
            decide_task_file_edit_proposal(**scope, proposal_id=proposal.proposal_id, decision='approved')
            with bindings.borrow(**scope) as sample:
                root = str(sample.root)
            if cleanup_pending:
                # 真实执行关闭的首次提交；仅测试进程让文件清理报告延期，以保留持久待办供HTTP读取。
                binding = bindings._bindings[(user.id, workspace_id, task_id)]
                original_close = bindings._registry.close

                def defer_cleanup(handle):
                    assert handle == binding.handle
                    return False

                bindings._registry.close = defer_cleanup
                try:
                    try:
                        bindings.close(**scope)
                    except TaskSampleBindingError as error:
                        assert error.code == 'sample_cleanup_incomplete'
                    else:
                        raise AssertionError('expected test-only deferred cleanup')
                finally:
                    bindings._registry.close = original_close
            elif os.environ.get('STATUS_LOST_REGISTRATION') == '1':
                # 只在隔离测试进程模拟重启：保留原句柄供退出时受控清理，HTTP依赖改读空登记。
                sample_execution_runtime._sample_bindings = TaskSampleBindings()
            # 只写父进程私有夹具文件，不把路径或凭证返回浏览器。
            Path(os.environ['EXECUTION_FIXTURE']).write_text(json.dumps({**scope, 'proposal_id': proposal.proposal_id, 'root': root}))
            yield
        finally:
            mode = 'normal'
            if cleanup_pending:
                # HTTP请求结束后才关闭本轮自建句柄，再撤销它的测试来源；不做产品级自动恢复。
                binding = bindings._bindings[(user.id, workspace_id, task_id)]
                assert binding.state == 'uncertain' and not binding.busy
                with SessionLocal() as session:
                    workspace = session.scalar(select(Workspace).where(Workspace.external_id == workspace_id))
                    origin = session.get(WorkspaceSampleOrigin, workspace.id)
                    source_task_id = session.scalar(select(Task.id).where(Task.external_id == task_id))
                    assert workspace.root_path is None
                    assert origin is not None and origin.task_id == source_task_id
                    assert origin.root_path == root and origin.lifecycle_state == 'cleanup_pending'
                assert bindings._registry.close(binding.handle)
                with SessionLocal() as session:
                    workspace = session.scalar(select(Workspace).where(Workspace.external_id == workspace_id))
                    origin = session.get(WorkspaceSampleOrigin, workspace.id)
                    source_task_id = session.scalar(select(Task.id).where(Task.external_id == task_id))
                    assert workspace.root_path is None
                    assert origin is not None and origin.task_id == source_task_id
                    assert origin.root_path == root and origin.lifecycle_state == 'cleanup_pending'
                    session.delete(origin)
                    session.commit()
                mode = 'pending-test-cleanup'
            else:
                try:
                    bindings.close(**scope)
                except TaskSampleBindingError:
                    # 仅隔离测试收尾：lifespan退出时HTTP请求已结束；重复领取使登记封锁。
                    # 先确认最终数据库/文件证据，再解绑并关闭自有登记，不修改生产恢复策略。
                    binding = bindings._bindings[(user.id, workspace_id, task_id)]
                    assert not binding.busy and binding.state == 'uncertain'
                    with SessionLocal() as session, session.begin():
                        row = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == proposal.proposal_id))
                        workspace = session.scalar(select(Workspace).where(Workspace.external_id == workspace_id))
                        assert row.application_status == 'applied'
                        assert workspace.root_path == root
                        assert (Path(root) / 'example.txt').read_bytes() == b'new\n'
                        workspace.root_path = None
                    bindings._registry.close(binding.handle)
                    mode = 'sealed-test-cleanup'
            Path(os.environ['EXECUTION_CLEANUP']).write_text(mode)


app.router.lifespan_context = lifespan
