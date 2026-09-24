"""真实 PostgreSQL→Task 快照→Docker 内部执行专项。

从 apps/api 运行：
../../.venv/bin/python -m pytest ../../scripts/verify_task_sample_command.py -q -s
独立测试库/私有 schema 由根测试夹具创建及清理，仅管理本轮自有资源。
"""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_execution as execution
from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox import sandbox_sample_command as owner
from app.services.runtime.sandbox import task_sample_command as service
from app.services.runtime.sandbox.sandbox_identity import parse_created_container_id
from app.services.runtime.sandbox.sandbox_sample_cleanup import cleanup_sample_command
from app.services.runtime.sandbox.sandbox_stop import stop_and_confirm_sandbox
from tests.runtime.command.test_task_command_source import lab as lab  # noqa: PLC0414
from tests.tasks.test_task_deletion_service import target as target  # noqa: PLC0414
from tests.workspace.samples.test_task_sample_binding import setup as setup  # noqa: PLC0414

pytest_plugins = ['tests.conftest']

PROBE = """
import os
from pathlib import Path
p = Path('/workspace/example.txt')
assert p.read_bytes() == b'task snapshot content\\n'
assert Path.cwd() == Path('/tmp')
assert os.statvfs('/workspace').f_flag & os.ST_RDONLY
try:
    p.write_bytes(b'forbidden')
except PermissionError:
    pass
except OSError as error:
    import errno
    assert error.errno == errno.EROFS
else:
    raise AssertionError('unexpected write')
print(p.read_text(), end='', flush=True)
"""


@pytest.mark.parametrize('mode', ['read', 'nonzero', 'timeout', 'cancel', 'create_loss', 'delete_loss'])
def test_real_task_snapshot_command(lab, target, engine, monkeypatch, mode):
    bindings, scope, _ = lab
    bindings.bind(**scope)
    with Session(engine) as session:
        task_root = Path(session.scalar(select(Workspace.root_path)))
    task_file = task_root / 'example.txt'
    task_file.write_bytes(b'task snapshot content\n')
    original_mode = task_file.stat().st_mode
    audit = {}
    actual_snapshot = service.create_task_sandbox_snapshot
    actual_create = owner.create_sandbox_container
    actual_remove = owner.remove_sandbox_container
    actual_drain = execution.drain_docker_attach

    def snapshot(**kwargs):
        result = actual_snapshot(**kwargs)
        audit['sample'] = result
        assert bindings.read_status(**scope).status == 'ready'
        return result

    async def create_container(*, spec):
        audit['spec'] = spec
        # 导出完成后再改原文件；真实容器必须读到独立快照中的旧字节。
        task_file.write_bytes(b'task changed after snapshot\n')
        receipt = await actual_create(spec=spec)
        audit['cid'] = parse_created_container_id(receipt)
        if mode == 'create_loss':
            raise OSError('injected create receipt loss')
        return receipt

    async def remove_container(**kwargs):
        await actual_remove(**kwargs)
        if mode == 'delete_loss':
            raise OSError('injected delete receipt loss')

    monkeypatch.setattr(service, 'create_task_sandbox_snapshot', snapshot)
    monkeypatch.setattr(owner, 'create_sandbox_container', create_container)
    monkeypatch.setattr(owner, 'remove_sandbox_container', remove_container)
    if mode == 'timeout':
        monkeypatch.setattr(execution, 'COMMAND_TIMEOUT_SECONDS', 1)
    program = PROBE
    if mode == 'nonzero':
        program += '\nraise SystemExit(7)\n'
    if mode in ('timeout', 'cancel'):
        program += '\nimport time; time.sleep(600)\n'
    request = CommandRequest(argv=['/usr/local/bin/python', '-c', program])

    async def scenario():
        ready = asyncio.Event()
        async def drain(reader):
            class Observed:
                tail = b''
                async def read(self, n):
                    value = await reader.read(n)
                    self.tail = (self.tail + value)[-128:]
                    if b'task snapshot content\n' in self.tail:
                        ready.set()
                    return value
            return await actual_drain(Observed())
        monkeypatch.setattr(execution, 'drain_docker_attach', drain)
        task = asyncio.create_task(service.run_task_sample_command(
            request=request, user_id=target['user_id'],
            conversation_id=target['conversation_id'], bindings=bindings,
        ))
        recovery = None
        try:
            if mode == 'cancel':
                await asyncio.wait_for(ready.wait(), 10)
                task.cancel()
            try:
                result = await task
            except (owner.SampleCommandUnconfirmed, owner.SampleCommandCancelled) as error:
                recovery = error.recovery
                assert mode in ('timeout', 'cancel', 'create_loss', 'delete_loss')
                assert recovery.sample is audit['sample'] and recovery.sample.root.exists()
                assert (recovery.sample.root / 'example.txt').read_bytes() == b'task snapshot content\n'
                if mode in ('timeout', 'cancel'):
                    assert recovery.stop_confirmed and recovery.start_attempted
                    if mode == 'cancel':
                        assert task.cancelled()
                    else:
                        assert recovery.execution_reason == 'timed_out'
                elif mode == 'create_loss':
                    assert recovery.container_id is None and recovery.create_attempted
                else:
                    assert recovery.command is not None and recovery.command.succeeded
                    assert recovery.delete_attempted and not recovery.container_absent
            else:
                assert mode in ('read', 'nonzero')
                assert result.command.stdout == 'task snapshot content\n'
                assert result.command.exit_code == (7 if mode == 'nonzero' else 0)
                assert result.sample_cleaned and not audit['sample'].root.parent.exists()
            assert bindings.read_status(**scope).status == 'ready'
            assert task_file.read_bytes() == b'task changed after snapshot\n'
            assert task_file.stat().st_mode == original_mode
            if recovery is not None and recovery.container_id is not None:
                assert (await cleanup_sample_command(recovery=recovery)).recovery.sample_cleaned
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            # 脚本拥有独立审计的完整 ID，可为 create 回执丢失现场显式收尾。
            # 产品未知 ID 恢复仍不可直接采信诊断候选或盲目清理。
            cid = audit.get('cid')
            if cid is None and 'spec' in audit:
                raise RuntimeError('create outcome unknown; preserve snapshot')
            if cid is not None:
                if not await client.is_sandbox_container_absent(container_id=cid):
                    await stop_and_confirm_sandbox(
                        request=request, execution_token=audit['spec'].execution_token,
                        expected_container_id=cid,
                    )
                    await client.remove_sandbox_container(container_id=cid)
                assert await client.is_sandbox_container_absent(container_id=cid)
            sample = audit.get('sample')
            if sample is not None and sample.token in samples._ACTIVE_SAMPLES:
                samples.cleanup_sandbox_sample(sample)
            assert sample is None or not sample.root.parent.exists()
        print(json.dumps({'case': mode, 'container_id': audit['cid'],
                          'container_absent': True, 'snapshot_released': True,
                          'task_source_preserved': True}), flush=True)

    asyncio.run(scenario())
    bindings.close(**scope)
    assert not task_root.exists()
