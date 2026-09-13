import json

import pytest
from pydantic import SecretStr, ValidationError

from app.schemas import RegisterRequest


def test_valid_request_normalizes_username_and_preserves_password():
    request = RegisterRequest(username="  Agent_User  ", password="Agent-2026!")
    assert request.username == "agent_user"
    assert isinstance(request.password, SecretStr)
    assert request.password.get_secret_value() == "Agent-2026!"


@pytest.mark.parametrize("length", [3, 64])
def test_username_length_boundaries(length):
    request = RegisterRequest(username="A" * length, password="Agent-2026!")
    assert request.username == "a" * length


@pytest.mark.parametrize(
    "username", ["", "  ", "ab", "a" * 65, "a b", "a-b", "用户🙂", "Ａgent", "ab\ncd"]
)
def test_invalid_username_rejected(username):
    with pytest.raises(ValidationError):
        RegisterRequest(username=username, password="Agent-2026!")


@pytest.mark.parametrize("length", [8, 128])
def test_password_length_boundaries(length):
    request = RegisterRequest(username="agent", password="A" * length)
    assert len(request.password.get_secret_value()) == length


@pytest.mark.parametrize("length", [0, 7, 129])
def test_invalid_password_length_rejected(length):
    with pytest.raises(ValidationError):
        RegisterRequest(username="agent", password="A" * length)


@pytest.mark.parametrize(
    "whitespace", [" ", "\t", "\n", "\r", "\v", "\f", "\u3000", "\u00a0"]
)
@pytest.mark.parametrize("position", ["start", "middle", "end"])
def test_password_whitespace_rejected(whitespace, position):
    passwords = {
        "start": whitespace + "Agent-2026!",
        "middle": "Agent" + whitespace + "-2026!",
        "end": "Agent-2026!" + whitespace,
    }
    with pytest.raises(ValidationError) as error:
        RegisterRequest(username="agent", password=passwords[position])
    assert error.value.errors(include_input=False)[0]["loc"] == ("password",)
    assert "密码不能包含空格或其他空白字符" in str(error.value)


@pytest.mark.parametrize("field", ["username", "password"])
@pytest.mark.parametrize(
    "value", [None, 12345678, True, b"Agent-2026!", ["Agent-2026!"]]
)
def test_strict_types(field, value):
    payload = {"username": "agent", "password": "Agent-2026!", field: value}
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(payload)


@pytest.mark.parametrize("field", ["username", "password"])
def test_required_fields(field):
    payload = {"username": "agent", "password": "Agent-2026!"}
    del payload[field]
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(payload)


@pytest.mark.parametrize("field", ["password_hash", "is_admin", "external_id"])
def test_extra_fields_rejected(field):
    with pytest.raises(ValidationError) as error:
        RegisterRequest.model_validate(
            {"username": "agent", "password": "Agent-2026!", field: "unexpected"}
        )
    assert error.value.errors(include_input=False)[0]["type"] == "extra_forbidden"


def test_secret_masking_and_error_string_hiding():
    password = "Sensitive-Password-2026!"
    request = RegisterRequest(username="agent", password=password)
    assert password not in repr(request)
    assert str(request.password) == "**********"
    assert json.loads(request.model_dump_json())["password"] == "**********"
    with pytest.raises(ValidationError) as error:
        RegisterRequest(username="agent", password=password + " ")
    assert password not in str(error.value)
    # errors() can retain raw inputs; hiding exception text is not global redaction.
    safe_errors = error.value.errors(include_input=False, include_context=False)
    assert password not in json.dumps(safe_errors)


def test_json_input_and_unicode_password():
    request = RegisterRequest.model_validate_json(
        json.dumps(
            {
                "username": "Agent",
                "password": "学习Agent-2026!",
            }
        )
    )
    assert request.username == "agent"
    assert request.password.get_secret_value() == "学习Agent-2026!"
