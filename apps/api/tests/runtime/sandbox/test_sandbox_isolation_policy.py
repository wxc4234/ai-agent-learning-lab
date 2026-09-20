"""隔离策略专项；Docker 实机检查显式启用且不启动容器。"""

import asyncio
import copy
import json
import os
from uuid import uuid4

import pytest

from app.services.runtime.docker.docker_client import inspect_sandbox_container, is_sandbox_container_absent
from app.services.runtime.sandbox.sandbox_cleanup import cleanup_created_sandbox
from app.services.runtime.sandbox.sandbox_creation import create_and_confirm_sandbox
from app.services.runtime.sandbox.sandbox_isolation_policy import (
    SandboxIsolationPolicyError,
    confirm_sandbox_isolation_policy,
)
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, request
from tests.runtime.sandbox.test_sandbox_execution_policy import configuration

EXACT = {
    "Privileged": False, "ReadonlyRootfs": True, "Init": True,
    "AutoRemove": False, "PublishAllPorts": False,
    "NetworkMode": "none", "PidMode": "", "IpcMode": "private", "UTSMode": "",
    "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"],
    "NanoCpus": 500000000, "Memory": 134217728, "MemorySwap": 134217728,
    "PidsLimit": 32, "ShmSize": 8388608,
}
OPTIONAL_LISTS = ("CapAdd", "Devices", "DeviceRequests", "DeviceCgroupRules",
                  "GroupAdd", "Binds", "Mounts", "VolumesFrom")


def fixture():
    data = configuration()
    data[0]["HostConfig"] = copy.deepcopy(EXACT) | {
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        "LogConfig": {"Type": "none"},
        "Tmpfs": {path: "rw,noexec,nosuid,nodev,size=16m,mode=0700,uid=10001,gid=10001"
                  for path in ("/home/agent", "/tmp")},
    }
    data[0]["Mounts"] = []
    return data


def confirm(data=None, **options):
    return confirm_sandbox_isolation_policy(**{
        "request": request(), "execution_token": TOKEN, "container_id": CID,
        "inspect_stdout": json.dumps(fixture() if data is None else data),
    } | options)


def test_success_and_input_unchanged():
    data = fixture()
    original = copy.deepcopy(data)
    assert confirm(data).container_id == CID
    assert data == original


@pytest.mark.parametrize("field,value", [
    (field, value) for field, expected in EXACT.items()
    for value in (None, True, False, 0, 1, "unexpected", [])
    if not (type(value) is type(expected) and value == expected)
])
def test_exact_fields_reject_wrong_type_or_value(field, value):
    data = fixture()
    data[0]["HostConfig"][field] = value
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(data)


@pytest.mark.parametrize("field", [*EXACT, "RestartPolicy", "LogConfig", "Tmpfs"])
def test_required_field_missing(field):
    data = fixture()
    del data[0]["HostConfig"][field]
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(data)


@pytest.mark.parametrize("field", OPTIONAL_LISTS)
@pytest.mark.parametrize("value", [None, [], False, {}, "", ["injected"]])
def test_optional_list_types(field, value):
    data = fixture()
    data[0]["HostConfig"][field] = value
    if value is None or isinstance(value, list) and not value:
        assert confirm(data).container_id == CID
    else:
        with pytest.raises(SandboxIsolationPolicyError):
            confirm(data)


@pytest.mark.parametrize("field", ["NanoCpus", "Memory", "MemorySwap", "PidsLimit", "ShmSize"])
def test_resource_float_and_numeric_string_rejected(field):
    for value in (float(EXACT[field]), str(EXACT[field]), EXACT[field] + 1, -1):
        data = fixture()
        data[0]["HostConfig"][field] = value
        with pytest.raises(SandboxIsolationPolicyError):
            confirm(data)


@pytest.mark.parametrize("location", ["ports", "volumes"])
@pytest.mark.parametrize("value", [None, {}, [], False, "", {"/host": {}}])
def test_optional_mapping_types(location, value):
    data = fixture()
    if location == "ports":
        data[0]["HostConfig"]["PortBindings"] = value
    else:
        data[0]["Config"]["Volumes"] = value
    if value is None or isinstance(value, dict) and not value:
        assert confirm(data).container_id == CID
    else:
        with pytest.raises(SandboxIsolationPolicyError):
            confirm(data)


