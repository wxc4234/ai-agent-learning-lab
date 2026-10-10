"""密钥不回显、配置原子保存和请求级模型快照。"""

import asyncio
import json
from types import SimpleNamespace
from typing import Literal

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.config import settings
from app.services.model import local_model_settings as service
from app.services.model.embedding_config import load_embedding_config, EmbeddingError
from app.services.model import model_client
from app.routers.model.settings import router


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_SETTINGS_PATH", str(tmp_path / "model-settings.json"))
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(settings, "local_runtime_token", SecretStr("a" * 64))
    monkeypatch.setattr(settings, "login_allowed_origins", ["http://localhost:3000"])


def update(kind: Literal["chat", "embedding"]="chat", **overrides):
    config = {"enabled": True, "base_url": "https://fixture.invalid/v1", "model": "fixture-model", "api_key": "test-secret",
                  "dimensions": 3 if kind == "embedding" else None, "request_dimensions": False}
    config.update(overrides)
    return service.Update(channel=kind, revision=service.public_settings()["revision"],
                          config=service.Channel.model_validate(config))


def test_atomic_save_redaction_permissions_and_keep_key():
    first = service.save_settings(update())
    assert "test-secret" not in json.dumps(first)
    assert "api_key" not in first["chat"]
    assert first["chat"]["key_configured"]
    assert service.storage_path().stat().st_mode & 0o777 == 0o600
    service.save_settings(update(api_key="", model="new-model"))
    stored = service.override("chat")
    assert stored is not None and stored.api_key.get_secret_value() == "test-secret"
    # 另一次读取仍能恢复持久化配置。
    assert service.public_settings()["chat"]["model"] == "new-model"


def test_conflict_and_change_host_cannot_reuse_key():
    old = update()
    service.save_settings(old)
    with pytest.raises(ValueError, match="conflict"):
        service.save_settings(old)
    with pytest.raises(ValueError, match="new_endpoint"):
        service.save_settings(update(api_key="", base_url="https://other.invalid/v1"))
    stored = service.override("chat")
    assert stored is not None and stored.base_url == "https://fixture.invalid/v1"


def test_clear_key_disable_and_embedding_loader():
    service.save_settings(update("embedding"))
    assert load_embedding_config().dimensions == 3
    value = update("embedding", enabled=False, api_key="")
    service.save_settings(value.model_copy(update={"clear_key": True}))
    assert not service.public_settings()["embedding"]["key_configured"]
    with pytest.raises(EmbeddingError):
        load_embedding_config()


@pytest.mark.parametrize("change", [
    {"base_url": "http://remote.invalid"}, {"base_url": "https://user:pass@fixture.invalid"},
    {"dimensions": None}, {"base_url": "https://api.deepseek.com"},
])
def test_bad_embedding_rejected(change):
    with pytest.raises(ValueError):
        service.save_settings(update("embedding", **change))
    assert not service.storage_path().exists()


def test_failed_replace_preserves_old_file(monkeypatch):
    service.save_settings(update())
    before = service.storage_path().read_bytes()
    def fail(*args):
        raise OSError()
    monkeypatch.setattr(service.os, "replace", fail)
    with pytest.raises(OSError):
        service.save_settings(update(model="replacement"))
    assert service.storage_path().read_bytes() == before
    assert list(service.storage_path().parent.iterdir()) == [service.storage_path()]


@pytest.mark.parametrize("mode", ["valid", "bad_count", "bad_dimension", "http_error"])
def test_detect_only_fixed_text_and_does_not_save(monkeypatch, mode):
    original = httpx.AsyncClient
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["input"] == ["Dimension detection test."]
        assert "dimensions" not in payload
        body = {"object": "list", "model": "fixture-model", "data": [
            {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}]}
        if mode == "bad_count":
            body["data"] *= 2
        if mode == "bad_dimension":
            body["data"][0]["embedding"] = []
        return httpx.Response(503 if mode == "http_error" else 200, json=body)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)))
    config = update("embedding", dimensions=None).config
    if mode == "valid":
        assert asyncio.run(service.detect_dimensions(config)) == 3
    else:
        with pytest.raises(ValueError):
            asyncio.run(service.detect_dimensions(config))
    assert len(calls) == 1 and not service.storage_path().exists()


