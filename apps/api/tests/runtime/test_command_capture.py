"""双路读取任务的并发、失败及取消收尾；不启动命令进程。"""

import asyncio
from builtins import ExceptionGroup
from dataclasses import FrozenInstanceError

import pytest

from app.services.runtime.command_capture import drain_command_streams
from app.services.runtime.command_contracts import CommandResult


def run(scenario):
    # 回归若错误地串行读取或遗漏取消，应超时失败而非挂住整套测试。
    asyncio.run(asyncio.wait_for(scenario(), 2))


def assert_no_readers():
    assert not [task for task in asyncio.all_tasks() if task.get_name() in {
        "command-stdout-drain", "command-stderr-drain",
    } and not task.done()]


@pytest.mark.parametrize("out,err", [(b"", b""), (b"out", "错误".encode()), (b"x" * 70_000, b"short"), (b"short", b"y" * 70_000)])
def test_real_memory_streams_and_result_contract(out, err):
    async def scenario():
        streams = [asyncio.StreamReader(), asyncio.StreamReader()]
        for stream, data in zip(streams, (out, err), strict=True):
            stream.feed_data(data)
            stream.feed_eof()
        result = await drain_command_streams(stdout=streams[0], stderr=streams[1])
        for snapshot, data in zip((result.stdout, result.stderr), (out, err), strict=True):
            assert snapshot.text == data[:65_536].decode("utf-8")
            assert snapshot.truncated is (len(data) > 65_536)
        assert all(stream.at_eof() for stream in streams)
        contract = CommandResult(status="exited", exit_code=1, duration_ms=0,
                                 stdout=result.stdout.text, stderr=result.stderr.text,
                                 stdout_truncated=result.stdout.truncated,
                                 stderr_truncated=result.stderr.truncated)
        assert CommandResult.model_validate_json(contract.model_dump_json()) == contract
        with pytest.raises(FrozenInstanceError):
            result.stdout = result.stderr
        assert_no_readers()
    run(scenario)


def test_same_reader_rejected_without_consuming():
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b"untouched")
        reader.feed_eof()
        with pytest.raises(ValueError):
            await drain_command_streams(stdout=reader, stderr=reader)
        assert await reader.read() == b"untouched"
        assert_no_readers()
    run(scenario)


def test_both_reads_start_before_either_can_finish():
    async def scenario():
        entered = [asyncio.Event(), asyncio.Event()]

        class Reader:
            def __init__(self, index):
                self.index = index
                self.sent = False

            async def read(self, n):
                assert n == 4096
                if self.sent:
                    return b""
                self.sent = True
                entered[self.index].set()
                await entered[1 - self.index].wait()
                return str(self.index).encode()

        result = await drain_command_streams(stdout=Reader(0), stderr=Reader(1))
        assert result.stdout.text == "0"
        assert result.stderr.text == "1"
        assert_no_readers()
    run(scenario)


@pytest.mark.parametrize("first", [0, 1])
def test_one_eof_does_not_end_other_read(first):
    async def scenario():
        streams = [asyncio.StreamReader(), asyncio.StreamReader()]
        streams[first].feed_eof()
        task = asyncio.create_task(drain_command_streams(stdout=streams[0], stderr=streams[1]))
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not task.done()
            streams[1 - first].feed_data(b"late")
            streams[1 - first].feed_eof()
            result = await task
            assert (result.stdout, result.stderr)[1 - first].text == "late"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert_no_readers()
    run(scenario)


@pytest.mark.parametrize("failed_side", [0, 1])
def test_failure_cancels_and_waits_for_sibling_cleanup(failed_side):
    async def scenario():
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()
        failure = OSError("read failure")

        class Broken:
            async def read(self, n):
                await entered.wait()
                raise failure

        class Waiting:
            async def read(self, n):
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    cleaning.set()
                    await release.wait()
                    finished.set()

        readers = [Waiting(), Waiting()]
        readers[failed_side] = Broken()
        task = asyncio.create_task(drain_command_streams(stdout=readers[0], stderr=readers[1]))
        try:
            await cleaning.wait()
            assert not task.done(), "must wait for sibling cleanup"
            release.set()
            with pytest.raises(ExceptionGroup) as caught:
                await task
            assert caught.value.exceptions == (failure,)
            assert finished.is_set()
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert_no_readers()
    run(scenario)


def test_parent_cancellation_waits_for_both_cleanups():
    async def scenario():
        entered = [asyncio.Event(), asyncio.Event()]
        cleaning = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        finished = []

        class Reader:
            def __init__(self, index):
                self.index = index

            async def read(self, n):
                entered[self.index].set()
                try:
                    await asyncio.Future()
                finally:
                    cleaning[self.index].set()
                    await release.wait()
                    finished.append(self.index)

        task = asyncio.create_task(drain_command_streams(stdout=Reader(0), stderr=Reader(1)))
        try:
            await asyncio.gather(*(event.wait() for event in entered))
            task.cancel()
            await asyncio.gather(*(event.wait() for event in cleaning))
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert sorted(finished) == [0, 1]
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert_no_readers()
    run(scenario)


def test_simultaneous_failures_are_both_retained():
    async def scenario():
        failures = [OSError("stdout failure"), ValueError("stderr failure")]

        class Broken:
            def __init__(self, error):
                self.error = error

            async def read(self, n):
                raise self.error

        with pytest.raises(ExceptionGroup) as caught:
            await drain_command_streams(stdout=Broken(failures[0]), stderr=Broken(failures[1]))
        assert set(caught.value.exceptions) == set(failures)
        assert_no_readers()
    run(scenario)
