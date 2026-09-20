"""真实事件循环验证并发准入、取消归还及循环边界。"""

import asyncio
from contextlib import ExitStack

import anyio
import pytest

from app.services.runtime.execution_budget import ExecutionBudget, ExecutionCapacityExceededError


@pytest.mark.parametrize("capacity", [True, False, 0, -1, 1.5, "2", None, [], {}])
def test_invalid_capacity(capacity):
    with pytest.raises(ValueError, match="正整数"):
        ExecutionBudget(capacity=capacity)


@pytest.mark.parametrize("capacity", [1, 2, 5])
def test_full_rejects_without_release_and_capacity_can_be_reused(capacity):
    budget = ExecutionBudget(capacity=capacity)

    async def scenario():
        assert budget.capacity == capacity and budget.in_use == 0
        for _ in range(2):
            with ExitStack() as stack:
                for used in range(capacity):
                    stack.enter_context(budget.reserve())
                    assert budget.in_use == used + 1
                for _ in range(3):
                    with pytest.raises(ExecutionCapacityExceededError) as caught, budget.reserve():
                        pytest.fail("full budget entered body")
                    assert caught.value.code == "execution_capacity_exceeded"
                    assert budget.in_use == capacity
            assert budget.in_use == 0

    asyncio.run(scenario())


def test_properties_readonly_and_instances_independent():
    first, second = ExecutionBudget(capacity=1), ExecutionBudget(capacity=1)
    with pytest.raises(AttributeError):
        first.capacity = 2
    with pytest.raises(AttributeError):
        first.in_use = 2

    async def scenario():
        with first.reserve(), second.reserve():
            assert first.in_use == second.in_use == 1
        assert first.in_use == second.in_use == 0

    asyncio.run(scenario())


def test_twenty_contenders_admit_only_three_without_queueing():
    async def scenario():
        budget = ExecutionBudget(capacity=3)
        release, attempted = asyncio.Event(), asyncio.Event()
        admitted, rejected = [], []

        async def contender(index):
            try:
                with budget.reserve():
                    admitted.append(index)
                    assert 1 <= budget.in_use <= 3
                    if len(admitted) + len(rejected) == 20:
                        attempted.set()
                    await release.wait()
            except ExecutionCapacityExceededError:
                rejected.append(index)
                if len(admitted) + len(rejected) == 20:
                    attempted.set()

        tasks = [asyncio.create_task(contender(index)) for index in range(20)]
        try:
            # 获准者持有名额直到所有竞争者尝试完，不用 sleep 猜竞争结果。
            await asyncio.wait_for(attempted.wait(), 2)
            assert len(admitted) == 3 and len(rejected) == 17
            assert budget.in_use == 3
        finally:
            release.set()
            await asyncio.gather(*tasks)
        assert budget.in_use == 0
        with budget.reserve():
            assert budget.in_use == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [ValueError("body"), asyncio.CancelledError()])
def test_exception_identity_preserved_and_capacity_returned(error):
    async def scenario():
        budget = ExecutionBudget(capacity=1)
        with pytest.raises(type(error)) as caught, budget.reserve():
            raise error
        assert caught.value is error
        assert budget.in_use == 0

    asyncio.run(scenario())


def test_repeated_task_cancel_returns_capacity():
    async def scenario():
        budget = ExecutionBudget(capacity=1)
        entered = asyncio.Event()

        async def work():
            with budget.reserve():
                entered.set()
                await asyncio.Future()

        task = asyncio.create_task(work())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert budget.in_use == 0
        with budget.reserve():
            assert budget.in_use == 1

    asyncio.run(scenario())


def test_anyio_cancel_returns_capacity():
    async def scenario():
        budget = ExecutionBudget(capacity=1)
        with anyio.CancelScope() as scope:
            with budget.reserve():
                scope.cancel()
                await anyio.lowlevel.checkpoint()
        assert scope.cancelled_caught
        assert budget.in_use == 0

    asyncio.run(scenario())


def test_capacity_held_until_inner_cleanup_exits():
    async def scenario():
        budget = ExecutionBudget(capacity=1)
        entered, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def work():
            with budget.reserve():
                try:
                    entered.set()
                    await asyncio.Future()
                finally:
                    cleaning.set()
                    await finish.wait()

        task = asyncio.create_task(work())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.wait_for(cleaning.wait(), 2)
            assert budget.in_use == 1
            with pytest.raises(ExecutionCapacityExceededError), budget.reserve():
                pytest.fail("cleanup still owns capacity")
        finally:
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert budget.in_use == 0

    asyncio.run(scenario())


def test_finished_context_cannot_release_another_reservation():
    async def scenario():
        budget = ExecutionBudget(capacity=1)
        manager = budget.reserve()
        with manager:
            assert budget.in_use == 1
        assert budget.in_use == 0
        # 已结束生成器再次退出不会重跑 finally 或减掉另一个名额。
        manager.__exit__(None, None, None)
        assert budget.in_use == 0
        with budget.reserve():
            manager.__exit__(None, None, None)
            assert budget.in_use == 1
        assert budget.in_use == 0

    asyncio.run(scenario())


def test_no_loop_rejection_does_not_bind_or_consume():
    budget = ExecutionBudget(capacity=1)
    with pytest.raises(RuntimeError, match="no running event loop"), budget.reserve():
        pytest.fail("must require loop")
    assert budget.in_use == 0

    async def scenario():
        with budget.reserve():
            assert budget.in_use == 1

    asyncio.run(scenario())
    assert budget.in_use == 0


def test_second_loop_rejected_after_first_loop_closed():
    budget = ExecutionBudget(capacity=1)

    async def first():
        with budget.reserve():
            assert budget.in_use == 1

    async def second():
        with pytest.raises(RuntimeError, match="绑定的事件循环"), budget.reserve():
            pytest.fail("cross-loop admission")
        assert budget.in_use == 0

    asyncio.run(first())
    asyncio.run(second())


def test_other_thread_loop_cannot_change_active_budget():
    async def scenario():
        budget = ExecutionBudget(capacity=1)

        async def wrong_loop():
            with pytest.raises(RuntimeError, match="绑定的事件循环"), budget.reserve():
                pytest.fail("cross-thread loop admission")

        with budget.reserve():
            await asyncio.to_thread(asyncio.run, wrong_loop())
            assert budget.in_use == 1
        assert budget.in_use == 0

    asyncio.run(scenario())
