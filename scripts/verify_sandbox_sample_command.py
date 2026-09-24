"""真实样例命令链：attach 输出、退出、超时/取消及响应丢失注入。

运行：PYTHONPATH=apps/api .venv/bin/python scripts/verify_sandbox_sample_command.py
Docker 操作均真实执行；仅测试预算、观测屏障与响应丢失由脚本注入。
"""

import asyncio
import json
from contextlib import ExitStack
from unittest.mock import patch

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_execution as execution
from app.services.runtime.sandbox import sandbox_sample_command as service
from app.services.runtime.sandbox.sandbox_identity import parse_created_container_id
from app.services.runtime.sandbox.sandbox_sample import SAMPLE_CONTENT, SAMPLE_FILENAME
from app.services.runtime.sandbox.sandbox_stop import stop_and_confirm_sandbox


READ_PROBE = r'''
import errno
import os
from pathlib import Path
source = Path('/workspace/example.txt')
assert Path.cwd() == Path('/tmp')
assert source.read_bytes() == b'sandbox sample\n'
assert os.statvfs('/workspace').f_flag & os.ST_RDONLY
try:
    source.write_bytes(b'forbidden\n')
except OSError as error:
    assert error.errno == errno.EROFS
else:
    raise AssertionError('write succeeded')
os.write(1, source.read_bytes())
os.write(2, b'sample-stderr\n')
'''


def fingerprint(sample):
    path = sample.root / SAMPLE_FILENAME
    info = path.stat()
    return path.read_bytes(), info.st_ino, info.st_mtime_ns


