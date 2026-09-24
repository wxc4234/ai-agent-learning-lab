"""补丁能力注册、可信上下文门禁及受控模型往返。"""

import asyncio
import json
from threading import get_ident
from types import SimpleNamespace

import pytest

from app.services.model.model_decision import DeepSeekDecisionMaker
from app.services.runtime.agent.agent_runtime import ToolErrorObservation, ToolObservation, run_agent_loop
from app.services.workspace.edits.workspace_edit_preview import TextEditPreview
from app.services.workspace.edits.workspace_file_preview import WorkspaceFileEditPreview
from app.services.workspace.edits.workspace_unified_patch import UnifiedPatchError
from app.tools import preview_file_patch as adapter
from app.tools.registry import TOOL_REGISTRY, ToolContextRequiredError, model_tools_for_context, tools_for_execution
from tests.model.test_model_decision import build_text_response, build_tool_response
from tests.tools.test_preview_file_patch_tool import ARGS, CONTEXT


@pytest.mark.parametrize('context', [None, {}, 'forged'])
def test_context_gate_before_executor(monkeypatch, context):
    tool = TOOL_REGISTRY['preview_file_patch']
    monkeypatch.setattr(adapter, 'preview_task_file_patch', lambda **kwargs: pytest.fail('must not execute'))
    assert tool.name not in {item.name for item in tools_for_execution(context=context)}
    assert tool.name not in {item['function']['name'] for item in model_tools_for_context(context)}
    with pytest.raises(ToolContextRequiredError):
        tool.execute(tool.validate_arguments(json.dumps(ARGS)), context=context)


@pytest.mark.parametrize('kind', ['success', 'truncated', 'conflict', 'unknown', 'invalid', 'no-context', 'empty-snapshot'])
def test_controlled_model_consumes_safe_observation(monkeypatch, kind):
    calls = []
    main_thread = get_ident()
    context = None if kind == 'no-context' else CONTEXT
    definitions = () if kind == 'empty-snapshot' else tools_for_execution(context=context)

    def generate(**kwargs):
        assert get_ident() != main_thread
        calls.append(kwargs)
        if kind == 'conflict':
            raise UnifiedPatchError('patch_context_mismatch')
        if kind == 'unknown':
            raise RuntimeError('PRIVATE_HOST_PATH')
        return WorkspaceFileEditPreview('file.txt', 'a' * 64, TextEditPreview('PRIVATE_CANDIDATE', 4, 4, '审阅Diff', kind == 'truncated'))

    turns = []
    async def create(**kwargs):
        turns.append(kwargs)
        assert kwargs.get('tools', []) == [tool.as_model_tool() for tool in definitions]
        observations = [message for message in kwargs['messages'] if message['role'] == 'tool']
        if not observations:
            args = ARGS | ({'user_id': 'PRIVATE_HOST_PATH'} if kind == 'invalid' else {})
            return build_tool_response(('patch', 'preview_file_patch', json.dumps(args)))
        raw = observations[-1]['content']
        assert 'PRIVATE_HOST_PATH' not in raw and 'PRIVATE_CANDIDATE' not in raw
        if kind in ('success', 'truncated'):
            public = json.loads(raw)
            assert public['status'] == 'preview_only' and 'updated_content' not in public
            assert public['diff_truncated'] is (kind == 'truncated')
        return build_text_response('预览已处理，未写入文件')

    monkeypatch.setattr(adapter, 'preview_task_file_patch', generate)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    maker = DeepSeekDecisionMaker(client=client, model='test', system_prompt='system', user_prompt='preview',
                                 tool_context=context, tool_definitions=definitions)
    result = asyncio.run(run_agent_loop(maker, tool_context=context, tool_definitions=definitions))
    assert len(turns) == 2 and result.answer == '预览已处理，未写入文件'
    assert len(calls) == (0 if kind in ('invalid', 'no-context', 'empty-snapshot') else 1)
    observation = result.observations[0]
    if kind in ('success', 'truncated'):
        assert isinstance(observation, ToolObservation)
    else:
        assert isinstance(observation, ToolErrorObservation)
        if kind in ('no-context', 'empty-snapshot'):
            assert observation.code == 'unknown_tool'
        elif kind == 'invalid':
            assert observation.code == 'invalid_tool_arguments'
            assert all('input' not in item and 'ctx' not in item for item in json.loads(observation.details))
        else:
            assert observation.details == ('patch_context_mismatch' if kind == 'conflict' else 'patch_preview_unavailable')
    if calls:
        assert calls[0] == ARGS | {'user_id': CONTEXT.user_id, 'workspace_id': CONTEXT.workspace_id, 'task_id': CONTEXT.task_id}
