"""延迟加载独立Embedding连接配置；不在导入或启动时解封密钥并创建客户端。"""

import ipaddress
import math
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from app.config import Settings, settings


class EmbeddingError(ValueError):
    """固定公开错误，不包含供应商正文、输入源码或配置凭证。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )

    api_key: SecretStr
    base_url: str
    model: str
    dimensions: int = Field(ge=1, le=4096)
    request_dimensions: bool = False
    timeout_seconds: float = Field(default=30.0, ge=1, le=60)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value()
        if (
            not key
            or len(key) > 4096
            or any(ord(char) < 33 or ord(char) > 126 for char in key)
        ):
            raise ValueError("Embedding密钥不能为空且必须适用于认证头")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if (
            not value
            or len(value) > 256
            or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("Embedding模型名称无效")
        try:
            if len(value.encode("utf-8")) > 1024:
                raise ValueError("Embedding模型名称过长")
        except UnicodeError:
            raise ValueError("Embedding模型名称无效") from None
        return value

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        try:
            if (
                not value
                or len(value) > 2048
                or any(ord(char) <= 32 or ord(char) >= 127 for char in value)
                or "\\" in value
            ):
                raise ValueError()
            parsed = urlsplit(value)
            if (
                not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or "?" in value
                or "#" in value
            ):
                raise ValueError()
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError()
            loopback = parsed.hostname == "localhost"
            try:
                loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                pass
            if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
                raise ValueError()
        except ValueError:
            raise ValueError(
                "Embedding地址必须为HTTPS或回环HTTP且不含凭证、查询或片段"
            ) from None
        return value.rstrip("/")

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Embedding超时必须是有限秒数")
        return value


def load_embedding_config(source: Settings = settings) -> EmbeddingConfig:
    """调用时才核对完整配置；关闭状态不阻止原有聊天功能启动。"""

    # 延迟导入避免配置验证器与本地存储互相导入；显式测试/宿主配置不受覆盖。
    if source is settings and settings.app_mode == "local":
        from app.services.model.local_model_settings import override, validate_channel
        try:
            selected = override("embedding")
            if selected is not None:
                if not selected.enabled:
                    raise ValueError("disabled")
                validate_channel(selected, "embedding")
                assert selected.dimensions is not None
                return EmbeddingConfig(api_key=selected.api_key, base_url=selected.base_url,
                                       model=selected.model, dimensions=selected.dimensions,
                                       request_dimensions=selected.request_dimensions,
                                       timeout_seconds=source.embedding_timeout_seconds)
        except (ValueError, OSError):
            raise EmbeddingError("embedding_config_invalid", "本地Embedding配置不可用") from None

    if (
        not source.embedding_base_url
        or not source.embedding_model
        or not source.embedding_api_key.get_secret_value()
        or source.embedding_dimensions is None
    ):
        raise EmbeddingError(
            "embedding_not_configured", "请先配置独立Embedding地址、模型、密钥与维度"
        )
    try:
        return EmbeddingConfig(
            api_key=source.embedding_api_key,
            base_url=source.embedding_base_url,
            model=source.embedding_model,
            dimensions=source.embedding_dimensions,
            request_dimensions=source.embedding_request_dimensions,
            timeout_seconds=source.embedding_timeout_seconds,
        )
    except ValidationError:
        raise EmbeddingError("embedding_config_invalid", "Embedding配置无效") from None