async def scenario(mode):
    program = READ_PROBE
    if mode == 'nonzero':
        program += '\nraise SystemExit(7)\n'
    elif mode == 'large':
        program = "import os; from pathlib import Path; assert Path('/workspace/example.txt').read_bytes()==b'sandbox sample\\n'; os.write(1,b'x'*200000); os.write(2,b'y'*200000)"
    elif mode in ('timeout', 'cancel', 'start_loss'):
        program = "import signal,time; from pathlib import Path; assert Path('/workspace/example.txt').read_bytes()==b'sandbox sample\\n'; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(600)"
    command = CommandRequest(argv=['/usr/local/bin/python', '-c', program])
    audit = {'mode': mode}
    report = {'mode': mode}
    actual_sample = service.create_sandbox_sample
    actual_create = service.create_sandbox_container
    actual_cleanup = service.cleanup_sandbox_sample
    actual_start = execution.start_sandbox_container
    actual_drain = execution.drain_docker_attach
    ready = asyncio.Event()
    created = asyncio.Event()
    task = None

    def create_sample():
        sample = actual_sample()
        audit['sample'] = sample
        audit['before'] = fingerprint(sample)
        assert audit['before'][0] == SAMPLE_CONTENT
        return sample

    async def create_container(*, spec):
        audit['spec'] = spec
        stdout = await actual_create(spec=spec)
        audit['container_id'] = parse_created_container_id(stdout)
        created.set()
        if mode == 'create_loss':
            raise OSError('injected lost create response after daemon success')
        if mode == 'create_cancel':
            await asyncio.Future()
        return stdout

    async def start_container(**kwargs):
        await actual_start(**kwargs)
        if mode == 'start_loss':
            raise OSError('injected lost start response after daemon success')

    async def drain(reader):
        class ObservedReader:
            tail = b''
            async def read(self, n):
                chunk = await reader.read(n)
                self.tail = (self.tail + chunk)[-32:]
                if b'ready\n' in self.tail:
                    ready.set()
                return chunk
        return await actual_drain(ObservedReader())

    def cleanup_sample(sample):
        # 此时由服务的成功缺失查询授权释放；核对文件副作用再做真实清理。
        assert fingerprint(sample) == audit['before']
        report['host_bytes_inode_mtime_unchanged'] = True
        actual_cleanup(sample)

    with ExitStack() as patches:
        patches.enter_context(patch.object(service, 'create_sandbox_sample', create_sample))
        patches.enter_context(patch.object(service, 'create_sandbox_container', create_container))
        patches.enter_context(patch.object(service, 'cleanup_sandbox_sample', cleanup_sample))
        patches.enter_context(patch.object(execution, 'start_sandbox_container', start_container))
        patches.enter_context(patch.object(execution, 'drain_docker_attach', drain))
        if mode == 'timeout':
            patches.enter_context(patch.object(execution, 'COMMAND_TIMEOUT_SECONDS', 1))
        primary_error = None
        try:
            task = asyncio.create_task(service.run_sample_sandbox_command(request=command))
            if mode == 'cancel':
                await asyncio.wait_for(ready.wait(), 10)
                task.cancel()
            if mode == 'create_cancel':
                await asyncio.wait_for(created.wait(), 10)
                task.cancel()
            try:
                result = await task
            except (service.SampleCommandCancelled, service.SampleCommandUnconfirmed) as error:
                assert mode in ('timeout', 'cancel', 'create_loss', 'create_cancel', 'start_loss')
                recovery = error.recovery
                assert recovery.sample is audit['sample']
                assert recovery.sample_root.exists() and not recovery.sample_cleaned
                assert not recovery.delete_attempted
                if mode in ('timeout', 'cancel', 'start_loss'):
                    assert recovery.start_attempted and recovery.stop_confirmed
                    assert recovery.container_id == audit['container_id']
                    if mode == 'timeout':
                        assert recovery.execution_reason == 'timed_out'
                else:
                    assert recovery.create_attempted and recovery.container_id is None
                if mode in ('cancel', 'create_cancel'):
                    assert isinstance(error, asyncio.CancelledError) and task.cancelled()
                assert recovery.command is None
                assert fingerprint(recovery.sample) == audit['before']
                report.update({
                    'expected_failure': type(error).__name__, 'phase': recovery.phase,
                    'start_attempted': recovery.start_attempted,
                    'stop_confirmed': recovery.stop_confirmed,
                    'source_retained_until_explicit_cleanup': True,
                    'host_bytes_inode_mtime_unchanged': True,
                })
            else:
                assert mode in ('read', 'nonzero', 'large')
                assert result.command.exit_code == (7 if mode == 'nonzero' else 0)
                assert result.command.oom_killed is False and result.command.daemon_error is False
                assert result.command.succeeded is (mode != 'nonzero')
                if mode == 'large':
                    assert result.command.stdout == 'x' * 65536 and result.command.stdout_truncated
                    assert result.command.stderr == 'y' * 65536 and result.command.stderr_truncated
                else:
                    assert result.command.stdout == 'sandbox sample\n'
                    assert result.command.stderr == 'sample-stderr\n'
                assert result.sample_cleaned and not audit['sample'].root.parent.exists()
                report.update({
                    'exit_code': result.command.exit_code,
                    'stdout_characters': len(result.command.stdout),
                    'stderr_characters': len(result.command.stderr),
                    'stdout_truncated': result.command.stdout_truncated,
                    'stderr_truncated': result.command.stderr_truncated,
                    'service_cleaned_source': True,
                })
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                sample = audit.get('sample')
                cid = audit.get('container_id')
                if cid is not None:
                    if not await client.is_sandbox_container_absent(container_id=cid):
                        # 仅验收脚本的显式故障收尾：重新授权/停止/按 ID 删除。
                        # 不把该路径接入生产自动恢复或自动重试。
                        stopped = await stop_and_confirm_sandbox(
                            request=command, execution_token=audit['spec'].execution_token,
                            expected_container_id=cid,
                        )
                        assert stopped.stopped
                        await client.remove_sandbox_container(container_id=cid)
                    assert await client.is_sandbox_container_absent(container_id=cid)
                    report['container_absent'] = True
                    report['container_id'] = cid
                elif 'spec' in audit:
                    raise RuntimeError('unknown create outcome; retain source and token')
                if sample is not None and sample.root.parent.exists():
                    assert fingerprint(sample) == audit['before']
                    actual_cleanup(sample)
                if sample is not None:
                    assert not sample.root.parent.exists()
                    report['sample_absent'] = True
                assert not [t for t in asyncio.all_tasks() if t.get_name() in (
                    'sandbox-attach-output', 'sandbox-execution-stop', 'docker-attach-close',
                    'docker-client-spawn', 'docker-client-collect', 'docker-client-cleanup',
                )]
                report['owned_async_tasks_absent'] = True
            except BaseException as cleanup_error:
                report['cleanup_unconfirmed'] = True
                if primary_error is not None:
                    primary_error.add_note(f'cleanup unconfirmed: {type(cleanup_error).__name__}')
                else:
                    raise
            finally:
                if 'spec' in audit:
                    report['execution_token'] = audit['spec'].execution_token
                if 'sample' in audit:
                    report['source'] = str(audit['sample'].root)
                print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


async def main():
    reports = []
    for mode in ('read', 'nonzero', 'large', 'timeout', 'cancel', 'create_loss', 'create_cancel', 'start_loss'):
        reports.append(await scenario(mode))
    print(json.dumps({'passed_scenarios': len(reports), 'all_resources_cleaned': True}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
