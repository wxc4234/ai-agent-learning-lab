"""许可宿主应用隔离与启动/关闭边界，不连接数据库或执行文件写入。"""

import asyncio

import pytest
from fastapi import FastAPI

from app import main
from app.config import settings
from app.services.workspace.proposals.project_write_grants import ProjectWriteGrantService


@pytest.mark.parametrize('failure', ['none', 'body', 'broker', 'git'])
def test_distinct_apps_restarts_and_cleanup(monkeypatch, failure):
    monkeypatch.setattr(settings, 'app_mode', 'local')
    monkeypatch.setattr(main, 'check_database_ready', lambda: None)
    async def broker():
        if failure == 'broker':
            raise RuntimeError('broker')
    async def git(app):
        if failure == 'git':
            raise RuntimeError('git')
    monkeypatch.setattr(main, 'start_git_samples', lambda app: None)
    monkeypatch.setattr(main, 'stop_git_samples', git)
    monkeypatch.setattr(main, 'close_cancellation_broker', broker)
    first, second = FastAPI(), FastAPI()
    hosts = []
    async def cycle(app):
        try:
            async with main.lifespan(app):
                host = app.state.project_write_grants
                assert isinstance(host, ProjectWriteGrantService)
                assert app.state.project_write_grants is host
                hosts.append(host)
                if failure == 'body':
                    raise RuntimeError('body')
        except RuntimeError:
            assert failure != 'none'
        assert not hasattr(app.state, 'project_write_grants')
    async def run():
        await cycle(first)
        await cycle(second)
        await cycle(first)
    asyncio.run(run())
    assert len({id(host) for host in hosts}) == 3
    assert len({host._reader._runtime_id for host in hosts}) == 3


@pytest.mark.parametrize('mode', ['account', 'local'])
def test_no_host_before_readiness_and_no_account_host(monkeypatch, mode):
    monkeypatch.setattr(settings, 'app_mode', mode)
    def fail():
        raise RuntimeError('not ready')
    monkeypatch.setattr(main, 'check_database_ready', fail)
    app = FastAPI()
    async def run():
        with pytest.raises(RuntimeError):
            async with main.lifespan(app):
                pytest.fail('not ready')
        assert not hasattr(app.state, 'project_write_grants')
        monkeypatch.setattr(main, 'check_database_ready', lambda: None)
        async with main.lifespan(app):
            assert hasattr(app.state, 'project_write_grants') == (mode == 'local')
        assert not hasattr(app.state, 'project_write_grants')
    asyncio.run(run())
