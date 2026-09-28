"""隔离PostgreSQL→Task固定测试→真实Docker；输出停滞/回执丢失仅测试注入。

从apps/api运行：../../.venv/bin/python -m pytest ../../scripts/verify_task_verification.py -q -s
"""

import asyncio
import json

import pytest

from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_execution as executor
from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox import sandbox_sample_command as owner
from app.services.runtime.sandbox.sandbox_identity import parse_created_container_id
from app.services.runtime.sandbox.sandbox_sample_cleanup import cleanup_sample_command
from app.services.runtime.sandbox.sandbox_stop import stop_and_confirm_sandbox
from app.services.runtime.verification import task_verification as service
from app.services.runtime.verification.contracts import VerificationRequest, build_verification_command
from tests.assertions import require_value
from tests.runtime.command.test_task_command_source import lab as lab  # noqa: PLC0414
from tests.runtime.verification.test_task_verification_input import context, root
from tests.tasks.test_task_deletion_service import target as target  # noqa: PLC0414
from tests.workspace.samples.test_task_sample_binding import setup as setup  # noqa: PLC0414

pytest_plugins = ['tests.conftest']


@pytest.mark.parametrize('mode', ['old', 'new', 'injection', 'timeout', 'cancel', 'delete_loss'])
def test_real_task_verification(lab, target, engine, monkeypatch, mode):
    bindings, scope, sessions = lab
    bindings.bind(**scope)
    expected = context(target)
    source = root(engine) / 'example.txt'
    content = b'old\n' if mode == 'old' else b'raise SystemExit(0)' if mode == 'injection' else b'new\n'
    source.write_bytes(content)
    def fingerprint(path):
        info = path.stat()
        return path.read_bytes(), info.st_ino, info.st_mtime_ns, info.st_mode
    before = fingerprint(source)
    audit = {}
    prepare = service.create_task_verification_snapshot
    create = owner.create_sandbox_container
    cleanup = owner.cleanup_sandbox_sample
    remove = owner.remove_sandbox_container
    drain = executor.drain_docker_attach
    def snapshot(**kwargs):
        value = prepare(**kwargs)
        assert 'sample' not in audit
        audit['sample'] = value
        audit['snapshot_before'] = fingerprint(value.root / 'example.txt')
        assert bindings.read_status(**scope).status == 'ready'
        assert all(s.closed and not s.in_transaction() for s in sessions)
        return value
    async def create_container(*, spec):
        audit['spec'] = spec
        response = await create(spec=spec)
        audit['cid'] = parse_created_container_id(response)
        return response
    async def remove_container(**kwargs):
        await remove(**kwargs)
        if mode == 'delete_loss':
            raise OSError('injected delete receipt loss')
    def cleanup_snapshot(sample):
        assert fingerprint(sample.root / 'example.txt') == audit['snapshot_before']
        cleanup(sample)
    monkeypatch.setattr(owner, 'cleanup_sandbox_sample', cleanup_snapshot)
    monkeypatch.setattr(service, 'create_task_verification_snapshot', snapshot)
    monkeypatch.setattr(owner, 'create_sandbox_container', create_container)
    monkeypatch.setattr(owner, 'remove_sandbox_container', remove_container)
    if mode == 'timeout':
        monkeypatch.setattr(executor, 'COMMAND_TIMEOUT_SECONDS', 2)
    command = build_verification_command('sample_unittest_v1')
    async def scenario():
        output_complete = asyncio.Event()
        async def stalled_drain(reader):
            result = await drain(reader)
            # 固定测试真实结束后阻止传输收尾：退出0仍不能覆盖本次超时/取消。
            output_complete.set()
            if mode in ('timeout', 'cancel'):
                await asyncio.Future()
            return result
        monkeypatch.setattr(executor, 'drain_docker_attach', stalled_drain)
        task = asyncio.create_task(service.run_task_verification(
            request=VerificationRequest(plan_id='sample_unittest_v1'), context=expected, bindings=bindings,
        ))
        try:
            if mode == 'cancel':
                await asyncio.wait_for(output_complete.wait(), 10)
                task.cancel()
            try:
                result = await task
            except (owner.SampleCommandUnconfirmed, owner.SampleCommandCancelled) as error:
                recovery = error.recovery
                print(json.dumps({'mode': mode, 'token': recovery.execution_token,
                    'container_id': recovery.container_id, 'root': str(recovery.sample_root)}), flush=True)
                assert mode in ('timeout', 'cancel', 'delete_loss')
                assert recovery.sample is audit['sample'] and recovery.stop_confirmed
                if mode == 'delete_loss':
                    assert require_value(recovery.command).succeeded
                    assert recovery.delete_attempted and not recovery.container_absent
                elif mode == 'timeout':
                    assert recovery.execution_reason == 'timed_out' and output_complete.is_set()
                else:
                    assert isinstance(error, owner.SampleCommandCancelled) and task.cancelled()
                assert fingerprint(require_value(recovery.sample).root / 'example.txt') == audit['snapshot_before']
                cleaned = await cleanup_sample_command(recovery=recovery)
                assert cleaned.recovery.sample_cleaned and cleaned.recovery.container_absent
            else:
                assert mode in ('old', 'new', 'injection')
                assert result.verification.outcome == ('passed' if mode == 'new' else 'failed')
                assert require_value(result.verification.report).tests_run == 1
                assert result.execution.sample_cleaned
            assert fingerprint(source) == before
            assert bindings.read_status(**scope).status == 'ready'
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            # 独立记录本脚本实际创建的ID；未知create结果保留现场，不盲删来源。
            cid = audit.get('cid')
            if cid is None and 'spec' in audit:
                raise RuntimeError('create outcome unknown; preserve snapshot')
            if cid is not None:
                if not await client.is_sandbox_container_absent(container_id=cid):
                    await stop_and_confirm_sandbox(
                        request=command, execution_token=audit['spec'].execution_token,
                        expected_container_id=cid,
                    )
                    await client.remove_sandbox_container(container_id=cid)
                assert await client.is_sandbox_container_absent(container_id=cid)
            sample = audit.get('sample')
            if sample is not None and sample.token in samples._ACTIVE_SAMPLES:
                assert fingerprint(sample.root / 'example.txt') == audit['snapshot_before']
                samples.cleanup_sandbox_sample(sample)
            assert sample is None or not sample.root.parent.exists()
        print(json.dumps({'mode': mode, 'container_absent': True, 'snapshot_released': True,
            'task_source_unchanged': True}), flush=True)
    asyncio.run(scenario())
    bindings.close(**scope)
    assert not source.parent.exists()
