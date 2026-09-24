"""验收脚本的失败收尾：外部结果未知时保留来源，不扩大生产执行入口。"""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.runtime.sandbox import sandbox_sample as samples
from tests.runtime.sandbox.test_sandbox_creation import CID
from tests.runtime.sandbox.test_sandbox_sample import mounted_payload
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414 -- 显式重导出供 pytest 注册共用夹具。


@pytest.fixture
def probe_module():
    source = Path(__file__).resolve().parents[5] / 'scripts' / 'verify_sandbox_sample.py'
    spec = importlib.util.spec_from_file_location('sample_probe_under_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('failure', ['none', 'create_receipt', 'probe', 'stop', 'absence', 'offline'])
def test_probe_cleanup_requires_confirmed_container_absence(probe_module, sample_base, monkeypatch, failure):
    module = probe_module
    events = []
    state = {'status': 'created'}
    real_create = module.create_sandbox_sample
    real_cleanup = module.cleanup_sandbox_sample

    def create_sample():
        state['sample'] = real_create()
        return state['sample']

    def cleanup_sample(sample):
        events.append('sample_cleanup')
        real_cleanup(sample)

    async def create_container(*, spec):
        state['spec'] = spec
        events.append('create')
        if failure in ('create_receipt', 'offline'):
            raise RuntimeError('creation result unknown')
        return CID + '\n'

    def payload():
        data = mounted_payload(state['sample'])
        spec = state['spec']
        item = data[0]
        item['Name'] = '/' + spec.container_name
        item['Config']['Labels']['ai-agent-learning-lab.execution'] = spec.execution_token
        item['Config']['Cmd'][-1] = 'import time; time.sleep(600)'
        item['State'].update({
            'Status': state['status'], 'Running': state['status'] == 'running',
            'Pid': 123 if state['status'] == 'running' else 0,
            'Paused': False, 'Restarting': False, 'Dead': False,
        })
        return json.dumps(data)

    async def inspect_id(**kwargs):
        assert kwargs['container_id'] == CID
        return payload()

    async def inspect_token(**kwargs):
        events.append('recover')
        if failure == 'offline':
            raise RuntimeError('daemon unavailable')
        assert kwargs['execution_token'] == state['spec'].execution_token
        return payload()

    async def start(**kwargs):
        events.append('start')
        state['status'] = 'running'

    async def run_probe(arguments):
        events.append('probe')
        assert arguments[:3] == ('container', 'exec', CID)
        if failure == 'probe':
            raise RuntimeError('probe failed')
        return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text='{"read": true}')))

    async def stop(**kwargs):
        events.append('stop')
        assert kwargs['expected_container_id'] == CID
        if failure == 'stop':
            raise RuntimeError('stop unconfirmed')
        return SimpleNamespace(stopped=True)

    async def remove(**kwargs):
        events.append('remove')
        assert kwargs['container_id'] == CID

    async def absent(**kwargs):
        events.append('absence')
        return failure != 'absence'

    monkeypatch.setattr(module, 'create_sandbox_sample', create_sample)
    monkeypatch.setattr(module, 'cleanup_sandbox_sample', cleanup_sample)
    monkeypatch.setattr(module, 'create_sandbox_container', create_container)
    monkeypatch.setattr(module, 'inspect_sandbox_container_by_id', inspect_id)
    monkeypatch.setattr(module, 'inspect_sandbox_container', inspect_token)
    monkeypatch.setattr(module, 'start_sandbox_container', start)
    monkeypatch.setattr(module, '_run_docker_client', run_probe)
    monkeypatch.setattr(module, 'stop_and_confirm_sandbox', stop)
    monkeypatch.setattr(module, 'remove_sandbox_container', remove)
    monkeypatch.setattr(module, 'is_sandbox_container_absent', absent)

    if failure == 'none':
        asyncio.run(module.main())
    else:
        with pytest.raises((RuntimeError, AssertionError)):
            asyncio.run(module.main())

    sample = state['sample']
    try:
        if failure in ('stop', 'absence', 'offline'):
            assert 'sample_cleanup' not in events
            assert sample.root.exists()
            if failure in ('stop', 'offline'):
                assert 'remove' not in events
        else:
            assert not sample.root.parent.exists()
            assert events.index('remove') < events.index('absence') < events.index('sample_cleanup')
        if failure in ('create_receipt', 'offline'):
            assert events.count('create') == 1
            assert 'start' not in events
    finally:
        # 此处 Docker 完全由 stub 替代，可以回收测试自己保留的样例。
        if samples._ACTIVE_SAMPLES.get(sample.token) is sample:
            real_cleanup(sample)
