"""明确超时来源，保留取消及恢复证据，重复取消不得遗留子任务。"""

import asyncio

import pytest

from app.services.runtime.agent.tool_wait import ToolWaitTimeout, wait_for_tool
from app.services.runtime.sandbox.command_recovery_journal import CommandRecoveryJournal
from app.services.runtime.sandbox.sandbox_command import SandboxCommandCancelled
from app.tools import run_command as command_tool
from tests.tools.test_run_command import RECOVERY
from tests.runtime.agent.test_async_tool_dispatch import register, consume, decide


@pytest.mark.parametrize("kind", ["ordinary_cancel", "special_cancel", "return", "error"])
def test_timeout_source_independent_of_cleanup_outcome(kind):
    async def executor():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            if kind == "special_cancel":
                raise SandboxCommandCancelled(recovery=RECOVERY) from None
            if kind == "return":
                return "too late"
            if kind == "error":
                raise RuntimeError("cleanup error") from None
            raise
    async def scenario():
        with pytest.raises(ToolWaitTimeout):
            await wait_for_tool(executor(), timeout=0.01)
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["timeout", "cancel", "special_cancel", "ordinary"])
def test_executor_exception_preserved_when_budget_not_expired(kind):
    error = {"timeout": TimeoutError("internal"), "cancel": asyncio.CancelledError(),
             "special_cancel": SandboxCommandCancelled(recovery=RECOVERY), "ordinary": ValueError("bad")}[kind]
    async def executor():
        raise error
    async def scenario():
        with pytest.raises(type(error)) as caught:
            await wait_for_tool(executor(), timeout=1)
        assert caught.value is error
    asyncio.run(scenario())


@pytest.mark.parametrize("timeout_first", [False, True])
def test_external_and_repeated_cancel_wait_for_cleanup(timeout_first):
    async def scenario():
        entered, cleaning, release, finished = (asyncio.Event() for _ in range(4))
        async def executor():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
                finished.set()
        task = asyncio.create_task(wait_for_tool(executor(), timeout=0.01 if timeout_first else 5))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if not timeout_first:
                task.cancel()
            await asyncio.wait_for(cleaning.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not finished.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(asyncio.wait_for(scenario(), 2))


@pytest.mark.parametrize("mode", ["result", "stream"])
@pytest.mark.parametrize("kind", ["budget", "internal_timeout"])
def test_runtime_classification_and_recovery(monkeypatch, mode, kind):
    async def scenario():
        journal = CommandRecoveryJournal()
        async def sandbox(**kwargs):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                raise SandboxCommandCancelled(recovery=RECOVERY) from None
        async def executor(**kwargs):
            if kind == "internal_timeout":
                raise TimeoutError("PRIVATE")
            return await command_tool.run_command(argv=["/bin/true"], recovery_journal=journal)
        monkeypatch.setattr(command_tool, "run_sandbox_command", sandbox)
        register(monkeypatch, executor, timeout=0.01)
        result = await consume(mode, decide=decide)
        observation = result.observations[0]
        assert observation.code == ("tool_timeout" if kind == "budget" else "tool_execution_failed")
        assert "PRIVATE" not in str(observation)
        if kind == "budget":
            assert journal.records[0].recovery is RECOVERY
            assert journal.records[0].status == "cancelled"
        else:
            assert journal.records == ()
    asyncio.run(scenario())
