"""可选应用配置与独立Embedding连接契约，不读取真实密钥或访问网络。"""

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.services.model.embedding_config import (
    EmbeddingConfig,
    EmbeddingError,
    load_embedding_config,
)


def loaded(**values):
    arguments = {
        "_env_file": None,
        "APP_MODE": "account",
        "DEEPSEEK_API_KEY": "CHAT_PRIVATE",
        **values,
    }
    return Settings(**arguments)  # pyright: ignore[reportCallIssue] -- BaseSettings别名和可选.env由运行时解析


def config(**values):
    return EmbeddingConfig.model_validate(
        {
            "api_key": SecretStr("EMBED_PRIVATE"),
            "base_url": "https://provider.invalid/v1",
            "model": "fixture-embedding",
            "dimensions": 3,
            **values,
        }
    )


@pytest.fixture(autouse=True)
def clean_embedding_environment(monkeypatch):
    for key in (
        "API_KEY",
        "BASE_URL",
        "MODEL",
        "DIMENSIONS",
        "REQUEST_DIMENSIONS",
        "TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv("EMBEDDING_" + key, raising=False)


def test_unconfigured_embedding_does_not_require_chat_or_application_changes():
    source = loaded()
    assert source.embedding_dimensions is None
    assert source.embedding_base_url == source.embedding_model == ""
    assert source.deepseek_api_key.get_secret_value() == "CHAT_PRIVATE"
    with pytest.raises(EmbeddingError) as caught:
        load_embedding_config(source)
    assert caught.value.code == "embedding_not_configured" and "PRIVATE" not in str(
        caught.value
    )


def test_load_independent_config_from_environment(monkeypatch):
    for key, value in {
        "API_KEY": "EMBED_PRIVATE",
        "BASE_URL": "https://provider.invalid/v1/",
        "MODEL": "fixture-embedding",
        "DIMENSIONS": "1536",
        "REQUEST_DIMENSIONS": "true",
        "TIMEOUT_SECONDS": "12.5",
    }.items():
        monkeypatch.setenv("EMBEDDING_" + key, value)
    result = load_embedding_config(loaded())
    assert result.base_url == "https://provider.invalid/v1"
    assert result.model == "fixture-embedding" and result.dimensions == 1536
    assert result.request_dimensions and result.timeout_seconds == 12.5
    assert result.api_key.get_secret_value() == "EMBED_PRIVATE"
    assert "EMBED_PRIVATE" not in repr(result) and "CHAT_PRIVATE" not in repr(result)


@pytest.mark.parametrize("value", [1, 4096, "1", "4096"])
def test_dimension_boundaries(value):
    assert loaded(EMBEDDING_DIMENSIONS=value).embedding_dimensions == int(value)


@pytest.mark.parametrize(
    "value", [0, -1, 4097, True, 1.5, "0", "-1", "4097", "1.0", " 3", "3 ", "03", "NaN"]
)
def test_invalid_dimensions_not_coerced(value):
    with pytest.raises(ValidationError):
        loaded(EMBEDDING_DIMENSIONS=value)


def test_blank_dimension_means_not_configured():
    assert loaded(EMBEDDING_DIMENSIONS="").embedding_dimensions is None


@pytest.mark.parametrize("value", [True, False, "true", "false"])
def test_dimension_request_switch(value):
    assert loaded(EMBEDDING_REQUEST_DIMENSIONS=value).embedding_request_dimensions is (
        value is True or value == "true"
    )


@pytest.mark.parametrize("value", [1, 0, "1", "yes", "TRUE"])
def test_invalid_dimension_request_switch(value):
    with pytest.raises(ValidationError):
        loaded(EMBEDDING_REQUEST_DIMENSIONS=value)


@pytest.mark.parametrize(
    "value", [0, 61, True, float("nan"), float("inf"), "0", "61", "NaN", "1e1", "30 "]
)
def test_invalid_wall_clock_timeout(value):
    with pytest.raises(ValidationError):
        loaded(EMBEDDING_TIMEOUT_SECONDS=value)


@pytest.mark.parametrize(
    "url",
    [
        "https://provider.invalid/v1/",
        "http://localhost:11434/v1",
        "http://127.0.0.1:8001/v1",
        "http://[::1]:8001/v1",
    ],
)
def test_remote_https_and_loopback_http(url):
    assert config(base_url=url).base_url == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://example.com",
        "http://example.com/v1",
        "https://user:PRIVATE@example.com",
        "https://example.com/?token=PRIVATE",
        "https://example.com/#PRIVATE",
        "https://example.com/?",
        "https://example.com/#",
        "https://example.com:0",
        "https://example.com:70000",
        " https://example.com",
        "https://example.com/a\nb",
        "https://example.com\\evil",
        "https://example.com/中文",
    ],
)
def test_invalid_connection_address(url):
    with pytest.raises(ValidationError):
        config(base_url=url)


@pytest.mark.parametrize(
    "key", ["", " key", "key ", "key\nPRIVATE", "中文", "x" * 4097]
)
def test_key_cannot_inject_auth_header(key):
    with pytest.raises(ValidationError) as caught:
        config(api_key=SecretStr(key))
    assert key not in str(caught.value) if key else True


@pytest.mark.parametrize(
    "model", ["", " model", "model ", "model\nPRIVATE", "\ud800", "x" * 257]
)
def test_invalid_model_name(model):
    with pytest.raises(ValidationError):
        config(model=model)


def test_partial_config_does_not_fall_back_to_chat_credentials():
    source = loaded(
        EMBEDDING_BASE_URL="https://provider.invalid/v1",
        EMBEDDING_MODEL="fixture",
        EMBEDDING_DIMENSIONS=3,
    )
    with pytest.raises(EmbeddingError) as caught:
        load_embedding_config(source)
    assert caught.value.code == "embedding_not_configured"


def test_invalid_full_config_reports_static_error():
    source = loaded(
        EMBEDDING_API_KEY="EMBED_PRIVATE",
        EMBEDDING_BASE_URL="https://user:PRIVATE@example.com",
        EMBEDDING_MODEL="fixture",
        EMBEDDING_DIMENSIONS=3,
    )
    with pytest.raises(EmbeddingError) as caught:
        load_embedding_config(source)
    assert caught.value.code == "embedding_config_invalid" and "PRIVATE" not in str(
        caught.value
    )


def test_config_is_immutable_and_not_relaxed_by_unknown_fields():
    result = config()
    with pytest.raises(ValidationError):
        result.dimensions = 9
    with pytest.raises(ValidationError):
        config(extra="ignored")
