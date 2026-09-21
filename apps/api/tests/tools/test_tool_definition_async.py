"""工具定义双入口的契约、上下文隔离与异步生命周期。"""

import asyncio
from dataclasses import FrozenInstanceError

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import (
    GetCurrentTimeArguments, ToolContextRequiredError, ToolDefinition,
)


CONTEXT = ToolExecutionContext(user_id=1, conversation_id="conversation", workspace_id="workspace", task_id="task")
ARGS = GetCurrentTimeArguments(utc_offset_hours=8)


def definition(**kwargs):
    return ToolDefinition(name="example", description="test", arguments_model=GetCurrentTimeArguments, **kwargs)


def invoke(tool, arguments=ARGS, **kwargs):
    if tool.is_async:
        return asyncio.run(tool.execute_async(arguments, **kwargs))
    return tool.execute(arguments, **kwargs)


@pytest.mark.parametrize("kwargs,error", [
    ({}, ValueError),
    ({"executor": lambda **kwargs: "ok", "async_executor": lambda **kwargs: "bad"}, ValueError),
    ({"executor": 1}, TypeError), ({"async_executor": "bad"}, TypeError),
    ({"executor": lambda **kwargs: "ok", "timeout_seconds": 0}, ValueError),
    ({"executor": lambda **kwargs: "ok", "timeout_seconds": -1}, ValueError),
])
def test_invalid_definition(kwargs, error):
    with pytest.raises(error):
        definition(**kwargs)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("required", [False, True])
def test_correct_payload_context_and_schema(asynchronous, required):
    calls = []

    def sync(**kwargs):
        calls.append(kwargs)
        return "ok"

    async def asynchronous_executor(**kwargs):
        return sync(**kwargs)

    tool = definition(**({"async_executor": asynchronous_executor} if asynchronous else {"executor": sync}),
                      requires_context=required)
    assert tool.is_async is asynchronous
    assert invoke(tool, context=CONTEXT) == "ok"
    assert calls == [{"utc_offset_hours": 8, **({"context": CONTEXT} if required else {})}]
    assert tool.validate_arguments('{"utc_offset_hours":8}') == ARGS
    assert tool.as_model_tool()["function"]["parameters"] == GetCurrentTimeArguments.model_json_schema()
    with pytest.raises(FrozenInstanceError):
        tool.requires_context = False


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("case", ["missing", "bad_context", "wrong_arguments", "injected_context"])
def test_preflight_blocks_executor(asynchronous, case):
    class ExtraArguments(BaseModel):
        model_config = ConfigDict(extra="allow")

    def forbidden(**kwargs):
        pytest.fail("preflight must reject before execution")

    async def forbidden_async(**kwargs):
        forbidden(**kwargs)

    kwargs = {"async_executor": forbidden_async} if asynchronous else {"executor": forbidden}
    tool = definition(**kwargs, requires_context=True)
    arguments, context, expected = ARGS, CONTEXT, TypeError
    if case == "missing":
        context, expected = None, ToolContextRequiredError
    elif case == "bad_context":
        context, expected = {}, ToolContextRequiredError
    elif case == "wrong_arguments":
        arguments = ExtraArguments()
    else:
        tool = ToolDefinition(name="extra", description="test", arguments_model=ExtraArguments,
                              requires_context=True, **kwargs)
        arguments = ExtraArguments(context="model-controlled")
        expected = ValueError
    with pytest.raises(expected):
        invoke(tool, arguments, context=context)


@pytest.mark.parametrize("alias", [False, True])
def test_context_field_or_alias_rejected(alias):
    class NamedContext(BaseModel):
        context: str

    class AliasedContext(BaseModel):
        value: str = Field(alias="context")

    with pytest.raises(ValueError):
        ToolDefinition(name="bad", description="bad", arguments_model=AliasedContext if alias else NamedContext,
                       executor=lambda **kwargs: "ok")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_wrong_entry_does_not_call_executor(asynchronous):
    def forbidden(**kwargs):
        pytest.fail("wrong entry must not call executor")

    async def forbidden_async(**kwargs):
        forbidden(**kwargs)

    tool = definition(**({"async_executor": forbidden_async} if asynchronous else {"executor": forbidden}))
    with pytest.raises(TypeError):
        if asynchronous:
            tool.execute(ARGS)
        else:
            asyncio.run(tool.execute_async(ARGS))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("value", [None, 0, {}, b"text"])
def test_non_string_result_rejected(asynchronous, value):
    async def async_executor(**kwargs):
        return value
    tool = definition(**({"async_executor": async_executor} if asynchronous else {"executor": lambda **kwargs: value}))
    with pytest.raises(TypeError):
        invoke(tool)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kind", ["ordinary", "safe", "cancel"])
def test_error_identity_preserved(asynchronous, kind):
    error = {"ordinary": RuntimeError("private"), "safe": SafeToolExecutionError("file_unavailable"),
             "cancel": asyncio.CancelledError("cancel")}[kind]

    def sync(**kwargs):
        raise error

    async def async_executor(**kwargs):
        sync(**kwargs)

    tool = definition(**({"async_executor": async_executor} if asynchronous else {"executor": sync}))
    with pytest.raises(type(error)) as caught:
        invoke(tool)
    assert caught.value is error


def test_async_waits_in_callers_task_and_cancellation_joins_cleanup():
    async def scenario():
        entered, cleanup_entered, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
        owner = None

        async def executor(**kwargs):
            assert asyncio.current_task() is owner
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleanup_entered.set()
                await release_cleanup.wait()

        tool = definition(async_executor=executor)
        owner = asyncio.create_task(tool.execute_async(ARGS))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert not owner.done()
            owner.cancel()
            await asyncio.wait_for(cleanup_entered.wait(), 1)
            assert not owner.done()
            release_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert owner.cancelled()
        finally:
            release_cleanup.set()
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
    asyncio.run(asyncio.wait_for(scenario(), 3))


def test_original_positional_constructor_still_works():
    tool = ToolDefinition("example", "test", GetCurrentTimeArguments, lambda **kwargs: "ok", 3, False)
    assert not tool.is_async and tool.execute(ARGS) == "ok"


def test_callable_returning_awaitable_is_supported():
    def executor(**kwargs):
        async def result():
            await asyncio.sleep(0)
            return "awaited"
        return result()
    assert invoke(definition(async_executor=executor)) == "awaited"