@pytest.mark.parametrize("method,token,origin,status", [
    ("GET", "a" * 64, None, 200), ("GET", "", None, 403),
    ("PUT", "a" * 64, "https://evil.invalid", 403),
    ("PUT", "a" * 64, "http://localhost:3000", 200),
])
def test_api_boundary_and_no_echo(method, token, origin, status):
    app = FastAPI()
    app.include_router(router)
    headers = {"x-local-runtime-token": token}
    if origin:
        headers["origin"] = origin
    body = update().model_dump(mode="json")
    body["config"]["api_key"] = "private-api-value"
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            return await client.request(method, "/model-settings", headers=headers, json=body)
    response = asyncio.run(run())
    assert response.status_code == status
    assert "private-api-value" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_api_validation_never_echoes_bad_key():
    app = FastAPI()
    app.include_router(router)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            return await client.put("/model-settings", headers={"x-local-runtime-token": "a" * 64, "origin": "http://localhost:3000"},
                                    json={"api_key": "NEVER-ECHO"})
    response = asyncio.run(run())
    assert response.status_code == 422 and "NEVER-ECHO" not in response.text


def test_model_session_fixed_snapshot_and_close(monkeypatch):
    service.save_settings(update())
    clients = []
    class Fake:
        def __init__(self, **kwargs):
            self.key = kwargs["api_key"]
            self.closed = False
            self.http = kwargs["http_client"]
            clients.append(self)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            self.closed = True
            await self.http.aclose()
    monkeypatch.setattr(model_client, "AsyncOpenAI", Fake)
    async def run():
        async with model_client.chat_model_session(object(), "old") as (client, model, custom):
            assert model == "fixture-model" and custom and isinstance(client, Fake) and client.key == "test-secret"
            service.save_settings(update(model="second"))
            assert model == "fixture-model"
        async with model_client.chat_model_session(object(), "old") as (_, model, _):
            assert model == "second"
    asyncio.run(run())
    assert all(c.closed and c.http.is_closed for c in clients)


def test_absent_override_keeps_existing_client():
    client = SimpleNamespace()
    async def run():
        async with model_client.chat_model_session(client, "env-model") as snapshot:
            assert snapshot == (client, "env-model", False)
    asyncio.run(run())


def test_custom_provider_does_not_use_deepseek_prices():
    from app.services.chat.chat_service import build_run_finished_payload
    from app.services.runtime.agent.agent_runtime import AgentLoopResult
    result = AgentLoopResult(status="completed", answer="answer", steps_taken=1, observations=())
    metrics = build_run_finished_payload(result, custom_model="other")["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["pricing"] is None and metrics["estimated_cost_cny"] is None


def test_account_mode_cannot_read_local_secrets(monkeypatch):
    service.save_settings(update())
    monkeypatch.setattr(settings, "app_mode", "account")
    assert service.override("chat") is None


def test_dimension_probe_cannot_reuse_key_for_changed_destination():
    service.save_settings(update("embedding"))
    config = update("embedding", api_key="", base_url="https://other.invalid").config
    with pytest.raises(ValueError):
        asyncio.run(service.detect_dimensions(config))


def test_title_uses_saved_model_and_closes_client(monkeypatch):
    from app.services.tasks.task_workspace import generate_title
    service.save_settings(update(model="title-model"))
    calls = []
    class Fake:
        def __init__(self, **kwargs):
            self.http = kwargs["http_client"]
            self.chat = SimpleNamespace(completions=self)
        def with_options(self, **kwargs):
            return self
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="标题"))])
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            await self.http.aclose()
    monkeypatch.setattr(model_client, "AsyncOpenAI", Fake)
    assert asyncio.run(generate_title([("user", "问题")])) == "标题"
    assert calls[0]["model"] == "title-model"


def test_disabled_chat_rolls_back_pending_history(monkeypatch):
    from app.services.chat import chat_service
    from app.services.runtime.execution.execution_threads import ExecutionThreads
    service.save_settings(update(enabled=False))
    history = [{"role": "user", "content": "pending"}]
    async def prepare(**kwargs):
        return history, list(history)
    monkeypatch.setattr(chat_service, "_prepare_chat_messages", prepare)
    with pytest.raises(ValueError, match="chat_model_disabled"):
        asyncio.run(chat_service.create_chat_reply(user_id=1, session_id="test", prompt="pending", execution_threads=ExecutionThreads()))
    assert history == []


def test_disabled_endpoint_change_discards_previous_key():
    service.save_settings(update())
    service.save_settings(update(enabled=False, api_key="", base_url="https://other.invalid/v1"))
    stored = service.override("chat")
    assert stored is not None and not stored.api_key.get_secret_value()
    with pytest.raises(ValueError):
        service.save_settings(update(api_key="", base_url="https://other.invalid/v1"))
