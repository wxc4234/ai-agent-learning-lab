"""仅隔离验收：固定模型决策，真实 Task/命令/Docker，退出前核对并清理自有资源。"""

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from uuid import uuid4

from app.database import SessionLocal
from app.models import Conversation, Task, Workspace
from app.services.auth.local_identity import resolve_local_identity
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction, ToolErrorObservation
from app.services.runtime.sandbox import sandbox_sample_command as owner
from app.services.runtime.sandbox import sandbox_execution as execution
from app.services.runtime.sandbox.sandbox_sample_cleanup import cleanup_sample_command
from app.services.runtime.docker.docker_client import is_sandbox_container_absent
from app.services.workspace.samples.sample_execution_runtime import get_sample_bindings

PROBE = """
import errno, os
from pathlib import Path
p = Path('/workspace/example.txt')
assert p.read_bytes() == b'old\\n'
assert Path.cwd() == Path('/tmp')
assert os.statvfs('/workspace').f_flag & os.ST_RDONLY
try:
    p.write_bytes(b'forbidden')
except OSError as error:
    assert error.errno in (errno.EROFS, errno.EACCES)
else:
    raise AssertionError('write unexpectedly allowed')
print('TASK_SAMPLE_READONLY_OK', flush=True)
"""


def sample_command_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if observations:
        observation = observations[-1]
        answer = (
            f'样例命令错误：{observation.details}'
            if isinstance(observation, ToolErrorObservation)
            else f"样例命令完成：退出码{json.loads(observation.result)['exit_code']}"
        )
        return FinalAnswer(content=answer, model_usage=usage)
    program = PROBE
    if '[task-sample-nonzero]' in prompt:
        program += '\nraise SystemExit(7)\n'
    elif '[task-sample-cancel]' in prompt:
        program += "\nimport time; print('TASK_SAMPLE_CANCEL_READY',flush=True); time.sleep(60)\n"
    elif '[task-sample-cleanup]' in prompt:
        program += '\n# DELETE_RECEIPT_LOSS\n'
    return ToolAction(
        tool_call_id='browser-task-sample', tool_name='run_command',
        arguments=json.dumps({'argv': ['/usr/local/bin/python', '-c', program]}), model_usage=usage,
    )


def install_sample_command_fixture(app):
    original_lifespan = app.router.lifespan_context
    original_create = owner.create_sandbox_container
    original_remove = owner.remove_sandbox_container
    original_drain = execution.drain_docker_attach
    lose_receipt = set()

    async def create(*, spec):
        result = await original_create(spec=spec)
        if 'DELETE_RECEIPT_LOSS' in str(spec):
            from app.services.runtime.sandbox.sandbox_identity import parse_created_container_id
            lose_receipt.add(parse_created_container_id(result))
        return result

    async def remove(*, container_id):
        await original_remove(container_id=container_id)
        if container_id in lose_receipt:
            lose_receipt.remove(container_id)
            raise OSError('isolated fixture: delete receipt lost')

    async def drain(reader):
        class Observed:
            tail = b''

            async def read(self, size):
                value = await reader.read(size)
                self.tail = (self.tail + value)[-256:]
                if b'TASK_SAMPLE_CANCEL_READY' in self.tail:
                    Path(os.environ['TASK_SAMPLE_READY']).write_text('ready')
                return value
        return await original_drain(Observed())

    owner.create_sandbox_container = create
    owner.remove_sandbox_container = remove
    execution.drain_docker_attach = drain

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            bindings = get_sample_bindings()
            fixtures = []
            roots = []
            with SessionLocal() as session:
                user = resolve_local_identity(session)
            # 提交数据库归属后才创建文件来源；不让数据库事务包住文件生命周期。
            for mode in ('success', 'nonzero', 'cleanup', 'cancel'):
                workspace_id, task_id, conversation_id = (uuid4().hex for _ in range(3))
                with SessionLocal() as session, session.begin():
                    workspace = Workspace(external_id=workspace_id, name=f'样例命令-{mode}', user_id=user.id)
                    task = Task(external_id=task_id, title=f'样例命令-{mode}', workspace=workspace)
                    conversation = Conversation(external_id=conversation_id, user_id=user.id, task=task)
                    session.add_all([workspace, task, conversation])
                scope = {'user_id': user.id, 'workspace_id': workspace_id, 'task_id': task_id}
                bindings.bind(**scope)
                with bindings.borrow(**scope) as sample:
                    root = sample.root
                    assert (root / 'example.txt').read_bytes() == b'old\n'
                    roots.append((scope, root, (root / 'example.txt').stat().st_mode))
                fixtures.append({'mode': mode, 'workspace_id': workspace_id, 'task_id': task_id})
            # 仅父进程私有测试文件，浏览器不获得宿主路径或内部凭证。
            Path(os.environ['TASK_SAMPLE_FIXTURE']).write_text(json.dumps(fixtures))
            try:
                yield
            finally:
                evidence = []
                checks = []
                scopes = list(application.state.task_sample_recovery_store._scopes.values())
                try:
                    for scope in scopes:
                        assert scope.journal._closed
                        assert len(scope.journal.records) == 1
                        record = scope.journal.records[0]
                        recovery = record.recovery
                        entry = {'run_id': scope.run_id, 'status': record.status, 'closed': True}
                        if recovery is not None:
                            entry.update(
                                stop_confirmed=recovery.stop_confirmed,
                                start_attempted=recovery.start_attempted,
                                delete_attempted=recovery.delete_attempted,
                                container_absent_at_record=recovery.container_absent,
                                command_retained=recovery.command is not None,
                                snapshot_retained=recovery.sample.root.exists(),
                            )
                            assert entry['snapshot_retained']
                            if record.status == 'cancelled':
                                assert recovery.stop_confirmed and recovery.start_attempted
                            else:
                                assert record.status == 'unconfirmed' and recovery.command.exit_code == 0
                                assert recovery.delete_attempted and not recovery.container_absent
                            # 隔离夹具作为原拥有者显式清理，不增加产品自动恢复行为。
                            cleaned = await cleanup_sample_command(recovery=recovery)
                            assert cleaned.recovery.sample_cleaned
                            assert not recovery.sample.root.parent.exists()
                            cid = recovery.container_id
                        else:
                            assert record.status == 'completed' and record.sample_cleaned
                            cid = record.container_id
                            entry['exit_code'] = json.loads(record.command_json)['exit_code']
                        assert await is_sandbox_container_absent(container_id=cid)
                        entry['container_absent_after_fixture_cleanup'] = True
                        evidence.append(entry)
                    for scope, root, original_mode in roots:
                        assert bindings.read_status(**scope).status == 'ready'
                        assert (root / 'example.txt').read_bytes() == b'old\n'
                        assert (root / 'example.txt').stat().st_mode == original_mode
                        checks.append(True)
                    assert len(scopes) == 4
                    assert sorted(item['status'] for item in evidence) == ['cancelled', 'completed', 'completed', 'unconfirmed']
                    assert not application.state.command_recovery_store._scopes
                    Path(os.environ['TASK_SAMPLE_REPORT']).write_text(json.dumps({
                        'records': evidence, 'task_sources_unchanged': len(checks),
                        'all_containers_absent': True,
                    }, indent=4))
                finally:
                    for scope, root, _ in roots:
                        bindings.close(**scope)
                        assert not root.exists()
    app.router.lifespan_context = lifespan
