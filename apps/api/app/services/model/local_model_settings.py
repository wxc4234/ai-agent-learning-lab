"""本机模型配置覆盖；原子替换0600文件，不修改.env、不返回密钥。"""

import json
import os
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.config import PROJECT_ROOT, settings
from app.services.model.embedding_config import EmbeddingConfig

LOCK = RLock()


def storage_path() -> Path:
    # 宿主/隔离测试可指定独立路径，浏览器不能选择写入位置。
    return Path(os.environ.get("MODEL_SETTINGS_PATH", str(PROJECT_ROOT / ".local/model-settings.json")))


class Channel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    enabled: bool = True
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=256)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    dimensions: int | None = Field(default=None, ge=1, le=4096)
    request_dimensions: bool = False


class Update(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    channel: Literal["chat", "embedding"]
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    config: Channel
    clear_key: bool = False


def _read() -> dict:
    path = storage_path()
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 32768:
        raise ValueError("model_settings_unavailable")
    value = json.loads(path.read_text())
    if type(value) is not dict or set(value) - {"chat", "embedding"}:
        raise ValueError("model_settings_unavailable")
    for item in value.values():
        Channel.model_validate(item)
    return value


def override(channel: str) -> Channel | None:
    if settings.app_mode != "local":
        return None
    with LOCK:
        raw = _read().get(channel)
        return None if raw is None else Channel.model_validate(raw)


def _default(channel: str) -> Channel:
    if channel == "chat":
        return Channel(base_url=settings.deepseek_base_url, model=settings.deepseek_model,
                       api_key=settings.deepseek_api_key)
    return Channel(base_url=settings.embedding_base_url, model=settings.embedding_model,
                   api_key=settings.embedding_api_key, dimensions=settings.embedding_dimensions,
                   request_dimensions=settings.embedding_request_dimensions,
                   enabled=bool(settings.embedding_base_url and settings.embedding_model))


def _revision(raw: dict) -> str:
    return sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def _public(raw: dict) -> dict:
    result: dict = {"revision": _revision(raw)}
    for name in ("chat", "embedding"):
        channel = Channel.model_validate(raw[name]) if name in raw else _default(name)
        result[name] = dict(channel.model_dump(exclude={"api_key"}),
                            key_configured=bool(channel.api_key.get_secret_value()),
                            source="local" if name in raw else "environment")
    return result


def public_settings() -> dict:
    with LOCK:
        return _public(_read())


def validate_channel(channel: Channel, kind: str) -> None:
    if not channel.enabled:
        return
    EmbeddingConfig(api_key=channel.api_key, base_url=channel.base_url, model=channel.model,
                    dimensions=channel.dimensions if kind == "embedding" and channel.dimensions else 1)
    if kind == "embedding":
        if channel.dimensions is None:
            raise ValueError("embedding_dimensions_required")
        if urlsplit(channel.base_url).hostname == "api.deepseek.com":
            raise ValueError("embedding_provider_unsupported")


def save_settings(update: Update) -> dict:
    with LOCK:
        raw = _read()
        if update.revision != _revision(raw):
            raise ValueError("model_settings_conflict")
        config = update.config
        prior = Channel.model_validate(raw[update.channel]) if update.channel in raw else _default(update.channel)
        secret = config.api_key.get_secret_value()
        if update.clear_key:
            secret = ""
        elif not secret:
            if config.base_url.rstrip("/") != prior.base_url.rstrip("/"):
                if config.enabled:
                    raise ValueError("new_endpoint_requires_key")
                # 停用时换地址也不能把旧密钥保留给未来的新目标。
                secret = ""
            else:
                secret = prior.api_key.get_secret_value()
        config = config.model_copy(update={"api_key": SecretStr(secret)})
        validate_channel(config, update.channel)
        raw[update.channel] = dict(config.model_dump(exclude={"api_key"}), api_key=secret)
        path = storage_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("model_settings_unavailable")
        temporary = None
        try:
            # 同目录原子替换，旧配置不会被半次写入破坏；锁覆盖读/保留密钥/替换。
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                json.dump(raw, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return _public(raw)


async def detect_dimensions(config: Channel) -> int:
    """只发送固定测试文本，不读取项目；检测不保存配置，也不触发索引。"""
    import asyncio
    import httpx
    from app.services.model.code_embeddings import _parse_response
    # 复用本机已保存密钥，但改变服务地址时必须显式输入新密钥。
    prior = override("embedding") or _default("embedding")
    if not config.api_key.get_secret_value() and config.base_url.rstrip("/") == prior.base_url.rstrip("/"):
        config = config.model_copy(update={"api_key": prior.api_key})
    config = config.model_copy(update={"enabled": True, "dimensions": 1})
    validate_channel(config, "embedding")
    checked = EmbeddingConfig(api_key=config.api_key, base_url=config.base_url,
                              model=config.model, dimensions=1)
    async with asyncio.timeout(15), httpx.AsyncClient(
        timeout=12, follow_redirects=False, trust_env=False,
    ) as client, client.stream(
            "POST", checked.base_url + "/embeddings",
            headers={"Authorization": "Bearer " + checked.api_key.get_secret_value(), "Accept-Encoding": "identity"},
            json={"model": checked.model, "input": ["Dimension detection test."], "encoding_format": "float"},
    ) as response:
        if not response.is_success or response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("embedding_probe_failed")
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > 256 * 1024:
                raise ValueError("embedding_probe_failed")
        # 先取实际长度，再交给严格协议解析器验证全部索引、数值和维度。
        try:
            dimension = len(json.loads(raw)["data"][0]["embedding"])
            if not 1 <= dimension <= 4096:
                raise ValueError()
            _parse_response(bytes(raw), 1, dimension)
            return dimension
        except (ValueError, KeyError, TypeError, IndexError):
            raise ValueError("embedding_probe_failed") from None
