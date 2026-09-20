"""创建响应及身份确认的纯解析测试，不调用 Docker。"""

from dataclasses import FrozenInstanceError
import json

import pytest

from app.services.runtime.command_contracts import CommandRequest
from app.services.runtime.sandbox_identity import (
    SandboxIdentityError,
    confirm_created_sandbox_identity,
    parse_created_container_id,
)
from app.services.runtime.sandbox_spec import APPROVED_SANDBOX_IMAGE, build_sandbox_create_spec

CONTAINER_ID = "a" * 64
SPEC = build_sandbox_create_spec(request=CommandRequest(argv=["/bin/echo"]), execution_token="b" * 32)


def payload():
    return [{
        "Id": CONTAINER_ID, "Name": f"/{SPEC.container_name}",
        "Config": {"Image": APPROVED_SANDBOX_IMAGE, "Labels": {
            "ai-agent-learning-lab.role": "sandbox",
            "ai-agent-learning-lab.execution": SPEC.execution_token,
        }},
        "State": {"Status": "created", "Running": False, "Pid": 0},
    }]


def confirm(text, **options):
    return confirm_created_sandbox_identity(**{
        "container_id": CONTAINER_ID, "inspect_stdout": text, "spec": SPEC,
    } | options)


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_full_id_and_single_line_ending(ending):
    assert parse_created_container_id(CONTAINER_ID + ending) == CONTAINER_ID


@pytest.mark.parametrize("value", [
    None, True, 1, b"a" * 64, "", "a" * 12, "a" * 63, "a" * 65,
    "A" * 64, "g" * 64, " " + CONTAINER_ID, CONTAINER_ID + " ",
    CONTAINER_ID + "\n\n", CONTAINER_ID + "\r", "log\n" + CONTAINER_ID,
    CONTAINER_ID + "\n" + CONTAINER_ID,
])
def test_invalid_create_stdout(value):
    with pytest.raises(SandboxIdentityError):
        parse_created_container_id(value)


@pytest.mark.parametrize("value", [None, True, "", "a" * 12, CONTAINER_ID + "\n"])
def test_confirm_requires_normalized_full_id(value):
    with pytest.raises(SandboxIdentityError):
        confirm(json.dumps(payload()), container_id=value)


@pytest.mark.parametrize("value", [None, 1, b"[]", "", "{PRIVATE", "[] trailing", "[" * 2000 + "]" * 2000])
def test_invalid_inspect_text_is_safely_rejected(value):
    with pytest.raises(SandboxIdentityError) as caught:
        confirm(value)
    assert str(caught.value) == "无法确认 Sandbox 容器身份或创建状态"
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("value", [None, {}, [], [None], [True], ["PRIVATE"], [{}, {}]])
def test_inspect_must_contain_exactly_one_object(value):
    with pytest.raises(SandboxIdentityError):
        confirm(json.dumps(value))


@pytest.mark.parametrize("path", [
    ("Id",), ("Name",), ("Config", "Image"),
    ("Config", "Labels", "ai-agent-learning-lab.role"),
    ("Config", "Labels", "ai-agent-learning-lab.execution"),
])
@pytest.mark.parametrize("value", [None, "PRIVATE_WRONG", 0])
def test_identity_fields_must_match(path, value):
    data = payload()
    target = data[0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(SandboxIdentityError) as caught:
        confirm(json.dumps(data))
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("path", [
    ("Config",), ("State",), ("Config", "Labels"), ("Id",), ("Name",),
    ("Config", "Image"), ("Config", "Labels", "ai-agent-learning-lab.role"),
    ("Config", "Labels", "ai-agent-learning-lab.execution"),
    ("State", "Status"), ("State", "Running"), ("State", "Pid"),
])
def test_required_fields_cannot_be_missing(path):
    data = payload()
    target = data[0]
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    with pytest.raises(SandboxIdentityError):
        confirm(json.dumps(data))


@pytest.mark.parametrize("path", [("Config",), ("State",), ("Config", "Labels")])
@pytest.mark.parametrize("value", [None, [], "PRIVATE"])
def test_nested_objects_have_strict_shapes(path, value):
    data = payload()
    target = data[0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(SandboxIdentityError):
        confirm(json.dumps(data))


@pytest.mark.parametrize("field,value", [
    ("Status", "running"), ("Status", "exited"), ("Status", "dead"),
    ("Running", True), ("Running", 0), ("Running", "false"),
    ("Pid", False), ("Pid", True), ("Pid", 0.0), ("Pid", "0"), ("Pid", -1), ("Pid", 42),
])
def test_created_state_requires_consistent_strict_values(field, value):
    data = payload()
    data[0]["State"][field] = value
    with pytest.raises(SandboxIdentityError):
        confirm(json.dumps(data))


@pytest.mark.parametrize("fragment", [
    '"Id":"duplicate",', '"extra":{"key":1,"key":2},',
    '"extra":NaN,', '"extra":Infinity,', '"extra":-Infinity,',
])
def test_duplicate_keys_and_nonstandard_numbers_rejected(fragment):
    text = json.dumps(payload())
    text = text.replace("[{", "[{" + fragment, 1)
    with pytest.raises(SandboxIdentityError):
        confirm(text)


def test_character_limit_extra_fields_and_immutable_snapshot():
    data = payload()
    data[0]["FutureField"] = {"anything": [1, 2]}
    data[0]["Config"]["Labels"]["extra"] = "allowed"
    text = json.dumps(data)
    bounded = text + " " * (65_536 - len(text))
    identity = confirm(bounded)
    assert identity.container_id == CONTAINER_ID
    assert identity.container_name == SPEC.container_name
    assert identity.execution_token == SPEC.execution_token
    assert identity.image == APPROVED_SANDBOX_IMAGE
    with pytest.raises(FrozenInstanceError):
        identity.container_id = "c" * 64
    with pytest.raises(SandboxIdentityError):
        confirm(bounded + " ")


def test_other_execution_spec_is_rejected():
    other = build_sandbox_create_spec(request=CommandRequest(argv=["/bin/echo"]), execution_token="c" * 32)
    with pytest.raises(SandboxIdentityError):
        confirm(json.dumps(payload()), spec=other)


@pytest.mark.parametrize("spec", [None, {}, "PRIVATE"])
def test_spec_requires_server_object(spec):
    with pytest.raises(TypeError):
        confirm(json.dumps(payload()), spec=spec)
