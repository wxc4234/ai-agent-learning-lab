"""执行配置纯校验及显式启用的未启动容器验收。"""

import asyncio
import json
import os
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker.docker_client import inspect_sandbox_container, is_sandbox_container_absent
from app.services.runtime.sandbox.sandbox_cleanup import cleanup_created_sandbox
from app.services.runtime.sandbox.sandbox_creation import create_and_confirm_sandbox
from app.services.runtime.sandbox.sandbox_execution_policy import (
    APPROVED_IMAGE_ENVIRONMENT,
    SandboxExecutionPolicyError,
    confirm_sandbox_execution_policy,
)
from app.services.runtime.sandbox.sandbox_spec import APPROVED_SANDBOX_IMAGE
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, payload, request


def configuration():
    data = payload()
    # 独立列出预期Cmd，避免用被测规格构造器复制相同错误。
    data[0]["Config"].update({
        "User": "10001:10001", "WorkingDir": "/tmp", "Entrypoint": ["/usr/bin/env"],
        "Cmd": ["-i", "--", "PATH=/usr/bin:/bin", "HOME=/home/agent", "TMPDIR=/tmp",
                "TMP=/tmp", "TEMP=/tmp", "LANG=C", "LC_ALL=C",
                "XDG_CONFIG_HOME=/home/agent/.config", "XDG_CACHE_HOME=/home/agent/.cache",
                "XDG_DATA_HOME=/home/agent/.local/share", "XDG_STATE_HOME=/home/agent/.local/state",
                "/usr/local/bin/python", "-c", "print('hello')"],
        "Env": list(APPROVED_IMAGE_ENVIRONMENT),
        "Tty": False, "OpenStdin": False, "StdinOnce": False, "AttachStdin": False,
    })
    return data


def confirm(data=None, **options):
    return confirm_sandbox_execution_policy(**{
        "request": request(), "execution_token": TOKEN, "container_id": CID,
        "inspect_stdout": json.dumps(configuration() if data is None else data),
    } | options)


def test_success_identity_and_no_input_mutation():
    data = configuration()
    before = json.dumps(data)
    identity = confirm(data)
    assert identity.container_id == CID and identity.execution_token == TOKEN
    assert identity.image == APPROVED_SANDBOX_IMAGE
    assert json.dumps(data) == before
    with pytest.raises(FrozenInstanceError):
        identity.container_id = "b" * 64


@pytest.mark.parametrize("field", ["User", "WorkingDir", "Entrypoint", "Cmd", "Env",
                                    "Tty", "OpenStdin", "StdinOnce", "AttachStdin"])
def test_missing_required_fields(field):
    data = configuration()
    del data[0]["Config"][field]
    with pytest.raises(SandboxExecutionPolicyError):
        confirm(data)


@pytest.mark.parametrize("field,value", [
    ("User", "0:0"), ("User", 10001), ("User", None),
    ("WorkingDir", "/"), ("WorkingDir", None),
    ("Entrypoint", "/usr/bin/env"), ("Entrypoint", ["/bin/sh"]),
    ("Entrypoint", ["/usr/bin/env", "extra"]), ("Cmd", None), ("Cmd", "PRIVATE"),
])
def test_changed_execution_configuration(field, value):
    data = configuration()
    data[0]["Config"][field] = value
    with pytest.raises(SandboxExecutionPolicyError, match="^无法确认 Sandbox 执行配置符合策略$"):
        confirm(data)


@pytest.mark.parametrize("field", ["Tty", "OpenStdin", "StdinOnce", "AttachStdin"])
@pytest.mark.parametrize("value", [True, 0, 1, None, "false"])
def test_interactive_flags_are_strict_bool(field, value):
    data = configuration()
    data[0]["Config"][field] = value
    with pytest.raises(SandboxExecutionPolicyError):
        confirm(data)


