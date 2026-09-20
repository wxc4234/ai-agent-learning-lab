"""预算配置及应用生命周期装配，不连接数据库。"""

import asyncio

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app import main
from app.config import Settings


@pytest.mark.parametrize("value", [True, False, 0, -1, 2.0, "0", "-1", "2.0", " 2", "02", "2\n", None])
def test_invalid_config(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_MODE="account", DEEPSEEK_API_KEY="test", AGENT_MAX_CONCURRENT_EXECUTIONS=value)


@pytest.mark.parametrize("value", [1, "2", "10"])
def test_valid_config(value):
    settings = Settings(_env_file=None, APP_MODE="account", DEEPSEEK_API_KEY="test", AGENT_MAX_CONCURRENT_EXECUTIONS=value)
    assert settings.agent_max_concurrent_executions == int(value)


def test_lifespan_new_budget_each_start_and_removes_on_failure(monkeypatch):
    application = FastAPI()
    instances = []
    monkeypatch.setattr(main, "check_database_ready", lambda: None)
    monkeypatch.setattr(main.settings, "agent_max_concurrent_executions", 3)

    async def close():
        pass
    monkeypatch.setattr(main, "close_cancellation_broker", close)

    async def scenario():
        for fail in [False, True]:
            try:
                async with main.lifespan(application):
                    budget = application.state.execution_budget
                    instances.append(budget)
                    assert budget.capacity == 3
                    with budget.reserve():
                        assert budget.in_use == 1
                    if fail:
                        raise ValueError("body")
            except ValueError:
                assert fail
            assert not hasattr(application.state, "execution_budget")
        assert instances[0] is not instances[1]
    asyncio.run(scenario())


def test_failed_database_check_does_not_install_budget(monkeypatch):
    def fail():
        raise RuntimeError("migration mismatch")
    monkeypatch.setattr(main, "check_database_ready", fail)
    application = FastAPI()

    async def scenario():
        with pytest.raises(RuntimeError, match="migration mismatch"):
            async with main.lifespan(application):
                pytest.fail("startup should fail")
        assert not hasattr(application.state, "execution_budget")
    asyncio.run(scenario())
