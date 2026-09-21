"""有界恢复记录及适配器在超时转换前保存证据。"""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.command_recovery_journal import (
    MAX_COMMAND_RECOVERY_RECORDS, CommandRecoveryJournal, CommandRecoveryJournalUnavailable,
)
from app.services.runtime.sandbox.sandbox_command import SandboxCommandCancelled, SandboxCommandUnconfirmed
from app.tools import run_command as tool
from tests.tools.test_run_command import RECOVERY


def request():
    return CommandRequest(argv=["/bin/true"])


def test_request_and_records_are_independent_snapshots():
    journal = CommandRecoveryJournal()
    command = request()
    index = journal.reserve(command)
    before = journal.records
    command.argv.clear()
    rebuilt = before[0].build_request()
    assert rebuilt == request()
    rebuilt.argv.clear()
    assert before[0].build_request() == request()
    journal.finish(index, status="unconfirmed", recovery=RECOVERY)
    assert before[0].status == "pending" and journal.records[0].status == "unconfirmed"
    with pytest.raises(FrozenInstanceError):
        journal.records[0].status = "completed"


def test_capacity_is_hard_and_completed_slots_are_not_overwritten():
    journal = CommandRecoveryJournal()
    for index in range(MAX_COMMAND_RECOVERY_RECORDS):
        assert journal.reserve(request()) == index
        journal.finish(index, status="completed")
    before = journal.records
    with pytest.raises(CommandRecoveryJournalUnavailable):
        journal.reserve(request())
    assert journal.records == before and len(before) == 16


@pytest.mark.parametrize("status", ["completed", "unconfirmed", "cancelled"])
def test_close_keeps_records_and_allows_inflight_finish(status):
    journal = CommandRecoveryJournal()
    index = journal.reserve(request())
    journal.close()
    journal.close()
    with pytest.raises(CommandRecoveryJournalUnavailable):
        journal.reserve(request())
    journal.finish(index, status=status)
    assert journal.records[0].status == status
    with pytest.raises(ValueError):
        journal.finish(index, status=status)


@pytest.mark.parametrize("index", [-1, 1, True, False, "0", None, 0.0])
def test_invalid_index_cannot_change_pending_record(index):
    journal = CommandRecoveryJournal()
    journal.reserve(request())
    with pytest.raises(ValueError):
        journal.finish(index, status="completed")
    assert journal.records[0].status == "pending"


@pytest.mark.parametrize("kwargs", [
    {"status": "pending"}, {"status": "invalid"},
    {"status": "completed", "recovery": RECOVERY},
    {"status": "unconfirmed", "recovery": {}},
])
def test_invalid_finish_keeps_record(kwargs):
    journal = CommandRecoveryJournal()
    journal.reserve(request())
    with pytest.raises((TypeError, ValueError)):
        journal.finish(0, **kwargs)
    assert journal.records[0].status == "pending"


@pytest.mark.parametrize("value", [None, {}, CommandRequest.model_construct(argv=[])])
def test_invalid_request_does_not_consume_capacity(value):
    journal = CommandRecoveryJournal()
    with pytest.raises((TypeError, ValueError)):
        journal.reserve(value)
    assert journal.records == ()


@pytest.mark.parametrize("kind", ["closed", "full", "invalid"])
def test_unavailable_journal_blocks_external_execution(monkeypatch, kind):
    journal = CommandRecoveryJournal()
    if kind == "closed":
        journal.close()
    elif kind == "full":
        for _ in range(16):
            journal.reserve(request())
    else:
        journal = None

    async def forbidden(**kwargs):
        pytest.fail("no execution without recording capacity")

    monkeypatch.setattr(tool, "run_sandbox_command", forbidden)
    with pytest.raises(tool.CommandToolExecutionError) as caught:
        asyncio.run(tool.run_command(argv=["/bin/true"], recovery_journal=journal))
    assert caught.value.code == "command_recovery_unavailable"


