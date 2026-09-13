"""Shared Chinese username policy and login-specific password semantics."""

import pytest
from pydantic import ValidationError

from app.schemas import LoginRequest, RegisterRequest


@pytest.mark.parametrize("model", [RegisterRequest, LoginRequest])
@pytest.mark.parametrize("raw, expected", [
    (" 中文用户名 ", "中文用户名"),
    ("繁體使用者", "繁體使用者"),
    (" 中文Agent_2026 ", "中文agent_2026"),
    ("𠮷小明", "𠮷小明"),
    ("〇一二", "〇一二"),
    ("汉" * 3, "汉" * 3),
    ("汉" * 64, "汉" * 64),
])
def test_chinese_usernames_share_normalization(
    model: type[RegisterRequest] | type[LoginRequest], raw: str, expected: str,
) -> None:
    request = model.model_validate({"username": raw, "password": "Password-2026!"})
    assert request.username == expected


@pytest.mark.parametrize("model", [RegisterRequest, LoginRequest])
@pytest.mark.parametrize("username", [
    "中文", "汉" * 65, "用户 名", "用户\u200b名", "用户\n名", "用户🙂",
    "用户。", "Ａgent", "用户é", "ユーザー", "user-name",
])
def test_username_rejections_remain_explicit(
    model: type[RegisterRequest] | type[LoginRequest], username: str,
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"username": username, "password": "Password-2026!"})


@pytest.mark.parametrize("password", ["x", " ", "  原始Password  ", "密" * 128])
def test_login_preserves_password_without_registration_rules(password: str) -> None:
    request = LoginRequest.model_validate({"username": "中文用户", "password": password})
    assert request.password.get_secret_value() == password


@pytest.mark.parametrize("password", ["", "x" * 129, 12345678, None, b"Password!"])
def test_login_rejects_invalid_password_input(password: object) -> None:
    with pytest.raises(ValidationError):
        LoginRequest.model_validate({"username": "中文用户", "password": password})


@pytest.mark.parametrize("payload", [
    {}, {"username": "中文用户"}, {"password": "Password!"},
    {"username": 123, "password": "Password!"},
    {"username": "中文用户", "password": "Password!", "is_admin": True},
])
def test_login_required_types_and_extra_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LoginRequest.model_validate(payload)


def test_login_secret_is_masked() -> None:
    secret = "Sensitive-Login-2026!"
    request = LoginRequest.model_validate({"username": "中文用户", "password": secret})
    assert secret not in repr(request)
    assert secret not in request.model_dump_json()
