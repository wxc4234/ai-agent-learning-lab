"""真实 Docker 显式清理验收，故障仅注入回执边界；不暴露项目路径。

运行：PYTHONPATH=apps/api .venv/bin/python scripts/verify_sandbox_sample_cleanup.py
"""

import asyncio
import json
from unittest.mock import patch

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_sample_command as commands
from app.services.runtime.sandbox import sandbox_sample_cleanup as cleanup
from app.services.runtime.sandbox.sandbox_stop import stop_and_confirm_sandbox


async def verify(mode):
    request = CommandRequest(argv=['/usr/local/bin/python', '-c', 'import time; time.sleep(600)'])
    recovery = None
    actual_inspect = commands.inspect_sandbox_container_by_id
    async def fail_inspect(**kwargs):
        # 编排已收到真实完整 ID，随后模拟读取失败，保留其原始恢复对象。
        await actual_inspect(**kwargs)
        raise OSError('injected inspection failure')
    with patch.object(commands, 'inspect_sandbox_container_by_id', fail_inspect):
        try:
            await commands.run_sample_sandbox_command(request=request)
        except commands.SampleCommandUnconfirmed as error:
            recovery = error.recovery
    assert recovery and recovery.container_id and recovery.sample
    cid = recovery.container_id
    try:
        if mode in ('running', 'exited'):
            await client.start_sandbox_container(container_id=cid)
            if mode == 'running':
                try:
                    await cleanup.cleanup_sample_command(recovery=recovery)
                except cleanup.SampleCleanupUnconfirmed as error:
                    assert not error.recovery.delete_attempted and recovery.sample.root.exists()
                else:
                    raise AssertionError('running container was accepted')
            await stop_and_confirm_sandbox(
                request=request, execution_token=recovery.execution_token, expected_container_id=cid,
            )
        if mode == 'absent':
            await client.remove_sandbox_container(container_id=cid)
        if mode in ('receipt_loss', 'cancel'):
            actual_remove = cleanup.remove_sandbox_container
            async def lost_receipt(**kwargs):
                await actual_remove(**kwargs)
                if mode == 'cancel':
                    raise asyncio.CancelledError
                raise OSError('injected receipt loss')
            with patch.object(cleanup, 'remove_sandbox_container', lost_receipt):
                try:
                    await cleanup.cleanup_sample_command(recovery=recovery)
                except (cleanup.SampleCleanupUnconfirmed, cleanup.SampleCleanupCancelled) as error:
                    recovery = error.recovery
                    assert recovery.delete_attempted and not recovery.container_absent
                    assert recovery.sample.root.exists()
                else:
                    raise AssertionError('lost receipt was accepted')
        result = await cleanup.cleanup_sample_command(recovery=recovery)
        assert result.recovery.sample_cleaned and result.recovery.container_absent
        assert not recovery.sample.root.parent.exists()
        assert await client.is_sandbox_container_absent(container_id=cid)
        print(json.dumps({'case': mode, 'container_id': cid, 'absent': True, 'source_released': True}), flush=True)
    except BaseException:
        # 失败时保留定位，不以无条件 finally 清理掩盖待核对的副作用。
        print(json.dumps({'unconfirmed_container': cid, 'sample_token': recovery.sample_token}), flush=True)
        raise


async def main():
    for mode in ('created', 'exited', 'absent', 'running', 'receipt_loss', 'cancel'):
        await verify(mode)


if __name__ == '__main__':
    asyncio.run(main())
