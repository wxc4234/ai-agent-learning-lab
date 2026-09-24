"""样例在 attach 前与启动边界重验；复用真实执行编排和帧解析。"""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from app.services.runtime.sandbox import sandbox_execution as service
from app.services.runtime.sandbox import sandbox_sample as samples
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, request
from tests.runtime.sandbox.test_sandbox_execution import install, assert_no_owned_tasks
from tests.runtime.sandbox.test_sandbox_sample import sample as sample  # noqa: PLC0414 -- pytest fixture export.
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414 -- pytest fixture export.


def mount_inspect(monkeypatch, sample, *, break_on=None):
    original = service.inspect_sandbox_container_by_id
    count = 0
    async def inspect(**kwargs):
        nonlocal count
        count += 1
        data = json.loads(await original(**kwargs))
        data[0]['HostConfig']['Mounts'] = [{
            'Type': 'bind', 'Source': str(sample.root), 'Target': '/workspace',
            'ReadOnly': True, 'BindOptions': {'Propagation': 'rprivate', 'NonRecursive': True},
        }]
        data[0]['Mounts'] = [{
            'Type': 'bind', 'Source': str(sample.root), 'Destination': '/workspace',
            'RW': False, 'Propagation': 'rprivate',
        }]
        if count == break_on:
            data[0]['Mounts'][0]['RW'] = True
        return json.dumps(data)
    monkeypatch.setattr(service, 'inspect_sandbox_container_by_id', inspect)


async def execute(sample=None):
    return await service.execute_created_sandbox(
        request=request(), execution_token=TOKEN, expected_container_id=CID, sample=sample,
    )


@pytest.mark.parametrize('code', [0, 7])
def test_sample_attach_output_and_exit_with_original_limits(monkeypatch, sample, code):
    async def scenario():
        lab = install(monkeypatch, code=code)
        mount_inspect(monkeypatch, sample)
        result = await execute(sample)
        assert result.streams.stdout.text == 'hello'
        assert result.streams.stderr.text == 'warning'
        assert result.exit.exit_code == code
        assert lab.calls == ['inspect', 'attach', 'inspect', 'start', 'inspect', 'close']
        assert_no_owned_tasks()
    asyncio.run(scenario())


@pytest.mark.parametrize('break_on', [1, 2])
def test_changed_mount_never_starts(monkeypatch, sample, break_on):
    async def scenario():
        lab = install(monkeypatch)
        mount_inspect(monkeypatch, sample, break_on=break_on)
        with pytest.raises(service.SandboxExecutionUnconfirmed) as caught:
            await execute(sample)
        assert not caught.value.start_attempted
        assert 'start' not in lab.calls and 'stop' not in lab.calls
        assert_no_owned_tasks()
    asyncio.run(scenario())


def test_default_entry_still_rejects_sample_mount(monkeypatch, sample):
    async def scenario():
        lab = install(monkeypatch)
        mount_inspect(monkeypatch, sample)
        with pytest.raises(service.SandboxExecutionUnconfirmed):
            await execute()
        assert lab.calls == ['inspect']
    asyncio.run(scenario())


def test_source_replaced_during_attach_is_rejected_before_start(monkeypatch, sample):
    async def scenario():
        lab = install(monkeypatch)
        mount_inspect(monkeypatch, sample)
        original_attach = service.open_docker_attach
        held = sample.root.with_name('held')
        @asynccontextmanager
        async def attach(**kwargs):
            async with original_attach(**kwargs) as reader:
                sample.root.rename(held)
                sample.root.symlink_to(held, target_is_directory=True)
                try:
                    yield reader
                finally:
                    sample.root.unlink()
                    held.rename(sample.root)
        monkeypatch.setattr(service, 'open_docker_attach', attach)
        with pytest.raises(service.SandboxExecutionUnconfirmed) as caught:
            await execute(sample)
        assert not caught.value.start_attempted
        assert 'close' in lab.calls and 'start' not in lab.calls
        assert samples.confirm_sandbox_sample_source(sample) == str(sample.root)
        assert_no_owned_tasks()
    asyncio.run(scenario())


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('stop_ok', [False, True])
def test_sample_timeout_cancel_preserves_source_and_joins(monkeypatch, sample, cancel, stop_ok):
    async def scenario():
        lab = install(monkeypatch, hold=True, stop_ok=stop_ok)
        mount_inspect(monkeypatch, sample)
        monkeypatch.setattr(service, 'COMMAND_TIMEOUT_SECONDS', 1 if cancel else 0.02)
        task = asyncio.create_task(execute(sample))
        try:
            await lab.started.wait()
            if cancel:
                task.cancel()
            with pytest.raises(service.SandboxExecutionCancelled if cancel else service.SandboxExecutionUnconfirmed) as caught:
                await task
            assert caught.value.start_attempted and caught.value.stop_confirmed is stop_ok
            if not cancel:
                assert caught.value.reason == 'timed_out'
            assert sample.root.exists()
            assert lab.calls[-2:] == ['close', 'stop']
            assert_no_owned_tasks()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
