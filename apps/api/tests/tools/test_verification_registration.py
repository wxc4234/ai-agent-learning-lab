"""请求能力门禁、原上下文与关闭后的记录保留；不调用数据库或Docker。"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from typing import Literal

import pytest

from app.routers.chat import chat_execution as owners
from app.schemas import ChatRequest
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.runtime.execution.verification_recovery_store import VerificationRecoveryStore
from app.services.runtime.verification import recorded_task_verification as recorded
from app.services.runtime.verification.contracts import VerificationRequest
from app.services.runtime.verification.sandbox_verification import adapt_sample_verification
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings, TaskSampleStatus
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, ToolContextRequiredError, tools_for_execution
from tests.assertions import require_value
from tests.runtime.verification.test_sandbox_verification import receipt

CONTEXT = ToolExecutionContext(1, 'c', 'w', 't')


def owner(monkeypatch, status: Literal['ready', 'missing', 'busy', 'sealed'] = 'ready'):
    bindings = TaskSampleBindings()
    monkeypatch.setattr(bindings, 'read_status', lambda **kw: TaskSampleStatus(status, None))
    monkeypatch.setattr(owners, 'load_tool_execution_context', lambda **kw: CONTEXT)
    monkeypatch.setattr(owners, 'finish_agent_run', lambda *a: None)
    return owners.ChatExecution(
        user_id=1, body=ChatRequest(session_id='c', prompt='verify'), threads=ExecutionThreads(),
        sample_bindings=bindings, verification_recovery_store=VerificationRecoveryStore(),
    )


@pytest.mark.parametrize('status', ['missing', 'busy', 'sealed'])
def test_unavailable_status_omits_tool(monkeypatch, status):
    execution = owner(monkeypatch, status)
    async def scenario():
        try:
            assert await execution.bind_verification_tool(CONTEXT) is None
            assert execution.verification_scope is None
        finally:
            await execution.close()
    asyncio.run(scenario())


def test_query_failure_never_becomes_capability(monkeypatch):
    execution = owner(monkeypatch)
    def fail(**kwargs):
        raise OSError('PRIVATE')
    monkeypatch.setattr(require_value(execution.sample_bindings), 'read_status', fail)
    async def scenario():
        try:
            with pytest.raises(OSError):
                await execution.bind_verification_tool(CONTEXT)
            assert execution.verification_scope is None
        finally:
            await execution.close()
    asyncio.run(scenario())


def test_registry_identity_and_lazy_application_ownership(monkeypatch):
    execution = owner(monkeypatch)
    monkeypatch.setattr(recorded, 'run_task_verification', AsyncMock(return_value=adapt_sample_verification(receipt())))
    async def scenario():
        execution.creation = asyncio.create_task(asyncio.sleep(0, result=1))
        await execution.creation
        definition = require_value(await execution.bind_verification_tool(CONTEXT))
        assert execution.verification_scope is None
        definitions = tools_for_execution(context=CONTEXT, verification_definition=definition)
        bound = next(value for value in definitions if value.name == 'verify_task_sample')
        assert bound.name not in TOOL_REGISTRY
        request = VerificationRequest(plan_id='sample_unittest_v1')
        with pytest.raises(ToolContextRequiredError):
            await bound.execute_async(request, context=replace(CONTEXT))
        await bound.execute_async(request, context=CONTEXT)
        scope = require_value(execution.verification_scope)
        await execution.close()
        assert require_value(execution.verification_recovery_store).get(context=CONTEXT, run_id=1) is scope
        assert scope.journal.records[0].status == 'completed'
        with pytest.raises(SafeToolExecutionError):
            await bound.execute_async(request, context=CONTEXT)
    asyncio.run(scenario())


def test_changed_context_blocks_before_record_allocation(monkeypatch):
    execution = owner(monkeypatch)
    async def scenario():
        execution.creation = asyncio.create_task(asyncio.sleep(0, result=1))
        await execution.creation
        definition = require_value(await execution.bind_verification_tool(CONTEXT))
        monkeypatch.setattr(owners, 'load_tool_execution_context', lambda **kw: replace(CONTEXT, task_id='other'))
        try:
            with pytest.raises(SafeToolExecutionError):
                await definition.execute_async(VerificationRequest(plan_id='sample_unittest_v1'), context=CONTEXT)
            assert execution.verification_scope is None
        finally:
            await execution.close()
    asyncio.run(scenario())
