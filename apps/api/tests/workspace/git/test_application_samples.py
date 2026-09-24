"""应用所有权、真实临时Git清理及可控关闭故障；数据库授权另由专项覆盖。"""

import asyncio
from threading import Event

import pytest
from fastapi import FastAPI, Request

from app import main
from app.services.workspace.git import application_samples as service
from app.services.workspace.git import task_git_samples as registry


SCOPE = {'user_id': 1, 'workspace_id': 'workspace', 'task_id': 'task'}


def request(application):
    return Request({'type': 'http', 'app': application})


@pytest.fixture(autouse=True)
def boundaries(monkeypatch):
    monkeypatch.setattr(main, 'check_database_ready', lambda: None)
    monkeypatch.setattr(registry, '_authorize', lambda key: (1, 2, 3))
    async def close_broker():
        pass
    monkeypatch.setattr(main, 'close_cancellation_broker', close_broker)


@pytest.mark.parametrize('failure', ['none', 'body', 'broker'])
def test_real_cleanup_and_new_instance_each_lifespan(monkeypatch, failure):
    application = FastAPI()
    managers = []
    if failure == 'broker':
        async def fail():
            raise RuntimeError('broker')
        monkeypatch.setattr(main, 'close_cancellation_broker', fail)
    async def scenario():
        for _ in range(2):
            try:
                async with main.lifespan(application):
                    manager = service.get_git_samples(request(application))
                    managers.append(manager)
                    assert manager is service.get_git_samples(request(application))
                    assert manager._bindings == {}
                    manager.bind(**SCOPE)
                    root = manager._bindings[tuple(SCOPE.values())].sample.root
                    assert root.is_dir()
                    if failure == 'body':
                        raise RuntimeError('body')
            except RuntimeError as error:
                assert str(error) == failure
            else:
                assert failure == 'none'
            assert not root.exists()
            assert not hasattr(application.state, 'git_samples')
            assert not hasattr(application.state, 'execution_budget')
            with pytest.raises(registry.TaskGitSampleError):
                service.get_git_samples(request(application))
            with pytest.raises(registry.TaskGitSampleError):
                manager.bind(**SCOPE)
            await service.stop_git_samples(application)
    asyncio.run(scenario())
    assert managers[0] is not managers[1]


def test_startup_failure_no_manager(monkeypatch):
    def fail():
        raise RuntimeError('readiness')
    monkeypatch.setattr(main, 'check_database_ready', fail)
    application = FastAPI()
    async def scenario():
        with pytest.raises(RuntimeError, match='readiness'):
            async with main.lifespan(application):
                pytest.fail('must not start')
    asyncio.run(scenario())
    assert not hasattr(application.state, 'git_samples')


def test_distinct_apps_and_duplicate_start():
    first, second = FastAPI(), FastAPI()
    async def scenario():
        async with main.lifespan(first), main.lifespan(second):
            assert service.get_git_samples(request(first)) is not service.get_git_samples(request(second))
            with pytest.raises(registry.TaskGitSampleError):
                service.start_git_samples(first)
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['busy', 'cleanup'])
def test_failed_shutdown_retains_sealed_owner_and_other_resources_close(failure):
    application = FastAPI()
    async def scenario():
        with pytest.raises((registry.TaskGitSampleError, OSError)):
            async with main.lifespan(application):
                manager = service.get_git_samples(request(application))
                manager.bind(**SCOPE)
                binding = manager._bindings[tuple(SCOPE.values())]
                root = binding.sample.root
                lifetime = binding.lifetime
                if failure == 'busy':
                    binding.busy = True
                else:
                    class FailedCleanup:
                        def __exit__(self, *args):
                            raise OSError('cleanup unconfirmed')
                    binding.lifetime = FailedCleanup()
        try:
            assert root.exists()
            assert application.state.git_samples.manager is manager
            assert not hasattr(application.state, 'task_sample_recovery_store')
            assert not hasattr(application.state, 'command_recovery_store')
            assert not hasattr(application.state, 'execution_budget')
            for action in [lambda: service.get_git_samples(request(application)),
                           lambda: service.start_git_samples(application),
                           lambda: manager.bind(**SCOPE),
                           lambda: manager.read_status(**SCOPE)]:
                with pytest.raises(registry.TaskGitSampleError):
                    action()
            with pytest.raises(registry.TaskGitSampleError):
                await service.stop_git_samples(application)
        finally:
            # 仅测试夹具解除注入故障；产品不自动重试未确认清理。
            binding.busy = binding.sealed = False
            binding.lifetime = lifetime
            manager.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('fail', [False, True])
def test_repeated_cancellation_waits_for_cleanup_thread(monkeypatch, fail):
    application = FastAPI()
    entered, release = Event(), Event()
    service.start_git_samples(application)
    manager = service.get_git_samples(request(application))
    calls = []
    def shutdown():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        if fail:
            raise OSError('unconfirmed')
    monkeypatch.setattr(manager, 'shutdown', shutdown)
    async def scenario():
        task = asyncio.create_task(service.stop_git_samples(application))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            assert application.state.git_samples.manager is manager
            with pytest.raises(registry.TaskGitSampleError):
                await service.stop_git_samples(application)
        finally:
            release.set()
        with pytest.raises(OSError if fail else asyncio.CancelledError):
            await task
        assert hasattr(application.state, 'git_samples') is fail
        assert calls == [1]
    asyncio.run(scenario())


def test_stop_during_real_borrow_preserves_directory_until_reader_finishes(monkeypatch):
    application = FastAPI()
    entered, release = Event(), Event()
    service.start_git_samples(application)
    manager = service.get_git_samples(request(application))
    manager.bind(**SCOPE)
    root = manager._bindings[tuple(SCOPE.values())].sample.root
    original = registry.collect_sample_git_status
    def collect(sample):
        entered.set()
        assert release.wait(5)
        return original(sample)
    monkeypatch.setattr(registry, 'collect_sample_git_status', collect)
    async def scenario():
        reader = asyncio.create_task(asyncio.to_thread(manager.read_status, **SCOPE))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(registry.TaskGitSampleError):
                await service.stop_git_samples(application)
            assert root.is_dir()
            with pytest.raises(registry.TaskGitSampleError):
                manager.read_status(**SCOPE)
        finally:
            release.set()
            result = await reader
        assert result.entries == ()
        assert application.state.git_samples.manager is manager
        # 借用结束不自动重试清理；这里只由测试宿主显式释放自有资源。
        assert root.is_dir()
        manager.shutdown()
        assert not root.exists()
    asyncio.run(scenario())
