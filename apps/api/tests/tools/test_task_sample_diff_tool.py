import asyncio
import json
from dataclasses import replace
from hashlib import sha256

import pytest

from app.tools import task_sample_diff as tool
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, ToolContextRequiredError, tools_for_execution
from tests.tools.test_verification_registration import CONTEXT, owner
from tests.assertions import require_value


def result(data=b''):
    return tool.TaskSampleDiff(sha256(b'old\n').hexdigest(), sha256(b'new\n').hexdigest(), data)


@pytest.mark.parametrize('mode', ['empty', 'unicode', 'extra', 'encoding', 'raw_limit', 'json_limit', 'baseline', 'digest', 'error'])
def test_protocol(monkeypatch, mode):
    value = result('你好\n'.encode() if mode == 'unicode' else b'')
    if mode == 'encoding':
        value = result(b'\xff')
    elif mode == 'raw_limit':
        monkeypatch.setattr(tool, 'MAX_DIFF_BYTES', 0)
        value = result(b'a')
    elif mode == 'json_limit':
        monkeypatch.setattr(tool, 'MAX_PUBLIC_BYTES', 1)
    elif mode == 'baseline':
        value = replace(value, baseline_sha256='0' * 64)
    elif mode == 'digest':
        value = replace(value, content_sha256='PRIVATE')
    def read(**kwargs):
        if mode == 'extra':
            pytest.fail('invalid arguments reached executor')
        if mode == 'error':
            raise OSError('PRIVATE /secret')
        return value
    monkeypatch.setattr(tool, 'read_task_sample_diff', read)
    definition = tool.make_task_sample_diff_definition(tool.TaskSampleBindings())
    executor = require_value(definition.executor)
    if mode in ('empty', 'unicode'):
        public = json.loads(executor(context=CONTEXT))
        assert public['source'] == 'task_application_sample'
        assert public['diff'] == value.data.decode()
        assert public['byte_count'] == len(value.data)
        assert set(public) == {'source', 'status', 'comparison', 'path', 'baseline_sha256', 'content_sha256', 'format', 'encoding', 'byte_count', 'diff'}
    else:
        with pytest.raises(SafeToolExecutionError) as caught:
            executor(context=CONTEXT, **({'path': '/secret'} if mode == 'extra' else {}))
        assert 'PRIVATE' not in str(caught.value) and '/secret' not in str(caught.value)


@pytest.mark.parametrize('status', ['ready', 'missing', 'busy', 'sealed'])
def test_request_capability(monkeypatch, status):
    execution = owner(monkeypatch, status)
    monkeypatch.setattr(tool, 'read_task_sample_diff', lambda **kw: result())
    async def scenario():
        try:
            definition = await execution.bind_sample_diff_tool(CONTEXT)
            if status != 'ready':
                assert definition is None
                return
            definitions = tools_for_execution(context=CONTEXT, sample_diff_definition=require_value(definition))
            bound = next(d for d in definitions if d.name == 'read_task_sample_diff')
            assert bound.name not in TOOL_REGISTRY
            with pytest.raises(ToolContextRequiredError):
                bound.execute(tool.TaskSampleDiffArguments(), context=replace(CONTEXT))
            assert json.loads(bound.execute(tool.TaskSampleDiffArguments(), context=CONTEXT))['status'] == 'complete'
            await execution.close()
            with pytest.raises(SafeToolExecutionError):
                bound.execute(tool.TaskSampleDiffArguments(), context=CONTEXT)
        finally:
            await execution.close()
    asyncio.run(scenario())