@pytest.mark.parametrize("kind", ["known_error", "unknown_error", "known_cancel", "unknown_cancel"])
def test_evidence_is_saved_before_error_leaves_adapter(monkeypatch, kind):
    journal = CommandRecoveryJournal()
    errors = {
        "known_error": SandboxCommandUnconfirmed(recovery=RECOVERY),
        "unknown_error": RuntimeError("PRIVATE"),
        "known_cancel": SandboxCommandCancelled(recovery=RECOVERY),
        "unknown_cancel": asyncio.CancelledError(),
    }
    async def executor(**kwargs):
        assert journal.records[0].status == "pending"
        raise errors[kind]
    monkeypatch.setattr(tool, "run_sandbox_command", executor)
    expected = asyncio.CancelledError if "cancel" in kind else tool.CommandToolExecutionError
    with pytest.raises(expected) as caught:
        asyncio.run(tool.run_command(argv=["/bin/true"], recovery_journal=journal))
    record = journal.records[0]
    assert record.status == ("cancelled" if "cancel" in kind else "unconfirmed")
    assert record.recovery is (RECOVERY if kind.startswith("known") else None)
    assert record.build_request() == request()
    if "cancel" in kind:
        assert caught.value is errors[kind]


@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.parametrize("specialized", [False, True])
def test_wait_for_conversion_does_not_lose_recovery(monkeypatch, timeout, specialized):
    async def scenario():
        journal = CommandRecoveryJournal()
        entered = asyncio.Event()

        async def executor(**kwargs):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                if specialized:
                    raise SandboxCommandCancelled(recovery=RECOVERY) from None
                raise

        monkeypatch.setattr(tool, "run_sandbox_command", executor)
        task = asyncio.create_task(asyncio.wait_for(
            tool.run_command(argv=["/bin/true"], recovery_journal=journal),
            timeout=0.03 if timeout else 5,
        ))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            # 作用域开始关闭也不能阻止在途异常写回证据。
            journal.close()
            if not timeout:
                task.cancel()
            with pytest.raises(TimeoutError if timeout and not specialized else asyncio.CancelledError):
                await task
            assert journal.records[0].status == "cancelled"
            assert journal.records[0].recovery is (RECOVERY if specialized else None)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(asyncio.wait_for(scenario(), 2))


def test_concurrent_reservations_are_bounded_and_journals_isolated(monkeypatch):
    async def scenario():
        journal, other = CommandRecoveryJournal(), CommandRecoveryJournal()
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def executor(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 16:
                entered.set()
            await release.wait()
            raise SandboxCommandUnconfirmed(recovery=RECOVERY)

        monkeypatch.setattr(tool, "run_sandbox_command", executor)
        tasks = [asyncio.create_task(tool.run_command(argv=["/bin/true"], recovery_journal=journal))
                 for _ in range(17)]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert calls == 16 and len(journal.records) == 16 and other.records == ()
            assert sum(error.code == "command_recovery_unavailable" for error in results) == 1
            assert all(record.status == "unconfirmed" for record in journal.records)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(asyncio.wait_for(scenario(), 2))


@pytest.mark.parametrize("code", [0, 7])
def test_normal_completion_records_cleanup_completion_not_exit_success(monkeypatch, code):
    from app.services.runtime.sandbox.sandbox_command import SandboxCommandResult
    from app.services.runtime.sandbox.sandbox_command_result import build_command_result
    from app.services.runtime.sandbox.sandbox_cleanup import SandboxCleanupResult
    from tests.runtime.sandbox.test_sandbox_command_result import execution

    journal = CommandRecoveryJournal()
    command = build_command_result(execution(code=code))

    async def executor(**kwargs):
        return SandboxCommandResult(command=command, cleanup=SandboxCleanupResult(
            execution_token="PRIVATE", container_id="PRIVATE"))

    monkeypatch.setattr(tool, "run_sandbox_command", executor)
    text = asyncio.run(tool.run_command(argv=["/bin/true"], recovery_journal=journal))
    assert text == command.model_dump_json() and "PRIVATE" not in text
    assert journal.records[0].status == "completed" and journal.records[0].recovery is None


def test_invalid_command_does_not_reserve_record():
    journal = CommandRecoveryJournal()
    with pytest.raises(tool.CommandToolExecutionError) as caught:
        asyncio.run(tool.run_command(argv=["relative"], recovery_journal=journal))
    assert caught.value.code == "command_request_rejected" and journal.records == ()
