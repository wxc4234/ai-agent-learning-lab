"""退出结果纯解析及显式Docker验证，不伪造尚未获取的输出。"""

import asyncio
import json
import os
from dataclasses import FrozenInstanceError, asdict
from uuid import uuid4

import pytest

from app.services.runtime import docker_client as client
from app.services.runtime.command_contracts import CommandRequest
from app.services.runtime.sandbox_creation import create_and_confirm_sandbox
from app.services.runtime.sandbox_exit import SandboxExitUnconfirmed, confirm_sandbox_exit
from app.services.runtime.sandbox_start import start_and_confirm_sandbox
from app.services.runtime.sandbox_stop import stop_and_confirm_sandbox
from tests.runtime.test_sandbox_creation import CID, TOKEN, request
from tests.runtime.test_sandbox_stop import data


def payload(code=0, oom=False, error=""):
    value = data("exited")
    value[0]["State"].update(ExitCode=code, OOMKilled=oom, Error=error)
    return value


def confirm(value=None, **options):
    return confirm_sandbox_exit(**{
        "request": request(), "execution_token": TOKEN, "container_id": CID,
        "inspect_stdout": json.dumps(payload() if value is None else value),
    } | options)


@pytest.mark.parametrize("code", [0, 1, 7, 126, 127, 137, 143, 255])
@pytest.mark.parametrize("oom,error", [(False, ""), (True, ""), (False, "PRIVATE /path"), (True, "PRIVATE")])
def test_exit_facts_and_success(code, oom, error):
    value = payload(code, oom, error)
    original = json.dumps(value)
    result = confirm(value)
    assert result.identity.container_id == CID
    assert result.exit_code == code
    assert result.oom_killed is oom
    assert result.daemon_error is bool(error)
    assert result.succeeded is (code == 0 and not oom and not error)
    assert "PRIVATE" not in repr(result) and "PRIVATE" not in json.dumps(asdict(result))
    assert json.dumps(value) == original
    assert not hasattr(result, "stdout") and not hasattr(result, "duration_ms")
    with pytest.raises(FrozenInstanceError):
        result.exit_code = 0


@pytest.mark.parametrize("field,value", [
    ("ExitCode", None), ("ExitCode", True), ("ExitCode", False), ("ExitCode", -9),
    ("ExitCode", 256), ("ExitCode", 0.0), ("ExitCode", "0"),
    ("OOMKilled", None), ("OOMKilled", 0), ("OOMKilled", 1), ("OOMKilled", "false"),
    ("Error", None), ("Error", False), ("Error", []),
])
def test_wrong_result_types_or_range(field, value):
    result = payload()
    result[0]["State"][field] = value
    with pytest.raises(SandboxExitUnconfirmed, match="^Sandbox 退出结果未确认$"):
        confirm(result)


@pytest.mark.parametrize("field", ["ExitCode", "OOMKilled", "Error"])
def test_missing_result_field_rejected(field):
    value = payload()
    del value[0]["State"][field]
    with pytest.raises(SandboxExitUnconfirmed):
        confirm(value)


@pytest.mark.parametrize("status", ["created", "running", "paused", "dead", "restarting", "removing"])
def test_not_exited_even_if_exit_code_zero(status):
    value = payload()
    value[0]["State"].update(data(status)[0]["State"])
    with pytest.raises(SandboxExitUnconfirmed):
        confirm(value)


@pytest.mark.parametrize("field,value", [("Pid", 1), ("Pid", False), ("Running", True),
    ("Running", 0), ("Paused", True), ("Restarting", True), ("Dead", True)])
def test_exit_requires_consistent_stopped_evidence(field, value):
    result = payload()
    result[0]["State"][field] = value
    with pytest.raises(SandboxExitUnconfirmed):
        confirm(result)


@pytest.mark.parametrize("text", [None, "PRIVATE", "[]", "[{},{}]", "x" * 65537,
    '[{"State":{"ExitCode":0,"ExitCode":1}}]', '[{"x":NaN}]', "[" * 2000])
def test_strict_response_failures_safe(text):
    with pytest.raises(SandboxExitUnconfirmed) as caught:
        confirm(inspect_stdout=text)
    assert str(caught.value) == "Sandbox 退出结果未确认"
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("field", ["id", "name", "label", "image"])
def test_identity_mismatch_rejected(field):
    value = payload()
    if field == "id":
        value[0]["Id"] = "c" * 64
    elif field == "name":
        value[0]["Name"] = "/other"
    elif field == "label":
        value[0]["Config"]["Labels"]["ai-agent-learning-lab.execution"] = "c" * 32
    else:
        value[0]["Config"]["Image"] = "other"
    with pytest.raises(SandboxExitUnconfirmed):
        confirm(value)


def test_bad_request_stays_preflight_error():
    command = request()
    command.argv.clear()
    with pytest.raises(ValueError) as caught:
        confirm(request=command)
    assert not isinstance(caught.value, SandboxExitUnconfirmed)


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_EXIT_DOCKER") != "1", reason="explicit Docker opt-in")
@pytest.mark.parametrize("code", [0, 7, 137])
def test_real_exit_codes(code):
    async def scenario():
        token = uuid4().hex
        command = CommandRequest(argv=["/usr/local/bin/python", "-c", f"raise SystemExit({code})"])
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        arguments = {"request": command, "execution_token": token, "expected_container_id": identity.container_id}
        try:
            await start_and_confirm_sandbox(**arguments)
            # 仅测试等待退出，生产模块仍为纯解析，不引入无界轮询。
            async with asyncio.timeout(5):
                while True:
                    text = await client.inspect_sandbox_container_by_id(container_id=identity.container_id)
                    if json.loads(text)[0]["State"]["Status"] == "exited":
                        break
                    await asyncio.sleep(0.05)
            result = confirm_sandbox_exit(request=command, execution_token=token,
                                          container_id=identity.container_id, inspect_stdout=text)
            assert result.exit_code == code and result.succeeded is (code == 0)
            assert not result.oom_killed and not result.daemon_error
        finally:
            assert (await stop_and_confirm_sandbox(**arguments)).stopped
            await client.remove_sandbox_container(container_id=identity.container_id)
            assert await client.is_sandbox_container_absent(container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 45))
