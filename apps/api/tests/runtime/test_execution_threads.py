"""用真实线程与事件验证取消等待后仍可靠收尾，不依赖线程执行时长。"""

import asyncio
from contextvars import ContextVar
from threading import Event, get_ident

import pytest

from app.services.runtime.execution_threads import ExecutionThreads


class ControlledWork:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.release = Event()
        self.finished = Event()

    def __call__(self, failure=False):
        self.loop.call_soon_threadsafe(self.started.set)
        try:
            # 超时仅作为测试故障保险；测试用事件主动推进线程。
            if not self.release.wait(5):
                raise AssertionError("测试没有释放后台工作")
            if failure:
                raise ValueError("worker failure")
            return "finished"
        finally:
            self.finished.set()


async def checkpoint():
    # 让已就绪的取消与关闭协程执行，不用固定毫秒数猜测线程进度。
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def test_result_arguments_thread_and_context():
    marker = ContextVar("marker", default="missing")

    async def scenario():
        tracker = ExecutionThreads()
        main_thread = get_ident()
        marker.set("request-context")

        def work(value, *, suffix):
            return value + suffix, get_ident(), marker.get()

        result, worker_thread, context = await tracker.run(work, "a", suffix="b")
        assert result == "ab"
        assert worker_thread != main_thread
        assert context == "request-context"
        await tracker.wait_closed()
        await tracker.wait_closed()

    asyncio.run(scenario())


def test_worker_exception_reaches_caller_and_close_succeeds():
    async def scenario():
        tracker = ExecutionThreads()
        error = ValueError("expected")

        def fail():
            raise error

        with pytest.raises(ValueError) as caught:
            await tracker.run(fail)
        assert caught.value is error
        await tracker.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("empty", [False, True])
def test_closed_tracker_rejects_new_work(empty):
    async def scenario():
        tracker = ExecutionThreads()
        calls = []
        if not empty:
            await tracker.run(lambda: 1)
        await tracker.wait_closed()
        with pytest.raises(RuntimeError, match="收尾"):
            await tracker.run(lambda: calls.append("unexpected"))
        assert calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
@pytest.mark.parametrize("failure", [False, True])
def test_abandoned_waiter_still_drains_real_thread(mode, failure):
    async def scenario():
        tracker = ExecutionThreads()
        work = ControlledWork()
        caller = asyncio.create_task(tracker.run(work, failure))
        loop = asyncio.get_running_loop()
        reports = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: reports.append(context))
        try:
            await asyncio.wait_for(work.started.wait(), 2)
            if mode == "cancel":
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
            else:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(caller, timeout=0)

            assert not work.finished.is_set()
            closing = asyncio.create_task(tracker.wait_closed())
            await checkpoint()
            assert not closing.done()
            with pytest.raises(RuntimeError):
                await tracker.run(lambda: None)
            work.release.set()
            await asyncio.wait_for(closing, 2)
            assert work.finished.is_set()
            await tracker.wait_closed()
            await checkpoint()
            assert reports == []
        finally:
            work.release.set()
            await tracker.wait_closed()
            loop.set_exception_handler(previous)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_count", [1, 3])
def test_cancelled_closer_waits_and_does_not_cancel_other_closer(cancel_count):
    async def scenario():
        tracker = ExecutionThreads()
        work = ControlledWork()
        caller = asyncio.create_task(tracker.run(work))
        try:
            await asyncio.wait_for(work.started.wait(), 2)
            first = asyncio.create_task(tracker.wait_closed())
            second = asyncio.create_task(tracker.wait_closed())
            await checkpoint()
            for _ in range(cancel_count):
                first.cancel()
                await checkpoint()
                assert not first.done()
                assert not second.done()
                assert not work.finished.is_set()
            work.release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(first, 2)
            await asyncio.wait_for(second, 2)
            assert work.finished.is_set()
            assert await caller == "finished"
            await tracker.wait_closed()
        finally:
            work.release.set()
            await tracker.wait_closed()

    asyncio.run(scenario())


def test_failed_work_does_not_end_close_before_other_work():
    async def scenario():
        tracker = ExecutionThreads()
        first, second = ControlledWork(), ControlledWork()
        callers = [
            asyncio.create_task(tracker.run(first, True)),
            asyncio.create_task(tracker.run(second)),
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(first.started.wait(), second.started.wait()), 2,
            )
            closing = asyncio.create_task(tracker.wait_closed())
            await checkpoint()
            first.release.set()
            with pytest.raises(ValueError):
                await callers[0]
            assert not closing.done()
            second.release.set()
            await asyncio.wait_for(closing, 2)
            assert await callers[1] == "finished"
            assert first.finished.is_set() and second.finished.is_set()
        finally:
            first.release.set()
            second.release.set()
            await tracker.wait_closed()
            await asyncio.gather(*callers, return_exceptions=True)

    asyncio.run(scenario())


def test_trackers_are_independent():
    async def scenario():
        first, second = ExecutionThreads(), ExecutionThreads()
        work = ControlledWork()
        caller = asyncio.create_task(first.run(work))
        try:
            await asyncio.wait_for(work.started.wait(), 2)
            assert await second.run(lambda: 42) == 42
            await second.wait_closed()
            assert not work.finished.is_set()
        finally:
            work.release.set()
            await first.wait_closed()
            await caller

    asyncio.run(scenario())