@pytest.mark.parametrize("change", ["missing", "extra", "exec", "size", "uid"])
def test_tmpfs_exact_policy(change):
    data = fixture()
    mounts = data[0]["HostConfig"]["Tmpfs"]
    if change == "missing":
        del mounts["/tmp"]
    elif change == "extra":
        mounts["/etc"] = mounts["/tmp"]
    else:
        old, new = {"exec": ("noexec", "exec"), "size": ("16m", "64m"),
                    "uid": ("uid=10001", "uid=0")}[change]
        mounts["/tmp"] = mounts["/tmp"].replace(old, new)
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(data)


@pytest.mark.parametrize("mounts", [None, {}, [None],
    [{"Type": "bind", "Destination": "/tmp", "RW": True}],
    [{"Type": "volume", "Destination": "/tmp", "RW": True}],
    [{"Type": "tmpfs", "Destination": "/etc", "RW": True}],
    [{"Type": "tmpfs", "Destination": None, "RW": True}],
    [{"Type": "tmpfs", "Destination": "/tmp", "RW": 1}],
    [{"Type": "tmpfs", "Destination": "/tmp", "RW": True}] * 2])
def test_mount_summary_rejections(mounts):
    data = fixture()
    data[0]["Mounts"] = mounts
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(data)


def test_tmpfs_summary_allowed_but_does_not_replace_declaration():
    data = fixture()
    data[0]["Mounts"] = [{"Type": "tmpfs", "Destination": path, "RW": True}
                         for path in ("/tmp", "/home/agent")]
    assert confirm(data).container_id == CID
    del data[0]["HostConfig"]["Tmpfs"]
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(data)


@pytest.mark.parametrize("field,value", [("HostConfig", None), ("HostConfig", []),
    ("RestartPolicy", []), ("RestartPolicy", {"Name": "always", "MaximumRetryCount": 0}),
    ("RestartPolicy", {"Name": "no", "MaximumRetryCount": False}),
    ("LogConfig", {}), ("LogConfig", {"Type": "json-file"})])
def test_nested_policy_rejections(field, value):
    data = fixture()
    target = data[0] if field == "HostConfig" else data[0]["HostConfig"]
    target[field] = value
    with pytest.raises(SandboxIsolationPolicyError):
        confirm(data)


@pytest.mark.parametrize("change", ["user", "id", "state", "json"])
def test_execution_identity_and_strict_json_still_required(change):
    data = fixture()
    if change == "user":
        data[0]["Config"]["User"] = "0"
    elif change == "id":
        data[0]["Id"] = "c" * 64
    elif change == "state":
        data[0]["State"]["Running"] = True
    with pytest.raises(SandboxIsolationPolicyError) as caught:
        confirm(data, **({"inspect_stdout": "PRIVATE"} if change == "json" else {}))
    assert str(caught.value) == "无法确认 Sandbox 隔离配置符合策略"
    assert caught.value.__suppress_context__


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_ISOLATION_POLICY_DOCKER") != "1", reason="explicit Docker opt-in")
def test_real_created_container_isolation_policy():
    async def scenario():
        command, token = request(), uuid4().hex
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        try:
            text = await inspect_sandbox_container(execution_token=token)
            assert confirm_sandbox_isolation_policy(request=command, execution_token=token,
                container_id=identity.container_id, inspect_stdout=text) == identity
            # 仅篡改响应副本；不为测试创建真实特权或宿主挂载容器。
            data = json.loads(text)
            data[0]["HostConfig"]["Privileged"] = True
            with pytest.raises(SandboxIsolationPolicyError):
                confirm_sandbox_isolation_policy(request=command, execution_token=token,
                    container_id=identity.container_id, inspect_stdout=json.dumps(data))
        finally:
            await cleanup_created_sandbox(request=command, execution_token=token,
                                          expected_container_id=identity.container_id)
            assert await is_sandbox_container_absent(container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 45))