@pytest.mark.parametrize("change", ["remove_i", "remove_separator", "env", "swap", "extra", "program", "argument"])
def test_command_sequence_must_match(change):
    data = configuration()
    argv = data[0]["Config"]["Cmd"]
    if change == "remove_i":
        argv.pop(0)
    elif change == "remove_separator":
        argv.pop(1)
    elif change == "env":
        argv[2] = "PATH=/evil"
    elif change == "swap":
        argv[2], argv[3] = argv[3], argv[2]
    elif change == "extra":
        argv.append("extra")
    elif change == "program":
        argv[-3] = "/bin/sh"
    else:
        argv[-1] = "PRIVATE"
    with pytest.raises(SandboxExecutionPolicyError):
        confirm(data)


@pytest.mark.parametrize("environment", [None, "PATH=x", {}, [1], [],
    list(APPROVED_IMAGE_ENVIRONMENT) + ["LD_PRELOAD=/tmp/private.so"],
    list(APPROVED_IMAGE_ENVIRONMENT) + [APPROVED_IMAGE_ENVIRONMENT[0]],
    list(APPROVED_IMAGE_ENVIRONMENT)[1:],
    ["PATH=/evil", *APPROVED_IMAGE_ENVIRONMENT[1:]],
])
def test_environment_rejects_wrong_type_missing_extra_duplicate_or_changed(environment):
    data = configuration()
    data[0]["Config"]["Env"] = environment
    with pytest.raises(SandboxExecutionPolicyError):
        confirm(data)


def test_environment_order_and_unrelated_fields_allowed():
    data = configuration()
    data[0]["Config"]["Env"].reverse()
    data[0]["Config"]["Hostname"] = "unrelated"
    data[0]["Config"]["Healthcheck"] = None
    assert confirm(data).container_id == CID


@pytest.mark.parametrize("health", [{}, {"Test": ["CMD", "echo", "PRIVATE"]}, {"Test": ["NONE"]}, False])
def test_any_healthcheck_configuration_rejected(health):
    data = configuration()
    data[0]["Config"]["Healthcheck"] = health
    with pytest.raises(SandboxExecutionPolicyError):
        confirm(data)


@pytest.mark.parametrize("text", ["PRIVATE", "[]", "[" * 2000, "x" * 65537,
                                   '[{"Id":"a","Id":"b"}]', '[{"x":NaN}]'])
def test_strict_identity_errors_are_converted(text):
    with pytest.raises(SandboxExecutionPolicyError) as caught:
        confirm(inspect_stdout=text)
    assert str(caught.value) == "无法确认 Sandbox 执行配置符合策略"
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("field", ["id", "token", "state"])
def test_valid_configuration_does_not_bypass_identity(field):
    data = configuration()
    if field == "id":
        data[0]["Id"] = "c" * 64
    elif field == "token":
        data[0]["Config"]["Labels"]["ai-agent-learning-lab.execution"] = "c" * 32
    else:
        data[0]["State"]["Status"] = "running"
    with pytest.raises(SandboxExecutionPolicyError):
        confirm(data)


def test_original_request_must_match_and_is_revalidated():
    with pytest.raises(SandboxExecutionPolicyError):
        confirm(request=CommandRequest(argv=["/bin/echo", "other"]))
    command = request()
    command.argv.clear()
    with pytest.raises(ValueError):
        confirm(request=command)


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_EXECUTION_POLICY_DOCKER") != "1", reason="explicit Docker opt-in")
def test_real_unstarted_container_execution_configuration():
    async def scenario():
        command, token = request(), uuid4().hex
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        try:
            text = await inspect_sandbox_container(execution_token=token)
            assert confirm_sandbox_execution_policy(request=command, execution_token=token,
                container_id=identity.container_id, inspect_stdout=text) == identity
            data = json.loads(text)
            data[0]["Config"]["Env"].append("LD_PRELOAD=/tmp/injected.so")
            with pytest.raises(SandboxExecutionPolicyError):
                confirm_sandbox_execution_policy(request=command, execution_token=token,
                    container_id=identity.container_id, inspect_stdout=json.dumps(data))
            # 只变更本地响应副本，真实容器不启动也不改配置。
        finally:
            await cleanup_created_sandbox(request=command, execution_token=token,
                                          expected_container_id=identity.container_id)
            assert await is_sandbox_container_absent(container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 45))
